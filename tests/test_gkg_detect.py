"""gdelt-gkg P3: new issues (lift), burst/steady labels, warm-up, gap ratios, outlet tracking."""
import unittest

from trendpulse.gkg import GkgArticle
from trendpulse.gkg_engine import (ActivityTracker, GkgEngine, GkgEngineConfig, SlotCoverage,
                                   smoothed_ratio, trend_label)

T0 = 1727740800          # 2024-10-01T00:00:00Z
SLOT = 900
HOUR = 3600
DAY = 86400


class Stream:
    """Builds GkgArticles with increasing idx."""

    def __init__(self):
        self.items = []

    def add(self, ts, keys, domain="d0.com", n=1):
        for _ in range(n):
            i = len(self.items)
            self.items.append(GkgArticle(idx=i, ts=ts, domain=domain, url=f"u{i}", title="",
                                         keys=tuple(keys), countries=(), tone=None))
        return self


def new_issue_run(n_articles, n_domains, activate_score=1.0):
    s = Stream().add(T0, ["P:bg"])
    for i in range(n_articles):
        s.add(T0 + SLOT, ["P:x"], domain=f"d{i % n_domains}.com")
    eng = GkgEngine(GkgEngineConfig(activate_score=activate_score))
    eng.run(s.items)
    return eng


class NewIssueTest(unittest.TestCase):
    def test_condition_boundaries(self):
        eng = new_issue_run(20, 5)
        new = eng.detection["new"]
        self.assertEqual(new["status"], "ok")
        self.assertEqual([r["key"] for r in new["top"]], ["P:x"])
        row = new["top"][0]
        self.assertEqual((row["articles_72h"], row["outlets_72h"]), (20, 5))
        w72, w168 = eng.window("72h"), eng.window("168h")
        t = T0 + SLOT
        self.assertAlmostEqual(row["lift"], (w72.bias_corrected_rate("P:x", t) + 1)
                               / (w168.bias_corrected_rate("P:x", t) + 1))
        self.assertEqual(new_issue_run(19, 5).detection["new"]["top"], [])     # 19 articles
        self.assertEqual(new_issue_run(20, 4).detection["new"]["top"], [])     # 4 outlets

    def test_activation_undercounts_by_at_most_five(self):
        # default activation score 3: the first 2 articles (score 1, 2) are not counted
        eng = new_issue_run(22, 5, activate_score=3.0)
        self.assertEqual(eng.activity.articles("P:x", T0 + SLOT), 20)
        self.assertEqual([r["key"] for r in eng.detection["new"]["top"]], ["P:x"])
        self.assertEqual(new_issue_run(21, 5, activate_score=3.0).detection["new"]["top"], [])

    def test_entry_events(self):
        eng = GkgEngine(GkgEngineConfig())
        self.assertEqual([(e["key"], e["kind"]) for e in eng.record_entries(1, ["A", "B"])],
                         [("A", "first"), ("B", "first")])
        self.assertEqual(eng.record_entries(2, ["B"]), [])
        ev = eng.record_entries(3, ["B", "A"])
        self.assertEqual([(e["key"], e["kind"], e["rank"]) for e in ev], [("A", "re-entry", 2)])
        ev = eng.record_entries(4, ["C", "A", "B"])
        self.assertEqual([(e["key"], e["kind"], e["rank"]) for e in ev], [("C", "first", 1)])
        self.assertEqual(len(eng.new_events), 4)

    def test_entry_event_from_stream(self):
        eng = new_issue_run(20, 5)
        self.assertEqual([(e["key"], e["kind"]) for e in eng.new_events], [("P:x", "first")])


class TrendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """40 days: steady key every slot, a key that stops after day 20, a burst in the last 6 h."""
        s = Stream()
        end = T0 + 40 * DAY
        t = T0
        while t < end:
            s.add(t, ["P:steady"], n=4)
            if t < T0 + 20 * DAY:
                s.add(t, ["P:cooling"], n=4)
            if t >= end - 6 * HOUR:
                s.add(t, ["P:burst"], n=200)
            t += SLOT
        cls.status = []

        def on_snap(eng, t):
            tr = eng.detection["trend"]
            cls.status.append(((t - T0) / DAY, tr["status"], {r["key"]: r["label"] for r in tr["top"]}))

        cls.eng = GkgEngine(GkgEngineConfig(trend_k=3), on_snapshot=on_snap).run(s.items)

    def test_label_boundaries(self):
        self.assertEqual(trend_label(2.0), "rising")
        self.assertEqual(trend_label(1.9999), "steady")
        self.assertEqual(trend_label(0.5), "steady")
        self.assertEqual(trend_label(0.4999), "cooling")
        self.assertEqual(trend_label(2.9, factor=3.0), "steady")
        self.assertEqual(smoothed_ratio(3.0, 1.0, 1.0), 2.0)
        self.assertEqual(smoothed_ratio(0.0, 0.0, 1.0), 1.0)

    def test_warming_up_before_14_days(self):
        before = [x for x in self.status if x[0] < 14]
        after = [x for x in self.status if x[0] >= 14]
        self.assertTrue(before and after)
        self.assertTrue(all(st == "warming_up" and set(lab.values()) == {"warming_up"} for _, st, lab in before))
        self.assertTrue(all(st == "ok" for _, st, _ in after))
        self.assertEqual(after[0][0], 14.0)

    def test_labels_at_day_40(self):
        tr = self.eng.detection["trend"]
        labels = {r["key"]: r["label"] for r in tr["top"]}
        self.assertEqual(labels, {"P:burst": "rising", "P:steady": "steady", "P:cooling": "cooling"})
        for r in tr["top"]:
            self.assertAlmostEqual(r["ratio"], (r["rate_168h"] + 1) / (r["rate_720h"] + 1))
        self.assertEqual({r["label_ko"] for r in tr["top"]}, {"최근 급증", "꾸준", "식는 중"})


class GapTest(unittest.TestCase):
    def test_missing_ratio(self):
        c = SlotCoverage()
        for i in range(96):
            if i != 50:
                c.mark(T0 + i * SLOT)
        self.assertEqual(c.missing_ratio(T0 + 95 * SLOT, 72), (1 / 96, 96))    # clipped at start
        self.assertEqual(c.missing_ratio(T0 + 3 * SLOT, 72), (0.0, 4))
        for i in range(96, 400):
            if i % 20:
                c.mark(T0 + i * SLOT)
        ratio, expected = c.missing_ratio(T0 + 399 * SLOT, 72)                  # slots 112..399
        self.assertEqual(expected, 288)
        self.assertEqual(ratio, sum(1 for i in range(112, 400) if i % 20 == 0) / 288)

    def gap_run(self, n_missing):
        s = Stream()
        for i in range(192):                          # two days
            if not (100 <= i < 100 + n_missing):
                s.add(T0 + i * SLOT, ["P:bg"])
        for i in range(20):
            s.add(T0 + 191 * SLOT, ["P:x"], domain=f"d{i % 5}.com")
        eng = GkgEngine(GkgEngineConfig(activate_score=1.0))
        eng.run(s.items)
        return eng

    def test_hold_only_above_five_percent(self):
        eng = self.gap_run(9)                         # 9/192 = 4.7 %
        d = eng.detection
        self.assertAlmostEqual(d["gaps"]["72h"]["missing_ratio"], 9 / 192)
        self.assertFalse(d["gaps"]["72h"]["insufficient"])
        self.assertEqual(d["new"]["status"], "ok")
        self.assertIn("P:x", [r["key"] for r in d["new"]["top"]])
        eng = self.gap_run(10)                        # 10/192 = 5.2 %
        d = eng.detection
        self.assertTrue(d["gaps"]["72h"]["insufficient"])
        self.assertEqual(d["new"]["status"], "insufficient")
        self.assertEqual(d["new"]["label"], "근거 부족")
        self.assertEqual(d["new"]["top"], [])
        self.assertEqual(d["trend"]["status"], "warming_up")   # warm-up is shown before gaps
        # a held snapshot records no events and keeps the previous Top K
        eng._prev_new = ["P:old"]
        n_events = len(eng.new_events)
        again = eng._detect(d["t"])
        self.assertEqual(again["new"]["status"], "insufficient")
        self.assertEqual(eng._prev_new, ["P:old"])
        self.assertEqual(len(eng.new_events), n_events)


class ActivityTrackerTest(unittest.TestCase):
    def test_activation_and_counts(self):
        a = ActivityTracker(72, activate_score=3.0, max_outlets=8)
        a.observe("P:k", T0, "a.com", 2.9)
        self.assertNotIn("P:k", a)
        a.observe("P:k", T0, "a.com", 3.0)
        self.assertIn("P:k", a)
        self.assertEqual((a.articles("P:k", T0), a.outlets("P:k", T0)), (1, 1))

    def test_keeps_at_most_8_most_recent_outlets(self):
        a = ActivityTracker(72, activate_score=0.0, max_outlets=8)
        for i in range(10):
            a.observe("P:k", T0 + i * SLOT, f"d{i}.com", 5.0)
        a.observe("P:k", T0 + 10 * SLOT, "d5.com", 5.0)       # already kept: refresh, no eviction
        out = a.items["P:k"].outlets
        self.assertEqual(sorted(out), [f"d{i}.com" for i in range(2, 10)])
        self.assertEqual(out["d5.com"], T0 + 10 * SLOT)
        self.assertEqual(a.outlets("P:k", T0 + 10 * SLOT), 8)  # capped at 8 (true count 10)
        self.assertEqual(a.articles("P:k", T0 + 10 * SLOT), 11)

    def test_window_and_sweep(self):
        a = ActivityTracker(72, activate_score=0.0)
        a.observe("P:k", T0, "a.com", 1.0)                      # hour H0
        a.observe("P:k", T0 + 71 * HOUR, "b.com", 1.0)          # hour H0+71
        t = T0 + 71 * HOUR
        self.assertEqual((a.articles("P:k", t), a.outlets("P:k", t)), (2, 2))
        t = T0 + 72 * HOUR                                      # hour H0 left the 72 clock hours
        self.assertEqual((a.articles("P:k", t), a.outlets("P:k", t)), (1, 1))
        self.assertEqual(a.sweep(T0 + 142 * HOUR), 0)          # H0+71 still inside
        self.assertEqual(a.sweep(T0 + 143 * HOUR), 1)
        self.assertNotIn("P:k", a)
        self.assertEqual(a.memory_bytes()["tracked_keys"], 0)


if __name__ == "__main__":
    unittest.main()
