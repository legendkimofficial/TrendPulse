"""v0.4 tests: evidence counts, rank history (race chart), timeline, seek, API routes."""
import unittest

from app import create_app
from tests.test_agent import make_articles, make_rotating_topics
from trendpulse.cases import EVENTS, events_as_dicts
from trendpulse.engine import EngineConfig, TrendEngine


class EvidenceTest(unittest.TestCase):
    def test_ranking_rows_carry_evidence_and_thin_flag(self):
        e = TrendEngine(make_articles(), EngineConfig(decay_unit="article", c=0.01, k=3, snapshot_every=20, min_evidence=2))
        e.step(300)
        rows = e.ranking()
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("evidence", r)
            self.assertIn("thin", r)
            # the synthetic stream repeats three headlines, so every Top-3 word has
            # about 1/3 of the last 1/c = 100 articles behind it
            self.assertGreaterEqual(r["evidence"], 20)
            self.assertFalse(r["thin"])
        # evidence equals the recent-window mention count reported by keyword_detail
        d = e.keyword_detail(rows[0]["keyword"])
        self.assertEqual(rows[0]["evidence"], d["mentions_in_effective_window"])

    def test_thin_flag_follows_min_evidence(self):
        e = TrendEngine(make_articles(), EngineConfig(decay_unit="article", c=0.01, k=3, snapshot_every=20, min_evidence=50))
        e.step(300)
        self.assertTrue(all(r["thin"] for r in e.ranking()))

    def test_burst_rows_carry_evidence_and_display_rank(self):
        e = TrendEngine(make_rotating_topics(), EngineConfig(decay_unit="article", c=0.01, k=5, snapshot_every=20))
        e.step(400)
        for b in e.burst():
            self.assertIn("evidence", b)
            self.assertIn("rank", b)          # None when the keyword is outside the Top K
            if b["rank"] is not None:
                self.assertLessEqual(b["rank"], 5)

    def test_min_evidence_validated(self):
        with self.assertRaises(ValueError):
            EngineConfig(min_evidence=-1).validate()
        EngineConfig(min_evidence=0).validate()


class RankHistoryTest(unittest.TestCase):
    def setUp(self):
        self.e = TrendEngine(make_rotating_topics(), EngineConfig(decay_unit="article", c=0.01, k=5, snapshot_every=20))
        self.e.step(400)

    def test_shape_and_current_point(self):
        race = self.e.rank_history(k=3, n=10)
        n = len(race["t"])
        self.assertEqual(len(race["dates"]), n)
        self.assertLessEqual(n, 11)                      # 10 snapshots + the current ranking
        self.assertEqual(race["t"][-1], self.e.now)       # ends at "now"
        self.assertEqual(race["k"], 3)
        for w, series in race["series"].items():
            self.assertEqual(len(series), n)
            self.assertTrue(any(r is not None for r in series))
            self.assertTrue(all(r is None or 1 <= r <= 3 for r in series))
        # the last point is the displayed ranking
        shown = {r["keyword"]: r["rank"] for r in self.e.ranking()[:3]}
        for w, rank in shown.items():
            self.assertEqual(race["series"][w][-1], rank)

    def test_k_is_capped_by_config(self):
        race = self.e.rank_history(k=50, n=5)
        self.assertEqual(race["k"], 5)

    def test_rotating_topics_enter_and_leave(self):
        race = self.e.rank_history(k=3, n=60)
        # with a new dominant word every 60 articles, some series must have gaps
        self.assertTrue(any(None in s for s in race["series"].values()))

    def test_state_includes_race_and_baseline_ranking(self):
        st = self.e.state()
        self.assertIn("race", st)
        self.assertIn("baseline_ranking", st)
        self.assertEqual(len(st["baseline_ranking"]), 5)
        self.assertEqual([b["rank"] for b in st["baseline_ranking"]], [1, 2, 3, 4, 5])
        self.assertTrue(all("pruned_rank" in b for b in st["baseline_ranking"]))


class TimelineSeekTest(unittest.TestCase):
    def setUp(self):
        self.e = TrendEngine(make_articles(), EngineConfig(decay_unit="article", c=0.01, k=3, snapshot_every=20))

    def test_timeline(self):
        tl = self.e.timeline()
        self.assertEqual(sum(m["count"] for m in tl["months"]), len(self.e.articles))
        self.assertEqual(tl["first_date"], self.e.articles[0].date)
        self.assertEqual(len(tl["events"]), len(EVENTS))
        self.assertEqual(tl["events"], sorted(tl["events"], key=lambda r: r["date"]))

    def test_events_module(self):
        self.assertEqual(len(EVENTS), 26)
        groups = {g for _, _, _, g in EVENTS}
        self.assertEqual(groups, {"v0.2", "holdout"})
        self.assertEqual(sum(1 for *_, g in EVENTS if g == "holdout"), 10)
        self.assertEqual(len(events_as_dicts()), 26)

    def test_seek_forward_steps_and_backward_resets(self):
        self.e.step(100)
        self.assertEqual(self.e.seek("2020-01-20"), "stepped")
        self.assertEqual(self.e.clock()["date"], "2020-01-20")
        self.assertEqual(self.e.clock()["start_date"], self.e.articles[0].date)   # replay start kept
        self.assertEqual(self.e.seek("2020-01-05"), "reset")
        self.assertEqual(self.e.clock()["start_date"], "2020-01-05")

    def test_seek_far_forward_resets(self):
        self.e.SEEK_FORWARD_MAX = 10
        self.assertEqual(self.e.seek("2020-01-20"), "reset")
        self.assertEqual(self.e.clock()["start_date"], "2020-01-20")

    def test_seek_past_the_end(self):
        self.e.step(10)
        self.e.seek("2030-01-01")
        self.assertTrue(self.e.finished)


class ApiV04Test(unittest.TestCase):
    def setUp(self):
        self.app = create_app(make_articles(), start_date=None)
        self.client = self.app.test_client()

    def test_timeline_route(self):
        res = self.client.get("/api/timeline")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("months", body)
        self.assertEqual(len(body["events"]), 26)

    def test_seek_route(self):
        self.assertEqual(self.client.post("/api/seek", json={"date": "nope"}).status_code, 400)
        res = self.client.post("/api/seek", json={"date": "2020-01-10"})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn(body["seek"], ("stepped", "reset"))
        self.assertEqual(body["clock"]["date"], "2020-01-10")
        self.assertIn("race", body)

    def test_reset_accepts_min_evidence(self):
        self.assertEqual(self.client.post("/api/reset", json={"min_evidence": -1}).status_code, 400)
        self.assertEqual(self.client.post("/api/reset", json={"min_evidence": 51}).status_code, 400)   # range is 0..50
        # per-article decay with c=0.05 -> recent window of 20 articles -> every Top-K row is "thin" at 50
        res = self.client.post("/api/reset", json={"min_evidence": 50, "snapshot_every": 10, "decay_unit": "article", "c": 0.05})
        self.assertEqual(res.status_code, 200)
        st = self.client.post("/api/step", json={"n": 200}).get_json()
        self.assertTrue(all(r["thin"] for r in st["ranking"]))

    def test_info_reports_version(self):
        self.assertEqual(self.client.get("/api/info").get_json()["version"], "0.4")


if __name__ == "__main__":
    unittest.main()
