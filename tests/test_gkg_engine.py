"""gdelt-gkg: TimeDecayWindow, GkgEngine and the sqlite evidence store."""
import math
import os
import random
import shutil
import sqlite3
import tempfile
import unittest

from trendpulse.gkg import GkgArticle
from trendpulse.gkg_engine import GkgEngine, GkgEngineConfig, TimeDecayWindow
from trendpulse.gkg_evidence import EvidenceStore

SEED = 20241001
T0 = 1727740800          # 2024-10-01T00:00:00Z
SLOT = 900               # 15 minutes


def direct(mentions, key, t, h_hours):
    h = h_hours * 3600.0
    return sum(2.0 ** (-(t - ts) / h) for ts, keys in mentions if key in keys and ts <= t)


def random_stream(rng, n_slots=200, per_slot=(0, 12), vocab=40, step=SLOT):
    """[(ts, keys)] with skewed key popularity; same ts for a whole slot."""
    words = [f"P:k{i:02d}" for i in range(vocab)]
    weights = [1.0 / (i + 1) for i in range(vocab)]
    out = []
    for s in range(n_slots):
        ts = T0 + s * step
        for _ in range(rng.randint(*per_slot)):
            ks = set(rng.choices(words, weights, k=rng.randint(1, 4)))
            out.append((ts, tuple(sorted(ks))))
    return out


def art(idx, ts, keys, url=None):
    return GkgArticle(idx=idx, ts=ts, domain="d.com", url=url or f"u{idx}", title=f"t{idx}",
                      keys=tuple(keys), countries=(), tone=None)


class TimeDecayWindowTest(unittest.TestCase):
    def test_matches_direct_sum(self):
        rng = random.Random(SEED)
        stream = random_stream(rng)
        w = TimeDecayWindow(72)
        for ts, keys in stream:
            w.add(keys, ts)
        t = stream[-1][0] + 3600
        for key in ("P:k00", "P:k05", "P:k39"):
            self.assertAlmostEqual(w.score(key, t), direct(stream, key, t, 72), places=9)
        self.assertEqual(w.score("P:none", t), 0.0)

    def test_rebase_keeps_scores(self):
        rng = random.Random(SEED + 1)
        stream = random_stream(rng, n_slots=300)
        # half-life 0.05 h = 3 min, so 300 slots of 15 min = 1500 half-lives -> many automatic rebases
        w = TimeDecayWindow(0.05)
        for ts, keys in stream:
            w.add(keys, ts)
        self.assertGreater(w.rebases, 0)
        t = stream[-1][0]
        for key in ("P:k00", "P:k01", "P:k10"):
            exact = direct(stream, key, t, 0.05)
            self.assertAlmostEqual(w.score(key, t), exact, delta=1e-9 * max(1.0, exact))
        # an explicit rebase changes neither scores nor the order
        w.snapshot(t)
        before = w.top(10)
        scores = {k: w.score(k, t) for k, _ in before}
        w._rebase(7)
        after = w.top(10)
        self.assertEqual([k for k, _ in before], [k for k, _ in after])
        for k, s in after:
            self.assertAlmostEqual(s, scores[k], delta=1e-12 * scores[k])

    def test_incremental_topk_equals_full_sort(self):
        for th in (None, 0.3):
            rng = random.Random(SEED + 2)
            stream = random_stream(rng, n_slots=400, vocab=200)
            w = TimeDecayWindow(2.0, th, top_m=15, prune_every_hours=1.0)
            last = None
            for ts, keys in stream:
                if last is not None and ts != last and (ts - T0) % 3600 == 0:
                    w.snapshot(last)
                    self.assertEqual(w.top(10), w.full_top(10, last), f"theta={th} t={last}")
                w.add(keys, ts)
                last = ts
            if th is not None:
                self.assertGreater(w.deleted_total, 0)
        with self.assertRaises(ValueError):
            w.top(16)

    def test_pruning_underestimates(self):
        rng = random.Random(SEED + 3)
        stream = random_stream(rng, n_slots=300, vocab=120)
        exact, pruned = TimeDecayWindow(1.0), TimeDecayWindow(1.0, 0.5, prune_every_hours=1.0)
        last, checked = None, 0
        for ts, keys in stream:
            if last is not None and ts != last:
                exact.snapshot(last)
                pruned.snapshot(last)
                for k in exact._v:
                    self.assertLessEqual(pruned.score(k, last), exact.score(k, last) + 1e-9)
                    checked += 1
                self.assertLessEqual(len(pruned), len(exact))
            exact.add(keys, ts)
            pruned.add(keys, ts)
            last = ts
        self.assertGreater(pruned.deleted_total, 0)
        self.assertGreater(checked, 0)
        # a deleted key starts again from 0
        w = TimeDecayWindow(1.0, 0.5, prune_every_hours=0)
        w.add(["P:a"], T0)
        w.snapshot(T0 + 2 * 3600)                    # score 0.25 < 0.5 -> deleted
        self.assertNotIn("P:a", w)
        w.add(["P:a"], T0 + 2 * 3600)
        self.assertAlmostEqual(w.score("P:a", T0 + 2 * 3600), 1.0)

    def test_ties_broken_by_key(self):
        w = TimeDecayWindow(72, top_m=3)
        w.add(["P:b", "P:a", "P:c", "P:d"], T0)
        w.snapshot(T0)
        self.assertEqual([k for k, _ in w.top(3)], ["P:a", "P:b", "P:c"])
        w.add(["P:d"], T0 + SLOT)
        w.snapshot(T0 + SLOT)
        self.assertEqual([k for k, _ in w.top(3)], ["P:d", "P:a", "P:b"])
        self.assertEqual(w.top(3), w.full_top(3, T0 + SLOT))

    def test_bias_corrected_rate(self):
        w = TimeDecayWindow(72)
        self.assertIsNone(w.bias_corrected_rate("P:x", T0))
        w.add(["P:x"], T0)
        self.assertIsNone(w.bias_corrected_rate("P:x", T0))       # elapsed 0
        t = T0 + 10 * 3600
        s = w.score("P:x", t)
        self.assertAlmostEqual(w.bias_corrected_rate("P:x", t),
                               s * math.log(2) / 72 / (1 - 2 ** (-10 / 72)), places=12)
        # constant 16 mentions/hour (4 per 15-min slot) -> rate ≈ 16 early and late
        w = TimeDecayWindow(72)
        for s_ in range(4 * 24 * 40):
            w.add(["P:c"] * 4, T0 + s_ * SLOT)                # 4 mentions in the slot
            if s_ in (4 * 12, 4 * 24 * 40 - 1):
                t = T0 + s_ * SLOT + SLOT                     # end of the slot
                self.assertAlmostEqual(w.bias_corrected_rate("P:c", t), 16.0, delta=16 * 0.01)

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            TimeDecayWindow(0)
        with self.assertRaises(ValueError):
            TimeDecayWindow(72, 0)


class GkgEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gkg_engine_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_after_whole_ts_batch(self):
        seen = []

        def on_snap(eng, t):
            seen.append((t, eng.window("72h").score("P:a", t), eng.articles))

        eng = GkgEngine(GkgEngineConfig(snapshot_every_minutes=60), on_snapshot=on_snap)
        arts = [art(0, T0, ["P:a"]), art(1, T0, ["P:a"]), art(2, T0, ["P:a"])]
        arts += [art(3 + i, T0 + (i + 1) * SLOT, ["P:b"]) for i in range(4)]   # 00:15 .. 01:00
        arts += [art(7, T0 + 5 * SLOT, ["P:a"])]                                # 01:15
        eng.run(arts)
        # first snapshot only after the whole 00:00 batch (3 articles), the next at 01:00
        # once its batch closed, then the final one in finish()
        self.assertEqual([t for t, _, _ in seen], [T0, T0 + 4 * SLOT, T0 + 5 * SLOT])
        self.assertAlmostEqual(seen[0][1], 3.0)
        self.assertEqual(seen[0][2], 3)        # taken when the next ts arrived, before adding it
        self.assertEqual(seen[1][2], 7)
        self.assertEqual(eng.snapshots, 3)

    def test_variants_and_memory_report(self):
        cfg = GkgEngineConfig(windows=(("72h", 72.0),), thresholds=(None, 0.5))
        eng = GkgEngine(cfg)
        rng = random.Random(SEED + 4)
        stream = random_stream(rng, n_slots=100)
        eng.run(art(i, ts, keys) for i, (ts, keys) in enumerate(stream))
        self.assertEqual(set(eng.windows), {("72h", "exact"), ("72h", "theta=0.5")})
        top = eng.top("72h", 5)
        self.assertEqual(len(top), 5)
        self.assertEqual(top[0]["key"], "P:k00")
        self.assertGreater(top[0]["rate_per_hour"], 0)
        rep = eng.memory_report()
        self.assertEqual(len(rep["decay_windows"]), 2)
        self.assertGreater(rep["key_strings"]["unique"], 0)
        self.assertNotIn("evidence", rep)
        for row in rep["decay_windows"]:
            self.assertGreater(row["estimate_bytes"], 0)

    def test_evidence_only_for_tracked_keys(self):
        path = os.path.join(self.tmp, "ev.sqlite")
        cfg = GkgEngineConfig(windows=(("72h", 72.0),), top_m=3, evidence_top=2, evidence_path=path)
        eng = GkgEngine(cfg)
        arts = [art(0, T0, ["P:a", "P:b"]), art(1, T0, ["P:a"]), art(2, T0, ["P:c"]),
                art(3, T0 + SLOT, ["P:a", "P:c"]), art(4, T0 + SLOT, ["P:z"])]
        eng.run(arts)
        rep = eng.memory_report()["evidence"]
        # snapshot after the 00:00 batch: Top 2 = a (2), b (1, ties with c, wins by key).
        # final snapshot at 00:15: Top 2 = a, c -> c becomes tracked, but only from then on
        self.assertEqual(rep["tracked_keys"], 3)
        self.assertEqual(rep["articles"], 1)       # only article 3 came after a was tracked
        self.assertEqual(rep["postings"], 1)       # (a, 3); c was untracked at article 3
        self.assertEqual([a["idx"] for a in eng.evidence.articles_for("P:a")], [3])
        self.assertEqual(eng.evidence.articles_for("P:c"), [])   # known limitation: no backfill
        eng.close()
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT key, first_tracked_ts FROM keys ORDER BY key_id").fetchall(),
                         [("P:a", T0), ("P:b", T0), ("P:c", T0 + SLOT)])
        db.close()

    def test_evidence_daily_cap(self):
        path = os.path.join(self.tmp, "ev.sqlite")
        s = EvidenceStore(path, per_day_cap=2)
        s.track(["P:a", "P:b"], T0, "72h/exact")
        day2 = T0 + 86400
        arts = [art(0, T0, ["P:a", "P:b"]), art(1, T0, ["P:a"]), art(2, T0 + SLOT, ["P:a", "P:b"]),
                art(3, T0 + 2 * SLOT, ["P:a"]),                 # a is full today -> article not stored
                art(4, day2, ["P:a"]), art(5, day2, ["P:a"]), art(6, day2, ["P:a"])]
        for a in arts:
            s.add_article(a)
        rep = s.report()
        self.assertEqual(rep["per_day_cap"], 2)
        self.assertEqual(rep["truncated_postings"], 3)        # (a,2), (a,3), (a,6)
        self.assertEqual(rep["postings"], 6)                  # a:0,1  b:0,2  a:4,5
        self.assertEqual(rep["articles"], 5)                  # 0,1,2,4,5 (first come each day)
        self.assertEqual([x["idx"] for x in s.articles_for("P:a")], [5, 4, 1, 0])
        self.assertEqual([x["idx"] for x in s.articles_for("P:b")], [2, 0])
        s.close()
        with self.assertRaises(ValueError):
            EvidenceStore(path, per_day_cap=0)

    def test_evidence_store_resets(self):
        path = os.path.join(self.tmp, "ev.sqlite")
        s = EvidenceStore(path)
        s.track(["P:a"], T0, "72h/exact")
        s.add_article(art(0, T0, ["P:a"]))
        s.close()
        s = EvidenceStore(path)
        self.assertEqual(s.report()["articles"], 0)
        self.assertEqual(s.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 0)
        s.close()


if __name__ == "__main__":
    unittest.main()
