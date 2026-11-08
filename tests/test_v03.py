"""v0.3 features: phrases, absorption, per-keyword error bounds, rank uncertainty,
burst detection, per-day decay, new config fields."""
import random
import unittest

from app import create_app
from trendpulse.data import Article
from trendpulse.decaying import DecayingWindow
from trendpulse.engine import EngineConfig, TrendEngine
from trendpulse.ranking import absorb, burst_scores, outside_candidates, uncertainty
from trendpulse.text import phrases, tokenize
from tests.test_algorithms import random_docs


class PhraseTest(unittest.TestCase):
    def test_adjacent_pairs(self):
        self.assertEqual(phrases("Donald Trump Praises Supreme Court"),
                         ["donald trump", "trump praises", "praises supreme", "supreme court"])

    def test_no_pair_across_punctuation_or_removed_word(self):
        self.assertEqual(phrases("Trump: White House Says No"), ["white house"])
        self.assertEqual(phrases("Obama And Biden"), [])            # "and" breaks the pair

    def test_new_can_start_a_phrase(self):
        self.assertEqual(phrases("New York Times Reporter"), ["new york"])
        self.assertEqual(phrases("Brand New Car"), ["new car"])     # "new" only starts phrases

    def test_acronyms_and_possessive(self):
        self.assertEqual(phrases("Trump's Wall"), ["trump wall"])
        self.assertIn("u.s. troops", phrases("U.S. Troops Leave"))


class AbsorbTest(unittest.TestCase):
    def test_word_absorbed_only_when_phrase_dominates(self):
        cands = [("trump", 70.0), ("donald", 30.0), ("donald trump", 28.0), ("white", 20.0),
                 ("white house", 12.0), ("house", 11.0)]
        out = [w for w, _ in absorb(cands, 10, 0.8)]
        # donald (30) and house (11) are mostly used inside their phrases -> hidden;
        # trump (70) and white (20) have other uses -> kept
        self.assertEqual(out, ["trump", "donald trump", "white", "white house"])

    def test_k_applied_after_absorption(self):
        cands = [("a", 10.0), ("a b", 9.0), ("c", 8.0)]
        self.assertEqual(absorb(cands, 2, 0.8), [("a b", 9.0), ("c", 8.0)])


def brute(docs, c, theta=None, steps=None):
    ref = {}
    for i, d in enumerate(docs):
        k = 1 if steps is None else steps[i]
        for w in list(ref):
            ref[w] *= (1 - c) ** k
        for w in d:
            ref[w] = ref.get(w, 0.0) + 1.0
        if theta is not None:
            for w in [w for w, s in ref.items() if s < theta]:
                del ref[w]
    return ref


class ErrorBoundTest(unittest.TestCase):
    def test_key_error_bounds_every_keyword(self):
        for seed in range(4):
            docs = random_docs(3000, vocab=200, seed=seed)
            c, theta = 0.02, 0.5
            exact, pruned = DecayingWindow(c), DecayingWindow(c, theta, track_error=True)
            for i, d in enumerate(docs):
                exact.add(d)
                pruned.add(d)
                if i % 97 == 0:
                    for w, s in exact.items():
                        gap = s - pruned.score(w)
                        self.assertGreaterEqual(gap, -1e-9)
                        self.assertLess(gap, pruned.key_error(w) + 1e-9, (seed, i, w))

    def test_long_lived_keywords_become_exact(self):
        dw = DecayingWindow(0.01, 0.5, track_error=True)
        for _ in range(2000):
            dw.add(["steady"])
        self.assertLess(dw.key_error("steady"), 1e-6)

    def test_uncertainty_flags_never_miss_a_swap(self):
        """If a pair is not flagged, the exact ranking must keep its order (the guarantee)."""
        checked = 0
        for seed in range(6):
            docs = random_docs(2500, vocab=150, seed=10 + seed)
            c, theta, k = 0.02, 0.7, 10
            exact, pruned = DecayingWindow(c), DecayingWindow(c, theta, track_error=True)
            for i, d in enumerate(docs):
                exact.add(d)
                pruned.add(d)
                if i < 200 or i % 50:
                    continue
                top = pruned.top_k(k)
                errs = [pruned.key_error(w) for w, _ in top]
                outside, complete = outside_candidates(pruned, top, extra=15)
                flags = uncertainty(top, errs, outside, pruned.error_bound(), complete)
                for j in range(len(top) - 1):
                    if not flags[j]["swap_below"]:
                        checked += 1
                        self.assertGreaterEqual(exact.score(top[j][0]) + 1e-9, exact.score(top[j + 1][0]))
                if not flags[-1]["swap_below"]:
                    inside = {w for w, _ in top}
                    best_out = max((s for w, s in exact.items() if w not in inside), default=0.0)
                    self.assertGreaterEqual(exact.score(top[-1][0]) + 1e-9, best_out)
        self.assertGreater(checked, 100)


class BurstTest(unittest.TestCase):
    def test_new_topic_bursts_and_steady_topic_does_not(self):
        rng = random.Random(3)
        long_w, short_w = DecayingWindow(0.002, 0.5), DecayingWindow(0.014, 0.5)
        for i in range(4000):
            doc = ["steady"] if rng.random() < 0.3 else ["noise%d" % rng.randrange(500)]
            if i > 3900 and rng.random() < 0.5:
                doc.append("surge")
            long_w.add(doc)
            short_w.add(doc)
        rows = burst_scores(short_w, long_w, 3.0, 5)
        self.assertEqual(rows[0]["keyword"], "surge")
        self.assertNotIn("steady", [r["keyword"] for r in rows])


def dated_articles():
    heads = []
    for day in range(60):
        for j in range(3 if day < 40 else 1):        # density drops after day 40
            heads.append((day, f"Festival Crowds Gather {j}" if j % 2 == 0 else "Senate Budget Vote"))
        if day >= 50:
            heads.append((day, "George Floyd Protests Grow"))
            heads.append((day, "Rally For George Floyd"))
    out = []
    for i, (day, h) in enumerate(heads):
        out.append(Article(idx=i, date=f"2020-{1 + day // 28:02d}-{1 + day % 28:02d}", headline=h,
                           description="", category="T", link=f"x/{i}", tokens=tuple(tokenize(h)),
                           phrases=tuple(phrases(h))))
    return out


class EngineV03Test(unittest.TestCase):
    def test_day_decay_reacts_in_sparse_period(self):
        arts = dated_articles()
        day = TrendEngine(arts, EngineConfig(decay_unit="day", half_life_days=3, k=3, snapshot_every=5,
                                             warmup_factor=0))
        day.step(len(arts))
        top = [r["keyword"] for r in day.state()["ranking"]]
        self.assertIn("george floyd", top)
        self.assertEqual(day.clock()["unit_label"], "일")

    def test_phrase_mode_ranks_and_tracks_phrases(self):
        arts = dated_articles()
        e = TrendEngine(arts, EngineConfig(decay_unit="article", c=0.05, k=4, snapshot_every=5, warmup_factor=0))
        e.step(len(arts))
        st = e.state()
        words = [r["keyword"] for r in st["ranking"]]
        self.assertIn("george floyd", words)
        self.assertNotIn("george", words)             # absorbed by the phrase
        self.assertTrue(all("may_swap_above" in r and "error_bound" in r for r in st["ranking"]))
        self.assertIn("burst", st)

    def test_config_validation_v03(self):
        for bad in ({"decay_unit": "week"}, {"phrases": "yes"}, {"half_life_days": 0},
                    {"burst_ratio": 1}, {"absorb_ratio": 0.2}):
            with self.assertRaises(ValueError, msg=str(bad)):
                EngineConfig(**bad).validate()

    def test_api_accepts_new_fields(self):
        app = create_app(dated_articles(), start_date=None)
        client = app.test_client()
        ok = client.post("/api/reset", json={"phrases": "false", "decay_unit": "day", "half_life_days": 5,
                                             "k": 3, "snapshot_every": 5})
        self.assertEqual(ok.status_code, 200)
        cfg = ok.get_json()["config"]
        self.assertEqual((cfg["phrases"], cfg["decay_unit"]), (False, "day"))
        self.assertEqual(client.post("/api/reset", json={"phrases": "maybe"}).status_code, 400)
        self.assertEqual(client.post("/api/reset", json={"decay_unit": "hour"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()


class WindowCountTest(unittest.TestCase):
    def test_recent_window_counts_days_in_day_mode(self):
        arts = dated_articles()
        e = TrendEngine(arts, EngineConfig(decay_unit="day", half_life_days=2, k=3, snapshot_every=5,
                                           warmup_factor=0))
        e.step(len(arts))
        d = e.keyword_detail("george floyd")
        span = round(1 / e.config.decay_c)
        self.assertEqual(d["window_label"], f"최근 {span}일")
        today = e._day[e.now]
        expected = sum(1 for i in e.postings["george floyd"] if e._day[i] > today - span)
        self.assertEqual(d["mentions_in_effective_window"], expected)
