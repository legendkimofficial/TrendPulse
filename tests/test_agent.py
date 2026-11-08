"""Agent and engine tests on small synthetic streams, with the Solar HTTP call mocked."""
import json
import os
import unittest
from unittest import mock

from trendpulse import agent
from trendpulse.data import Article
from trendpulse.engine import EngineConfig, TrendEngine
from trendpulse.text import tokenize

NATO = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
        "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()


def make_articles():
    heads = []
    for i in range(600):
        if i % 3 == 0:
            heads.append(f"Festival Crowds Gather In City Park {i}")
        elif i % 3 == 1:
            heads.append(f"Senate Debates Budget Bill Number {i}")
        else:
            heads.append(f"Weather Forecast Calls For Rain {i}")
    return _articles(heads)


def make_rotating_topics(block=60):
    """Each block of articles is dominated by a new topic word -> Top K keeps changing."""
    heads = []
    for b, word in enumerate(NATO):
        for i in range(block):
            heads.append(f"{word.title()} Story Number {i}" if i % 2 == 0 else f"Daily Briefing {word.title()}")
    return _articles(heads)


def _articles(heads):
    return [Article(idx=i, date=f"2020-{1 + i // 900:02d}-{1 + (i // 30) % 28:02d}", headline=h,
                    description="desc", category="TEST", link=f"https://example.org/{i}",
                    tokens=tuple(tokenize(h))) for i, h in enumerate(heads)]


def tool_call(name, args, cid="call_1"):
    return {"choices": [{"message": {"content": None, "tool_calls": [{
        "id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def final(text):
    return {"choices": [{"message": {"content": text}}]}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.engine = TrendEngine(make_articles(), EngineConfig(c=0.01, k=5, dgim_n=100, snapshot_every=20))
        self.engine.step(600)

    def run_solar(self, responses):
        calls = []

        def fake_post(url, key, payload, timeout=60):
            calls.append(json.loads(json.dumps(payload)))
            return responses[len(calls) - 1]

        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "test"}), \
                mock.patch.object(agent, "_post", fake_post):
            res = agent.ask(self.engine, "festival 왜?")
        return res, calls

    def test_guess_keyword_from_korean_question(self):
        self.assertEqual(agent.guess_keyword(self.engine, "festival이 왜 순위에 올랐어?"), "festival")

    def test_offline_answer_cites_valid_articles(self):
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": ""}):
            res = agent.ask(self.engine, "festival 왜 화제야?")
        self.assertEqual(res["mode"], "offline")
        self.assertTrue(res["citations"])
        self.assertTrue(all(c["valid"] and c["link"] for c in res["citations"]))
        self.assertEqual(res["as_of"]["t"], self.engine.now)

    def test_offline_overview_without_keyword(self):
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": ""}):
            res = agent.ask(self.engine, "요즘 뭐가 화제야?")
        self.assertIsNone(res["keyword"])
        self.assertIn("Top 10", res["answer"])

    def test_solar_tool_loop_with_valid_citation(self):
        res, calls = self.run_solar([
            tool_call("search_related_news", {"keyword": "festival", "limit": 3}),
            final("festival 기사 근거 [#597]"),
        ])
        self.assertTrue(res["mode"].startswith("solar"))
        self.assertTrue(res["verified"])
        self.assertEqual(res["citations"][0]["id"], 597)
        self.assertEqual(calls[0]["tool_choice"], "required")   # first round must call a tool
        self.assertEqual(calls[1]["tool_choice"], "auto")
        tool_msgs = [m for m in calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1")
        self.assertEqual(json.loads(tool_msgs[0]["content"])["count"], 3)

    def test_answer_without_tools_and_fake_citation_is_rejected(self):
        res, calls = self.run_solar([
            final("근거 없이 쓴 답변 [#999999999]"),
            final("다시 써도 근거 없음 [#999999999]"),
        ])
        self.assertEqual(res["mode"], "offline-fallback")
        self.assertIn("근거 검증", res["warning"])
        self.assertNotIn("999999999", res["answer"])
        self.assertTrue(all(c["valid"] for c in res["citations"]))
        # the corrective retry was sent and still required a tool call
        self.assertIn("근거 검증을 통과하지 못했다", calls[1]["messages"][-1]["content"])
        self.assertEqual(calls[1]["tool_choice"], "required")

    def test_invalid_citation_repaired_on_retry(self):
        res, calls = self.run_solar([
            tool_call("search_related_news", {"keyword": "festival", "limit": 2}),
            final("잘못된 인용 [#3]"),            # article 3 was not returned by the tool
            final("고친 답변 [#597]"),
        ])
        self.assertTrue(res["verified"])
        self.assertEqual(res["answer"], "고친 답변 [#597]")
        self.assertIn("evidence-check", [t["tool"] for t in res["trace"]])

    def test_keyword_answer_must_search_articles(self):
        res, _ = self.run_solar([
            tool_call("get_keyword_stats", {"keyword": "festival"}),
            final("통계만 보고 쓴 답변"),
            final("여전히 기사 조회 없음"),
        ])
        self.assertEqual(res["mode"], "offline-fallback")

    def test_solar_failure_falls_back(self):
        def boom(*a, **k):
            raise agent.urllib.error.URLError("no network")

        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "test"}), \
                mock.patch.object(agent, "_post", boom):
            res = agent.ask(self.engine, "festival 왜?")
        self.assertEqual(res["mode"], "offline-fallback")
        self.assertIn("warning", res)

    def test_related_respects_as_of(self):
        tools = agent.TrendTools(self.engine, as_of=300)
        res = tools.search_related_news("festival", limit=5)
        self.assertTrue(all(a["id"] <= 300 for a in res["articles"]))


class EngineTest(unittest.TestCase):
    def test_ranking_and_auto_tracking(self):
        e = TrendEngine(make_articles(), EngineConfig(c=0.01, k=3, dgim_n=100, snapshot_every=20))
        e.step(600)
        st = e.state()
        scores = [r["score"] for r in st["ranking"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(st["metrics"]["topk_overlap"], 3)
        info = e.dgim_info(st["ranking"][0]["keyword"])
        self.assertIsNotNone(info)
        self.assertLessEqual(info["rel_error"], info["bound"])

    def test_current_topk_always_tracked_after_capacity_reached(self):
        cfg = EngineConfig(c=0.02, k=3, dgim_n=100, snapshot_every=10, max_tracked=6, warmup_factor=0)
        e = TrendEngine(make_rotating_topics(), cfg)
        e.step(len(e.articles))
        self.assertGreater(e.evictions, 0)                       # capacity was hit
        self.assertLessEqual(len(e.trackers), cfg.max_tracked)
        top = [r["keyword"] for r in e.state()["ranking"]]
        for w in top:
            self.assertIn(w, e.trackers, f"{w} in Top K but not tracked")

    def test_pins_survive_and_are_capped(self):
        cfg = EngineConfig(c=0.02, k=3, dgim_n=100, snapshot_every=10, max_tracked=6, warmup_factor=0)
        e = TrendEngine(make_rotating_topics(), cfg)
        ok, _ = e.track("alpha")
        self.assertTrue(ok)
        for w in ("bravo", "charlie"):
            self.assertTrue(e.track(w)[0])
        ok, msg = e.track("delta")                               # 4th pin > max_tracked // 2
        self.assertFalse(ok)
        e.step(len(e.articles))
        for w in ("alpha", "bravo", "charlie"):
            self.assertIn(w, e.trackers)

    def test_retracking_restarts_observation(self):
        cfg = EngineConfig(c=0.02, k=3, dgim_n=100, snapshot_every=10, max_tracked=4, warmup_factor=0)
        e = TrendEngine(make_rotating_topics(), cfg)
        e.step(len(e.articles))
        regen = [w for w, n in e.track_generations.items() if n > 1]
        for w in regen:
            if w in e.trackers:
                self.assertGreater(e.trackers[w].generation, 1)

    def test_expire_all_uses_one_reference_time(self):
        e = TrendEngine(make_articles(), EngineConfig(c=0.01, k=3, dgim_n=50, snapshot_every=20))
        e.step(600)
        e.metrics()
        for tr in e.trackers.values():
            if tr.dgim._buckets:
                self.assertGreater(tr.dgim._buckets[-1][0], e.now - 50)

    def test_config_validation(self):
        for bad in ({"snapshot_every": 0}, {"warmup_factor": -1}, {"new_entry_gap": 0},
                    {"max_tracked": 2, "k": 5}, {"c": 0.0}, {"threshold": 1.0}, {"dgim_r": 1},
                    {"c": float("nan")}):
            with self.assertRaises(ValueError, msg=str(bad)):
                EngineConfig(**bad).validate()
        EngineConfig().validate()

    def test_event_kinds(self):
        e = TrendEngine(make_rotating_topics(), EngineConfig(c=0.02, k=3, dgim_n=100, snapshot_every=10,
                                                            new_entry_gap=5, warmup_factor=0))
        e.step(len(e.articles))
        kinds = {ev["kind"] for ev in e.events}
        self.assertIn("first", kinds)
        self.assertTrue(kinds <= {"first", "reentry"})

    def test_reset_with_start_date_warms_up(self):
        e = TrendEngine(make_articles(), EngineConfig(c=0.01, k=3, snapshot_every=20))
        e.reset(start_date="2020-01-10")
        self.assertEqual(e.articles[e.start_pos].date, "2020-01-10")
        self.assertGreater(len(e.pruned), 0)


if __name__ == "__main__":
    unittest.main()
