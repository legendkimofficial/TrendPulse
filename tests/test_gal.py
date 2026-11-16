"""Unit tests for the GDELT GAL submission: replay contract, 7-day decaying
ranking, 72-hour surge, pruning/re-appearance, DGIM tracking and URL/country
normalization.  Run: python -m unittest tests.test_gal -v

Every engine test builds a small synthetic GalArticle stream (marked SYNTHETIC
in the submission report) with exact observation times, so expected values are
hand-computed direct summations, independent of the incremental score.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.gal import GalArticle, parse_iso
from trendpulse.gal_engine import GalConfig, GalEngine
from trendpulse.text import tokenize

H = 3600
DAY = 24 * H


def art(i, obs_iso, title, outlet="Test Outlet", domain="test.com", country="US",
        url=None, tokens=None):
    return GalArticle(i=i, obs=parse_iso(obs_iso), obs_iso=obs_iso,
                      url=url or f"https://{domain}/a/{i}", curl=url or f"https://{domain}/a/{i}",
                      domain=domain, domain_std=domain, outlet=outlet, country=country,
                      title=title, desc="", adate=None,
                      tokens=tokens if tokens is not None else tuple(tokenize(title)))


def direct_score(mentions, t, hl=48 * H):
    return sum(2.0 ** (-(t - m) / hl) for m in mentions)


class RankingMathTest(unittest.TestCase):
    """7-day window + 48h half-life + document frequency, vs direct summation."""

    def setUp(self):
        # 2026-11-02T00:00Z .. one article per observation time
        self.arts = [
            art(0, "2026-11-02T00:00:00Z", "Alpha Beta"),      # t-7d exactly (edge)
            art(1, "2026-11-02T00:00:01Z", "Alpha"),           # 1s inside the window
            art(2, "2026-11-05T00:00:00Z", "alpha alpha alpha"),  # dup keyword in one title
            art(3, "2026-11-08T12:00:00Z", "Alpha Gamma"),
            art(4, "2026-11-09T00:00:00Z", "Alpha"),           # at t (included)
        ]
        self.t = parse_iso("2026-11-09T00:00:00Z")
        self.eng = GalEngine(self.arts, GalConfig(k=5))
        while not self.eng.finished:
            self.eng.step()

    def test_window_boundary_excludes_exactly_7d(self):
        # exactly t-7d is EXPIRED, 1s later is kept, t is included
        mentions = [parse_iso("2026-11-02T00:00:01Z"), parse_iso("2026-11-05T00:00:00Z"),
                    parse_iso("2026-11-08T12:00:00Z"), parse_iso("2026-11-09T00:00:00Z")]
        self.eng._sync(self.eng.kw["alpha"], self.t)
        exp = direct_score(mentions, self.t)
        self.assertAlmostEqual(self.eng.kw["alpha"].S, exp, places=9)
        # without the boundary rule the 2026-11-02T00:00:00Z article would add ~1/8
        self.assertLess(self.eng.kw["alpha"].S, exp + 0.13)

    def test_document_frequency_and_same_timestamp(self):
        # same observation time, two articles, each counts once
        arts = [art(0, "2026-11-08T00:00:00Z", "Alpha One"),
                art(1, "2026-11-08T00:00:00Z", "Alpha Two")]
        eng = GalEngine(arts, GalConfig(k=5))
        eng.step()
        t = parse_iso("2026-11-08T00:00:00Z")
        eng._sync(eng.kw["alpha"], t)
        self.assertAlmostEqual(eng.kw["alpha"].S, 2.0, places=9)

    def test_duplicate_keyword_in_title_counts_once(self):
        st = self.eng.kw["alpha"]
        # "alpha alpha alpha" (art 2) contributed exactly 1
        self.eng._sync(st, self.t)
        mentions = [parse_iso("2026-11-02T00:00:01Z"), parse_iso("2026-11-05T00:00:00Z"),
                    parse_iso("2026-11-08T12:00:00Z"), parse_iso("2026-11-09T00:00:00Z")]
        self.assertAlmostEqual(st.S, direct_score(mentions, self.t), places=9)

    def test_long_gap_decay_only(self):
        # long observation gap: score decays, no article appears
        self.eng.advance_clock("2026-11-10T00:00:00Z")
        t2 = parse_iso("2026-11-10T00:00:00Z")
        mentions = [m for m in [parse_iso("2026-11-05T00:00:00Z"), parse_iso("2026-11-08T12:00:00Z"),
                                parse_iso("2026-11-09T00:00:00Z")]]
        exp = direct_score(mentions, t2)
        self.assertAlmostEqual(self.eng.kw["alpha"].S, exp, places=9)
        self.assertAlmostEqual(self.eng.score_at("alpha", t2), exp, places=9)

    def test_clock_advance_expires_and_decays(self):
        # one keyword, clock advances past 7d with no new articles -> expired
        arts = [art(0, "2026-11-02T00:00:00Z", "Solo")]
        eng = GalEngine(arts, GalConfig(k=5))
        eng.step()
        eng.advance_clock("2026-11-09T00:00:01Z")
        self.assertNotIn("solo", eng.kw)
        self.assertEqual(eng.ranking_rows(), [])   # nothing ranks anymore

    def test_tie_break_keyword_ascending(self):
        arts = [art(0, "2026-11-08T00:00:00Z", "Zeta"),
                art(1, "2026-11-08T00:00:00Z", "Alpha"),
                art(2, "2026-11-08T00:00:00Z", "Mid")]
        eng = GalEngine(arts, GalConfig(k=3))
        eng.step()
        self.assertEqual([r["keyword"] for r in eng.ranking_rows()], ["alpha", "mid", "zeta"])

    def test_incremental_matches_direct_for_all_keywords(self):
        for w, st in self.eng.kw.items():
            self.assertAlmostEqual(st.S, self.eng.score_at(w, self.t), places=9,
                                   msg=f"incremental vs direct mismatch for {w}")

    def test_deterministic_replay(self):
        digests = []
        for _ in range(2):
            eng = GalEngine(self.arts, GalConfig(k=5))
            eng.replay_all()
            digests.append(eng.snapshot_digest())
            order = [a.i for a in eng.articles]
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(order, sorted(order))


class PruningTest(unittest.TestCase):
    def _stream(self):
        # alpha: three mentions on 11-02..11-03 (S=3), then silence; daily zeta
        # articles keep snapshots flowing so alpha decays below theta=0.5
        # (3 * 2^(-144h/48h) = 0.375) and is dropped before it reappears on 11-09.
        arts = [art(0, "2026-11-02T00:00:00Z", "Alpha Alpha"),
                art(1, "2026-11-02T06:00:00Z", "Alpha"),
                art(2, "2026-11-03T00:00:00Z", "Alpha")]
        i = 3
        for day in range(3, 10):                      # 2026-11-03 .. 2026-11-09
            arts.append(art(i, f"2026-11-{day:02d}T00:00:00Z", "Zeta Zeta"))
            i += 1
        arts.append(art(i, "2026-11-09T06:00:00Z", "Alpha Comeback"))   # reappears
        return sorted(arts, key=lambda a: (a.obs, a.curl))

    def test_prune_rule_and_reappearance(self):
        cfg = GalConfig(k=5, threshold=0.5)
        arts = self._stream()
        exact = GalEngine(arts, GalConfig(k=5))
        pruned = GalEngine(arts, cfg)
        while not exact.finished:
            exact.step()
            pruned.step()
        t = exact.now_t
        # alpha was dropped at some snapshot with score < 0.5
        self.assertGreater(pruned.dropped_events, 0)
        # reappearance starts from 1; past contributions are NOT subtracted again:
        # pruned = exact - residual_past, i.e. 0 <= exact - pruned < 1
        e, p = exact.kw["alpha"].S, pruned.kw["alpha"].S
        self.assertLessEqual(p, e + 1e-9)
        self.assertGreaterEqual(e - p, 0.0)
        self.assertLess(e - p, 1.0)          # lost mass < threshold(0.5)+slack, definitely < 1
        # and zeta (never pruned) is identical in both
        self.assertAlmostEqual(exact.kw["zeta"].S, pruned.kw["zeta"].S, places=9)

    def test_pruned_never_exceeds_exact_any_snapshot(self):
        arts = self._stream()
        exact = GalEngine(arts, GalConfig(k=5))
        pruned = GalEngine(arts, GalConfig(k=5, threshold=0.3))
        while not exact.finished:
            exact.step()
            pruned.step()
            for w in set(exact.kw) | set(pruned.kw):
                p_s = pruned.kw[w].S if w in pruned.kw else 0.0
                e_s = exact.kw[w].S if w in exact.kw else 0.0
                self.assertLessEqual(p_s, e_s + 1e-9,
                                     f"pruned exceeded exact at {w}")


class SurgeTest(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(k=5, min_articles_per_interval=3, min_keyword_articles=2,
                    min_keyword_outlets=1)
        base.update(kw)
        return GalConfig(**base)

    def test_diff_pp_and_zero_previous(self):
        # t = 2026-11-09T12:00Z; coverage article at exactly t-144h (11-03T12:00Z,
        # excluded from the previous interval but confirms observability);
        # previous (t-144h, t-72h] = 11-03T12..11-06T12: 4 fillers, 0 surge;
        # recent (t-72h, t] = 11-06T12..11-09T12: 2 surge + 2 fillers.
        arts = [art(0, "2026-11-03T12:00:00Z", "Boundary Cover"),
                art(1, "2026-11-04T00:00:00Z", "Filler A"),
                art(2, "2026-11-04T12:00:00Z", "Filler B"),
                art(3, "2026-11-05T00:00:00Z", "Filler C"),
                art(4, "2026-11-05T12:00:00Z", "Filler D"),
                art(5, "2026-11-08T00:00:00Z", "Surge One", outlet="O1"),
                art(6, "2026-11-08T06:00:00Z", "Surge Two", outlet="O2"),
                art(7, "2026-11-09T00:00:00Z", "Filler E"),
                art(8, "2026-11-09T12:00:00Z", "Filler F")]
        eng = GalEngine(arts, self._cfg())
        eng.replay_all()
        snap = eng.snapshots[-1]
        self.assertEqual(snap["burst_status"], "ok")
        row = next(r for r in snap["burst"] if r["kw"] == "surge")
        self.assertEqual(row["num_recent"], 2)
        self.assertEqual(row["den_recent"], 4)
        self.assertEqual(row["num_prev"], 0)
        self.assertEqual(row["den_prev"], 4)
        self.assertAlmostEqual(row["diff_pp"], 100 * (2 / 4 - 0 / 4), places=6)

    def test_deferred_when_observability_short(self):
        # stream starts inside the previous interval -> deferred(observability)
        arts = [art(0, "2026-11-08T12:00:00Z", "Surge"), art(1, "2026-11-08T13:00:00Z", "Surge"),
                art(2, "2026-11-08T14:00:00Z", "Other"), art(3, "2026-11-08T15:00:00Z", "Other"),
                art(4, "2026-11-08T16:00:00Z", "Other"), art(5, "2026-11-08T17:00:00Z", "Other")]
        eng = GalEngine(arts, self._cfg())
        eng.replay_all()
        snap = eng.snapshots[-1]
        self.assertEqual(snap["burst_status"], "deferred")
        self.assertEqual(snap["burst_reason"], "observability")

    def test_deferred_denominator_and_keyword_support(self):
        base = [art(0, "2026-11-02T00:00:00Z", "Boundary Cover")]
        base += [art(i, "2026-11-03T00:00:00Z", f"Filler Number {i}") for i in range(1, 7)]
        base += [art(7, "2026-11-07T00:00:00Z", "Filler Recent"),
                 art(8, "2026-11-08T00:00:00Z", "Surge Alpha"), art(9, "2026-11-08T01:00:00Z", "Surge Beta")]
        # t = 2026-11-08T01:00Z; prev (11-02T01, 11-05T01]: 6 fillers; recent: 2 surge
        eng = GalEngine(base, self._cfg())
        eng.replay_all()
        self.assertEqual(eng.snapshots[-1]["burst_status"], "ok")
        # min_keyword_articles = 3: only 2 surge articles -> keyword_support
        cfg2 = self._cfg(min_keyword_articles=3)
        eng2 = GalEngine(base, cfg2)
        eng2.replay_all()
        d = eng2.keyword_detail("surge")["burst"]
        self.assertEqual(d["status"], "deferred")
        self.assertIn("keyword_support(recent_articles)", d["reason"])
        # min outlets = 2 but both surge articles from one outlet -> outlets deferral
        cfg3 = self._cfg(min_keyword_outlets=2)
        eng3 = GalEngine(base, cfg3)
        eng3.replay_all()
        d3 = eng3.keyword_detail("surge")["burst"]
        self.assertEqual(d3["status"], "deferred")
        self.assertIn("outlets", d3["reason"])
        # denominator below minimum -> deferred(denominator)
        cfg4 = self._cfg(min_articles_per_interval=10)
        eng4 = GalEngine(base, cfg4)
        eng4.replay_all()
        self.assertEqual(eng4.snapshots[-1]["burst_status"], "deferred")
        self.assertEqual(eng4.snapshots[-1]["burst_reason"], "denominator")

    def test_collection_gap_defers(self):
        arts = [art(0, "2026-11-02T00:00:00Z", "Boundary Cover"),
                art(1, "2026-11-05T00:00:00Z", "Filler A"), art(2, "2026-11-05T01:00:00Z", "Filler B"),
                art(3, "2026-11-05T02:00:00Z", "Filler C"),
                art(4, "2026-11-08T00:00:00Z", "Surge A"), art(5, "2026-11-08T01:00:00Z", "Surge B"),
                art(6, "2026-11-08T02:00:00Z", "Filler D")]
        gaps = [{"start": "2026-11-06T00:00:00Z", "end": "2026-11-07T00:00:00Z",
                 "start_ts": parse_iso("2026-11-06T00:00:00Z"),
                 "end_ts": parse_iso("2026-11-07T00:00:00Z")}]
        eng = GalEngine(arts, self._cfg(gaps=gaps))
        eng.replay_all()
        snap = eng.snapshots[-1]
        self.assertEqual(snap["burst_status"], "deferred")
        self.assertEqual(snap["burst_reason"], "collection_gap")


class EntryTest(unittest.TestCase):
    def test_first_reentry_flags(self):
        a0 = "2026-11-08T00:00:00Z"
        a1 = "2026-11-08T01:00:00Z"
        a2 = "2026-11-08T02:00:00Z"
        arts = [art(0, a0, "Alpha"), art(1, a0, "Beta"),
                art(2, a1, "Beta"),
                art(3, a2, "Alpha")]
        eng = GalEngine(arts, GalConfig(k=1))             # k=1: alpha/beta alternate
        eng.replay_all()
        s0, s1, s2 = eng.snapshots
        self.assertEqual(s0["top"][0]["e"], "initial")
        self.assertEqual(s0["top"][0]["kw"], "alpha")     # tie -> keyword ascending
        self.assertEqual(s1["top"][0]["e"], "first")      # beta enters (alpha out)
        self.assertEqual(s2["top"][0]["e"], "re-entry")   # alpha back after absence


class DgimTest(unittest.TestCase):
    def _stream(self, n=60, mention_at=()):
        arts = []
        for i in range(n):
            title = "Surge Mention" if i in mention_at else "Plain Story"
            arts.append(art(i, f"2026-11-02T{i % 24:02d}:{i % 60:02d}:00Z"
                             if False else _seq_iso(i), title))
        return sorted(arts, key=lambda a: (a.obs, a.curl))

    def test_zero_mentions_and_partial_window(self):
        arts = self._stream(30)
        eng = GalEngine(arts, GalConfig(dgim_n=1000, dgim_r=2))
        eng.replay_all()
        eng.track("surge")
        # no mention since tracking start (which is after the whole stream): zero
        d = eng.dgim_info("surge")
        self.assertEqual(d["exact"], 0)
        self.assertEqual(d["estimate"], 0.0)
        # track before anything, then step small amounts: partial observed range
        eng2 = GalEngine(arts, GalConfig(dgim_n=1000, dgim_r=2))
        eng2.track("surge")            # start_i = 0
        eng2.step(10)
        d2 = eng2.dgim_info("surge")
        self.assertLess(d2["observed"], 1000)
        self.assertTrue(d2["partial"])
        truth = sum(1 for a in arts[:10] if "surge" in a.tokens)
        self.assertEqual(d2["exact"], truth)

    def test_boundary_expiry_and_merges(self):
        mentions = {0, 1, 2, 3, 10, 11, 20, 45}
        arts = self._stream(60, mentions)
        eng = GalEngine(arts, GalConfig(dgim_n=30, dgim_r=2))
        eng.track("surge")
        eng.replay_all()
        d = eng.dgim_info("surge")           # window = last 30 articles = idx 30..59
        truth = sum(1 for i in mentions if 29 < i <= 59)
        self.assertEqual(d["exact"], truth)
        self.assertLessEqual(abs(d["estimate"] - truth), 1 + 1e-9)   # r=2 -> |err|<=1
        # invariants: sizes powers of two, <= r per size, timestamps decrease
        sizes = [s for _, s in eng.trackers["surge"].dgim._buckets]
        self.assertEqual(sizes, sorted(sizes))
        for s in set(sizes):
            self.assertEqual(s & (s - 1), 0)
            self.assertLessEqual(sizes.count(s), 2)

    def test_multiple_n_and_patterns(self):
        import random
        rng = random.Random(4)
        bits = [rng.random() < p for p in ([0.02] * 300 + [0.4] * 300)]  # intermittent then frequent
        arts = self._stream(len(bits), {i for i, b in enumerate(bits) if b})
        for N in (10, 50, 200):
            eng = GalEngine(arts, GalConfig(dgim_n=N, dgim_r=2))
            eng.track("surge")
            eng.replay_all()
            t = len(bits) - 1
            truth = sum(bits[max(0, t - N + 1): t + 1])
            d = eng.dgim_info("surge")
            self.assertEqual(d["exact"], truth)
            if truth:
                self.assertLessEqual(abs(d["estimate"] - truth) / truth, 0.5 + 1e-9)
            else:
                self.assertEqual(d["estimate"], 0.0)

    def test_related_respects_as_of(self):
        arts = self._stream(10, {2, 5, 8})
        eng = GalEngine(arts, GalConfig())
        eng.replay_all()
        self.assertEqual([r["id"] for r in eng.related("surge", 10, as_of=5)], [5, 2])
        self.assertEqual([r["id"] for r in eng.related("surge", 10, as_of=4)], [2])
        # no exposure of future articles beyond as_of
        self.assertTrue(all(r["id"] <= 5 for r in eng.related("surge", 10, as_of=5)))


def _seq_iso(i):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 2, tzinfo=timezone.utc)
    return (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")


class NormalizationTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import fetch_gdelt_gal as fg
        self.fg = fg

    def test_canonical_url(self):
        cu = self.fg.canonical_url
        self.assertEqual(cu("HTTPS://Example.COM:443/a?b=1#frag"), "https://example.com/a?b=1")
        self.assertEqual(cu("https://x.com/p?utm_source=rss&id=5"), "https://x.com/p?id=5")
        self.assertEqual(cu("https://x.com/p?ref=/home?ref=/watch&id=9"), "https://x.com/p?id=9")
        self.assertEqual(cu("https://x.com/p?fbclid=abc"), "https://x.com/p")
        self.assertEqual(cu("https://x.com:8443/p"), "https://x.com:8443/p")
        # article-identifying queries survive, path untouched
        self.assertEqual(cu("https://x.com/story.jsp?id=1&mode=print"), "https://x.com/story.jsp?id=1&mode=print")
        self.assertEqual(cu("ftp://x.com/a"), None)
        self.assertEqual(cu("https://m.example.com/story/123"), "https://m.example.com/story/123")

    def test_country_of(self):
        ov = self.fg.load_overrides()
        c = lambda d: self.fg.country_of(d, ov)
        self.assertEqual(c("allafrica.com"), None)          # gTLD, unmapped -> unknown
        self.assertEqual(c("northsomersettimes.co.uk"), "GB")
        self.assertEqual(c("wsls.com"), None)
        self.assertEqual(c("theguardian.ng"), "NG")
        self.assertEqual(c("news.com.au"), "AU")
        self.assertEqual(c("nytimes.com"), "US")            # override
        self.assertEqual(c("bbc.co.uk"), "GB")
        self.assertEqual(c("straitstimes.com"), "SG")
        self.assertEqual(c("example.io"), None)             # generic ccTLD -> unknown
        self.assertEqual(c("koreatimes.co.kr"), "KR")

    def test_registrable_domain(self):
        self.assertEqual(self.fg.registrable_domain("www.example.co.uk"), "example.co.uk")
        self.assertEqual(self.fg.registrable_domain("m.example.com"), "m.example.com")


if __name__ == "__main__":
    unittest.main()


class GalAppTest(unittest.TestCase):
    """Flask test-client checks for app_gdelt (no network)."""

    @classmethod
    def setUpClass(cls):
        import app_gdelt
        arts = [art(0, "2026-11-02T00:00:00Z", "Boundary Cover")]
        arts += [art(i, "2026-11-03T00:00:00Z", f"Filler Number {i}") for i in range(1, 7)]
        arts += [art(7, "2026-11-07T00:00:00Z", "Filler Recent"),
                 art(8, "2026-11-08T00:00:00Z", "Surge Alpha"),
                 art(9, "2026-11-08T01:00:00Z", "Surge Beta"),
                 art(10, "2026-11-08T02:00:00Z", "Surge Gamma")]
        cls.arts = arts
        cls.app = app_gdelt.create_gal_app(arts, start_batches=99)  # full replay

    def test_state_shape_and_keyword_flow(self):
        with self.app.test_client() as c:
            st = c.get("/api/state").get_json()
            self.assertIn("ranking", st)
            self.assertTrue(st["ranking"])
            kw = st["ranking"][0]["keyword"]
            self.assertIsNotNone(st["clock"]["t_iso"])
            # keyword detail uses the same reference time as the ranking
            d = c.get(f"/api/keyword/{kw}").get_json()
            self.assertEqual(d["as_of"], st["clock"]["t_iso"])
            self.assertGreaterEqual(d["mentions_7d"], 1)
            self.assertIn("num_recent", d["burst"])

    def test_no_future_articles_beyond_replay_position(self):
        import app_gdelt
        app2 = app_gdelt.create_gal_app(self.arts, start_batches=2)  # partial replay
        with app2.test_client() as c:
            st = c.get("/api/state").get_json()
            pos = st["clock"]["pos"]
            d = c.get("/api/keyword/surge").get_json()
            self.assertTrue(all(r["id"] < pos for r in d["related"]))
            self.assertEqual(c.get("/api/info").get_json()["articles"], len(self.arts))

    def test_ask_offline_mode_uses_tools(self):
        import os
        old = os.environ.pop("UPSTAGE_API_KEY", None)
        try:
            with self.app.test_client() as c:
                r = c.post("/api/ask", json={"question": "surge 에 대해 설명해줘",
                                             "keyword": "surge"}).get_json()
                self.assertEqual(r["mode"], "offline")
                tools = {t["tool"] for t in r["trace"]}
                self.assertIn("get_topic_stats", tools)
                self.assertIn("search_related_news", tools)
                self.assertIn("Surge", r["answer"])
        finally:
            if old is not None:
                os.environ["UPSTAGE_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
