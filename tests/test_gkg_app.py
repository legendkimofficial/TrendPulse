"""gdelt-gkg dashboard: cache build/reuse, API routes, agent (offline and mocked Solar)."""
import gzip
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from trendpulse import gkg_agent
from trendpulse.gkg_cache import CacheConfig, CacheReader, ensure_cache, trend_groups

DAYS = ("2024-10-01", "2024-10-02")
NO_LLM = {"LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": ""}   # tests never reach a real API


def write_fake_slim(root):
    """Two days, every 15 minutes: a steady person, a story that starts on day 2, places, countries."""
    i = 0
    for day in DAYS:
        d0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        os.makedirs(os.path.join(root, "slim", day), exist_ok=True)
        for s in range(96):
            t = d0 + timedelta(minutes=15 * s)
            recs = []
            for j in range(6):
                persons = ["Joe Biden"] if j < 3 else ["Jane Roe"]
                orgs = ["NATO"] if j % 2 else ["World Bank"]
                if day == DAYS[1] and s >= 48 and j < 4:
                    persons = persons + ["Storm Alpha"]
                recs.append({"id": f"x{i}", "ts": t.strftime("%Y%m%d%H%M%S"), "domain": f"site{j}.com",
                             "url": f"https://site{j}.com/a/{i}", "title": f"Headline {i}",
                             "persons": persons, "orgs": orgs,
                             "places": [["Kyiv, Kyyiv, Ukraine", 4, "UP"], ["Ukraine", 1, "UP"]],
                             "themes": [], "tone": 0.5})
                i += 1
            with gzip.open(os.path.join(root, "slim", day, t.strftime("%H%M") + ".jsonl.gz"), "wt",
                           encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gkg_app_")
        cls.root = os.path.join(cls.tmp, "gdelt")
        write_fake_slim(cls.root)
        cls.cache = os.path.join(cls.tmp, "cache.sqlite")
        cls.evid = os.path.join(cls.tmp, "evid.sqlite")
        cls.cfg = CacheConfig()
        cls.meta = ensure_cache(cls.cfg, root=cls.root, path=cls.cache, evidence_path=cls.evid, progress=None)
        from app_gkg import create_app
        cls.app = create_app(cls.cache, cls.evid)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ cache
    def test_cache_built_once_and_reused(self):
        self.assertTrue(self.meta["rebuilt"])
        self.assertEqual(self.meta["articles"], 2 * 96 * 6)
        again = ensure_cache(self.cfg, root=self.root, path=self.cache, evidence_path=self.evid, progress=None)
        self.assertFalse(again["rebuilt"])
        self.assertEqual(again["fingerprint"], self.meta["fingerprint"])

    def test_cache_rebuilt_when_input_changes(self):
        tmp = tempfile.mkdtemp(prefix="gkg_app2_")
        try:
            root = os.path.join(tmp, "gdelt")
            shutil.copytree(self.root, root)
            cache, evid = os.path.join(tmp, "c.sqlite"), os.path.join(tmp, "e.sqlite")
            m1 = ensure_cache(self.cfg, root=root, path=cache, evidence_path=evid, progress=None)
            os.remove(os.path.join(root, "slim", DAYS[1], "2345.jsonl.gz"))
            m2 = ensure_cache(self.cfg, root=root, path=cache, evidence_path=evid, progress=None)
            self.assertTrue(m2["rebuilt"])
            self.assertNotEqual(m1["fingerprint"], m2["fingerprint"])
            self.assertEqual(m2["articles"], m1["articles"] - 6)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------ routes
    def test_info_and_six_hour_snapshots(self):
        info = self.client.get("/api/gkg/info").get_json()
        times = info["snapshots"]
        self.assertEqual(info["range"], {"start": DAYS[0], "end": DAYS[1]})
        # one per 6-hour block (8 blocks) plus the final 23:45 state
        self.assertEqual(len(times), 9)
        self.assertTrue(all(t % 21600 == 0 for t in times[:-1]))
        self.assertEqual(times, sorted(times))

    def test_snapshot_rows_and_filters(self):
        r = self.client.get("/api/gkg/snapshot?tab=major&kind=all").get_json()
        self.assertEqual(r["window"], "168h")
        row = r["rows"][0]
        for f in ("key", "score", "rate", "evidence", "outlets", "label", "label_ko", "gap", "approx", "dgim", "ratio"):
            self.assertIn(f, row)
        self.assertEqual(row["label"], "warming_up")          # 2 days < 21
        self.assertFalse(row["approx"])
        self.assertEqual(r["countries"][0]["name"], "ukraine")
        self.assertFalse(any(x["key"] == "L:ukraine" for x in r["rows"]))   # countries are not keys
        persons = self.client.get("/api/gkg/snapshot?tab=major&kind=P").get_json()["rows"]
        self.assertTrue(persons and all(x["key"].startswith("P:") for x in persons))
        new = self.client.get("/api/gkg/snapshot?tab=new").get_json()
        self.assertEqual((new["status"], new["rows"]), ("warming_up", []))   # 2 days < 7
        tr = self.client.get("/api/gkg/snapshot?tab=trend").get_json()
        self.assertEqual((tr["status"], tr["rows"], tr["window"]), ("warming_up", [], "168h"))   # < 21 days
        self.assertEqual(self.client.get("/api/gkg/snapshot?tab=x").status_code, 400)
        self.assertEqual(self.client.get("/api/gkg/snapshot?kind=Z").status_code, 400)
        self.assertEqual(self.client.get("/api/gkg/snapshot?t=abc").status_code, 400)

    def test_slider_time_changes_rows(self):
        times = self.client.get("/api/gkg/info").get_json()["snapshots"]
        early = self.client.get(f"/api/gkg/snapshot?t={times[1]}&tab=major").get_json()
        late = self.client.get(f"/api/gkg/snapshot?t={times[-1]}&tab=major").get_json()
        self.assertLess(early["articles"], late["articles"])
        self.assertNotIn("P:storm alpha", [x["key"] for x in early["rows"]])
        self.assertIn("P:storm alpha", [x["key"] for x in late["rows"]])
        # a time between snapshots resolves to the previous one
        mid = self.client.get(f"/api/gkg/snapshot?t={times[1] + 3600}").get_json()
        self.assertEqual(mid["t"], times[1])

    def test_key_detail_evidence_and_dgim(self):
        times = self.client.get("/api/gkg/info").get_json()["snapshots"]
        d = self.client.get("/api/gkg/key?key=P:joe biden").get_json()
        self.assertTrue(d["evidence"]["tracked"])
        snap = self.client.get("/api/gkg/snapshot").get_json()
        arts = d["evidence"]["articles"]
        self.assertTrue(arts)
        self.assertTrue(all(a["idx"] < snap["articles"] for a in arts))
        self.assertTrue({"title", "domain", "ts", "url"} <= set(arts[0]))
        # 30 per UTC day cap: two days -> at most 60 stored
        self.assertLessEqual(d["evidence"]["total"], 60)
        self.assertIsNotNone(d["row"]["dgim"])
        self.assertGreater(d["row"]["dgim"]["estimate"], 0)
        early = self.client.get(f"/api/gkg/key?key=P:joe biden&t={times[1]}").get_json()
        self.assertLess(early["evidence"]["total"], d["evidence"]["total"])
        self.assertEqual(self.client.get("/api/gkg/key?key=joe").status_code, 400)

    def test_new_issue_rows_are_tracked_for_evidence(self):
        """Keys shown in the new-issue list get evidence/DGIM even outside every Top 200."""
        from trendpulse.gkg_cache import _Collector
        from trendpulse.gkg_evidence import EvidenceStore
        ev = EvidenceStore(os.path.join(self.tmp, "track.sqlite"))
        col = _Collector(CacheConfig(top_n=2), None, None, {}, ev)

        class Eng:
            new_ranked = [{"key": "P:a"}, {"key": "P:b"}, {"key": "P:c"}, {"key": "O:x"}]
        col.block = int(1000 // 21600)          # same block: tracking only, no store
        col(Eng(), 1000)
        self.assertEqual(sorted(ev.tracked), ["O:x", "P:a", "P:b"])
        ev.close()

    def test_index_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Trend Pulse", r.get_data(as_text=True))

    # ------------------------------------------------------------------ agent
    def test_ask_offline_cites_only_returned_articles(self):
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "", **NO_LLM}):
            r = self.client.post("/api/gkg/ask", json={"question": "Joe Biden 왜 많이 나와?"}).get_json()
            self.assertEqual((r["mode"], r["key"]), ("offline", "P:joe biden"))
            cited = {int(x) for x in re.findall(r"\[#(\d+)\]", r["answer"])}
            d = self.client.get("/api/gkg/key?key=P:joe biden&limit=8").get_json()
            self.assertTrue(cited and cited <= {a["idx"] for a in d["evidence"]["articles"]})
            r = self.client.post("/api/gkg/ask", json={"question": "요즘 뭐가 화제야?"}).get_json()
            self.assertIsNone(r["key"])
            self.assertIn("7일 주요 이슈", r["answer"])
            self.assertEqual(self.client.post("/api/gkg/ask", json={}).status_code, 400)

    def test_ask_solar_mocked(self):
        reader = CacheReader(self.cache, self.evid)
        idx = CacheReader.evidence(reader, "P:joe biden", reader.articles[reader.times[-1]], 8)["articles"][0]["idx"]

        def fake(answers):
            seq = iter(answers)

            def _post(url, key, payload):
                return {"choices": [{"message": next(seq)}]}
            return _post

        call = {"tool_calls": [{"id": "c1", "function": {"name": "get_key_detail",
                                                          "arguments": json.dumps({"key": "joe biden"})}}]}
        good = [call, {"content": f"요약 [#{idx}]"}]
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "k", **NO_LLM}), \
                mock.patch.object(gkg_agent, "_post", fake(good)):
            r = gkg_agent.ask(reader, "joe biden 왜?")
        self.assertTrue(r["mode"].startswith("solar"))
        self.assertTrue(r["verified"])
        bad = [call, {"content": "요약 [#999999]"}, {"content": "다시 [#999998]"}]
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "k", **NO_LLM}), \
                mock.patch.object(gkg_agent, "_post", fake(bad)):
            r = gkg_agent.ask(reader, "joe biden 왜?")
        self.assertEqual(r["mode"], "offline-fallback")
        self.assertIn("근거 검증", r["warning"])


class TrendGroupsTest(unittest.TestCase):
    def test_order_and_cap(self):
        c = lambda k, ratio, lab: {"key": k, "ratio": ratio, "label": lab}  # noqa: E731
        cands = [c("P:s1", 1.0, "steady"), c("P:r1", 1.6, "rising"), c("P:c1", 0.6, "cooling"),
                 c("P:s2", 1.2, "steady"), c("P:r2", 2.4, "rising"), c("P:c2", 0.3, "cooling"),
                 c("P:r3", 2.4, "rising"), c("P:s3", 0.9, "steady")]
        g = trend_groups(cands, n=2)
        self.assertEqual([x["key"] for x in g["rising"]], ["P:r2", "P:r3"])    # ratio desc, tie by key
        self.assertEqual([x["key"] for x in g["cooling"]], ["P:c2", "P:c1"])   # ratio asc
        self.assertEqual([x["key"] for x in g["steady"]], ["P:s1", "P:s2"])    # input (168h score) order
        self.assertEqual(list(g), ["rising", "cooling", "steady"])


class TrendTabTest(unittest.TestCase):
    """22 days, so the 21-day warm-up is over at the end."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gkg_trend_")
        root = os.path.join(cls.tmp, "gdelt")
        d0 = datetime(2024, 9, 15, tzinfo=timezone.utc)
        end = d0 + timedelta(days=22)
        i, t = 0, d0
        while t < end:
            recs = []

            def add(persons, n=1):
                nonlocal i
                for _ in range(n):
                    recs.append({"id": f"x{i}", "ts": t.strftime("%Y%m%d%H%M%S"), "domain": f"s{i % 7}.com",
                                 "url": f"https://s.com/{i}", "title": f"T{i}", "persons": persons, "orgs": [],
                                 "places": [], "themes": [], "tone": None})
                    i += 1
            add(["Steady One"], 4)
            add(["Steady Two"], 3)
            add(["Org Holder"], 1)
            if t < d0 + timedelta(days=6):
                add(["Cool Six"], 4)
            if t < d0 + timedelta(days=8):
                add(["Cool Eight"], 4)
            if t >= end - timedelta(hours=6):
                add(["Burst Big"], 200)
                add(["Burst Small"], 100)
            day = t.strftime("%Y-%m-%d")
            os.makedirs(os.path.join(root, "slim", day), exist_ok=True)
            with gzip.open(os.path.join(root, "slim", day, t.strftime("%H%M") + ".jsonl.gz"), "wt",
                           encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            t += timedelta(minutes=15)
        cache, evid = os.path.join(cls.tmp, "c.sqlite"), os.path.join(cls.tmp, "e.sqlite")
        ensure_cache(CacheConfig(), root=root, path=cache, evidence_path=evid, progress=None)
        from app_gkg import create_app
        cls.client = create_app(cache, evid).test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_three_groups(self):
        r = self.client.get("/api/gkg/snapshot?tab=trend&kind=all").get_json()
        self.assertEqual(r["status"], "ok")
        groups = {}
        for row in r["rows"]:
            groups.setdefault(row["group"], []).append(row)
        self.assertEqual(list(groups), ["rising", "cooling", "steady"])
        self.assertEqual([x["key"] for x in groups["rising"]], ["P:burst big", "P:burst small"])
        self.assertEqual([x["key"] for x in groups["cooling"]], ["P:cool six", "P:cool eight"])
        self.assertEqual([x["key"] for x in groups["steady"]], ["P:steady one", "P:steady two", "P:org holder"])
        for row in groups["rising"]:
            self.assertGreaterEqual(row["ratio"], 1.5)
            self.assertEqual(row["label_ko"], "최근 급증")
        for row in groups["cooling"]:
            self.assertLess(row["ratio"], 1 / 1.5)
        self.assertTrue(all(1 / 1.5 <= x["ratio"] < 1.5 for x in groups["steady"]))
        self.assertGreater(groups["rising"][0]["ratio"], groups["rising"][1]["ratio"])
        self.assertLess(groups["cooling"][0]["ratio"], groups["cooling"][1]["ratio"])

    def test_new_issue_events_and_trend_race(self):
        st = self.client.get("/api/gkg/state?tab=trend&race=1").get_json()
        self.assertEqual(st["race"]["grp"], "rising")
        self.assertIn("P:burst big", st["race"]["series"])
        self.assertEqual(st["burst"]["status"], "ok")
        self.assertEqual(st["burst"]["rows"][0]["key"], "P:burst big")
        ev = st["events"]
        self.assertGreaterEqual(ev["first"], 1)
        self.assertEqual(ev["total"], ev["first"] + ev["reentry"])
        self.assertEqual([e["t"] for e in ev["rows"]], sorted((e["t"] for e in ev["rows"]), reverse=True))
        d = self.client.get("/api/gkg/key?key=P:burst big&full=1").get_json()
        self.assertEqual(d["entries"][0]["kind"], "first")

    def test_kind_filter_applies(self):
        self.assertEqual(self.client.get("/api/gkg/snapshot?tab=trend&kind=O").get_json()["rows"], [])
        rows = self.client.get("/api/gkg/snapshot?tab=trend&kind=P").get_json()["rows"]
        self.assertEqual(len(rows), 7)


class DashboardV04Test(unittest.TestCase):
    """v0.4-style dashboard: cache v4 tables and the /api/gkg/state, series, search, e1 routes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gkg_v04_")
        cls.root = os.path.join(cls.tmp, "gdelt")
        write_fake_slim(cls.root)
        cls.proc = os.path.join(cls.tmp, "processed")
        from trendpulse.gkg_cache import cache_paths
        cls.exact = cache_paths(None, cls.proc)
        cls.theta = cache_paths(0.5, cls.proc)
        cls.meta = ensure_cache(CacheConfig(), root=cls.root, path=cls.exact[0], evidence_path=cls.exact[1],
                                progress=None)
        ensure_cache(CacheConfig(theta=0.5), root=cls.root, path=cls.theta[0], evidence_path=cls.theta[1],
                     progress=None)
        from app_gkg import create_app
        from trendpulse.gkg_cache import discover_variants
        cls.variants = discover_variants(cls.proc)
        cls.results = os.path.join(cls.tmp, "results.json")
        cls.client = create_app(variants=cls.variants, results_path=cls.results).test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_variants_discovered_exact_first(self):
        self.assertEqual(list(self.variants), ["exact", "theta=0.5"])
        info = self.client.get("/api/gkg/info").get_json()
        self.assertEqual((info["variant"], info["default_variant"]), ("exact", "exact"))
        self.assertEqual([v["name"] for v in info["variants"]], ["exact", "theta=0.5"])
        self.assertEqual(self.client.get("/api/gkg/info?v=theta=0.5").get_json()["config"]["theta"], 0.5)
        self.assertEqual(self.client.get("/api/gkg/state?v=nope").status_code, 400)

    def test_day_table_counts_and_missing_slots(self):
        days = self.meta["days"]
        self.assertEqual([d["date"] for d in days], list(DAYS))
        self.assertEqual(sum(d["articles"] for d in days), self.meta["articles"])
        self.assertEqual(days[0]["files"], 96)
        from trendpulse.gkg_cache import day_table
        root = os.path.join(self.tmp, "gdelt_missing")
        shutil.copytree(self.root, root)
        os.remove(os.path.join(root, "slim", DAYS[1], "0315.jsonl.gz"))
        open(os.path.join(root, "slim", DAYS[1], "0315.missing"), "w").close()
        t = day_table(root, DAYS[0], DAYS[1], {})
        self.assertEqual((t[1]["missing"], t[1]["files"], t[0]["missing"]), (["0315"], 95, []))

    def test_info_has_timeline_and_event_catalog(self):
        info = self.client.get("/api/gkg/info").get_json()
        self.assertEqual(len(info["days"]), 2)
        ids = [e["id"] for e in info["events"]]
        self.assertIn("han_kang", ids)
        self.assertEqual([e["time"] for e in info["events"]], sorted(e["time"] for e in info["events"]))

    def test_state_rows_ranks_sparks_and_kpis(self):
        times = self.client.get("/api/gkg/info").get_json()["snapshots"]
        st = self.client.get(f"/api/gkg/state?t={times[-1]}&tab=major&kind=all").get_json()
        self.assertEqual((st["idx"], st["n"], st["t"]), (len(times) - 1, len(times), times[-1]))
        rows = st["rows"]
        self.assertEqual([r["rank"] for r in rows], list(range(1, len(rows) + 1)))
        storm = next(r for r in rows if r["key"] == "P:storm alpha")
        biden = next(r for r in rows if r["key"] == "P:joe biden")
        self.assertIsNotNone(biden["prev_rank"])
        self.assertEqual(biden["delta"], biden["prev_rank"] - biden["rank"])
        self.assertTrue(biden["spark"] and len(biden["spark"]) <= 28 and biden["spark"][-1] > 0)
        self.assertGreater(storm["spark"][-1], storm["spark"][-2])                # rising story
        self.assertNotIn("exact_rank", biden)                                     # exact view: no comparison
        k = st["kpi"]
        self.assertEqual((k["articles"], k["articles_total"]), (self.meta["articles"], self.meta["articles"]))
        self.assertEqual(set(k["stats"]["keys"]), {"72h", "168h", "720h"})
        self.assertGreater(k["stats"]["decay_bytes"], 0)
        self.assertEqual(st["burst"], {"status": "warming_up", "rows": []})     # 2 days < 7
        self.assertEqual(st["events"]["total"], 0)
        early = self.client.get(f"/api/gkg/state?t={times[0]}").get_json()
        self.assertTrue(all(r["prev_rank"] is None and r["delta"] is None for r in early["rows"]))

    def test_state_race(self):
        times = self.client.get("/api/gkg/info").get_json()["snapshots"]
        st = self.client.get(f"/api/gkg/state?t={times[-1]}&race=1&k=3").get_json()
        race = st["race"]
        self.assertEqual(race["t"], times[-len(race["t"]):])
        self.assertEqual(race["k"], 3)
        self.assertTrue(all(len(s) == len(race["t"]) for s in race["series"].values()))
        self.assertTrue(all(r is None or 1 <= r <= 3 for s in race["series"].values() for r in s))
        self.assertNotIn("race", self.client.get("/api/gkg/state").get_json())
        self.assertEqual(self.client.get("/api/gkg/state?tab=trend&race=1&grp=x").status_code, 400)

    def test_theta_variant_compares_with_exact(self):
        st = self.client.get("/api/gkg/state?v=theta=0.5&tab=major").get_json()
        self.assertEqual(st["variant"], "theta=0.5")
        self.assertTrue(st["exact_compared"])
        self.assertTrue(all("exact_rank" in r for r in st["rows"]))
        self.assertTrue(all(r["approx"] for r in st["rows"]))

    def test_series_search_and_full_key(self):
        times = self.client.get("/api/gkg/info").get_json()["snapshots"]
        r = self.client.get("/api/gkg/series?key=P:joe biden&key=L:kyiv, kyyiv, ukraine&key=P:storm alpha").get_json()
        self.assertEqual(r["t"], times)
        self.assertEqual(set(r["series"]), {"P:joe biden", "L:kyiv, kyyiv, ukraine", "P:storm alpha"})
        storm = r["series"]["P:storm alpha"]
        self.assertIsNone(storm["r72"][0])                  # not watched before it showed up
        self.assertGreater(storm["r72"][-1], 0)
        self.assertGreaterEqual(storm["watched_from"], times[0])
        cut = self.client.get(f"/api/gkg/series?key=P:joe biden&t={times[3]}").get_json()
        self.assertEqual(cut["t"], times[:4])
        self.assertEqual(self.client.get("/api/gkg/series").status_code, 400)
        self.assertEqual(self.client.get("/api/gkg/series?key=joe").status_code, 400)
        self.assertEqual(self.client.get("/api/gkg/search?q=BID").get_json()["keys"], ["P:joe biden"])
        self.assertEqual(self.client.get("/api/gkg/search?q=%25").get_json()["keys"], [])
        self.assertEqual(self.client.get("/api/gkg/search?q=_").get_json()["keys"], [])
        d = self.client.get("/api/gkg/key?key=P:joe biden&full=1").get_json()
        self.assertGreater(d["rates"]["r168"], 0)
        self.assertEqual(d["series"]["t"], times)
        self.assertEqual(d["entries"], [])
        co = dict(map(tuple, d["cooccurring"]["keys"]))
        self.assertIn("O:world bank", co)
        self.assertNotIn("P:joe biden", co)
        self.assertTrue(d["cooccurring"]["domains"])
        self.assertNotIn("series", self.client.get("/api/gkg/key?key=P:joe biden").get_json())

    def test_e1_waiting_then_ok(self):
        if os.path.exists(self.results):
            os.remove(self.results)
        self.assertEqual(self.client.get("/api/gkg/e1").get_json(), {"status": "waiting"})
        with open(self.results, "w", encoding="utf-8") as f:
            json.dump({"E1": {"k": 20, "thetas": [0.5], "rows": [{"window": "168h", "theta": None, "keys_mean": 10}]},
                       "meta": {"command": "python experiments/run_gkg_experiments.py",
                                "input": {"stream_sha256": "ab"}}}, f)
        r = self.client.get("/api/gkg/e1").get_json()
        self.assertEqual((r["status"], r["k"], r["stream_sha256"]), ("ok", 20, "ab"))
        self.assertEqual(r["rows"][0]["keys_mean"], 10)

    def test_page_has_v04_controls(self):
        html = self.client.get("/").get_data(as_text=True)
        for el in ("btnPlay", "btnStep", "speed", "preset", "customDate", "btnDemo", "btnReset", "timelineSvg",
                   "kpis", "tabs", "kinds", "rankView", "btnCsv", "rankBody", "raceChart", "compareChart", "burst",
                   "events", "eventFilter", "metrics", "agentCard", "askForm", "drawer", "lookupInput",
                   "settingsDlg", "cfgK", "cfgVariant", "helpDlg", "btnTheme", "btnShare"):
            self.assertIn(f'id="{el}"', html, el)
        self.assertIn("style.css", html)
        self.assertIn("gkg.js", html)

    def test_ask_returns_citations(self):
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "", **NO_LLM}):
            r = self.client.post("/api/gkg/ask", json={"question": "joe biden 왜 많이 나와?"}).get_json()
        cited = {int(x) for x in re.findall(r"\[#(\d+)\]", r["answer"])}
        self.assertEqual({c["idx"] for c in r["citations"]}, cited)
        self.assertTrue(cited)
        self.assertTrue(all(c["valid"] and c["url"].startswith("https://") for c in r["citations"]))
        self.assertEqual(self.client.post("/api/gkg/ask", json={"question": "x", "v": "nope"}).status_code, 400)


class LlmProviderTest(unittest.TestCase):
    """OpenAI-compatible provider via LLM_* (mocked HTTP), then UPSTAGE_*, then offline."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gkg_llm_")
        root = os.path.join(cls.tmp, "gdelt")
        write_fake_slim(root)
        cls.cache, cls.evid = os.path.join(cls.tmp, "c.sqlite"), os.path.join(cls.tmp, "e.sqlite")
        ensure_cache(CacheConfig(), root=root, path=cls.cache, evidence_path=cls.evid, progress=None)
        cls.reader = CacheReader(cls.cache, cls.evid)
        from app_gkg import create_app
        cls.client = create_app(cls.cache, cls.evid).test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    LLM = {"LLM_API_KEY": "sk-test-secret", "LLM_BASE_URL": "https://llm.example.com/v1/", "LLM_MODEL": "gpt-test",
           "UPSTAGE_API_KEY": "up-secret"}

    def recorder(self, idx):
        calls = []
        seq = iter([{"tool_calls": [{"id": "c1", "function": {"name": "get_key_detail",
                                                               "arguments": json.dumps({"key": "joe biden"})}}]},
                    {"content": f"요약 [#{idx}]"}])

        def _post(url, key, payload):
            calls.append((url, key, payload["model"]))
            return {"choices": [{"message": next(seq)}]}
        return calls, _post

    def idx(self):
        return self.reader.evidence("P:joe biden", self.reader.articles[self.reader.times[-1]], 8)["articles"][0]["idx"]

    def test_provider_order(self):
        with mock.patch.dict(os.environ, self.LLM):
            p = gkg_agent.provider()
            self.assertEqual((p["name"], p["base"], p["model"]), ("llm", "https://llm.example.com/v1", "gpt-test"))
        with mock.patch.dict(os.environ, {**self.LLM, "LLM_MODEL": ""}):
            p = gkg_agent.provider()
            self.assertEqual(p["name"], "solar")                     # partial LLM_* is skipped, with a note
            self.assertIn("LLM_MODEL", p["note"])
        with mock.patch.dict(os.environ, {**NO_LLM, "UPSTAGE_API_KEY": ""}):
            self.assertEqual(gkg_agent.provider()["name"], "offline")

    def test_llm_call_uses_llm_env(self):
        calls, post = self.recorder(self.idx())
        with mock.patch.dict(os.environ, self.LLM), mock.patch.object(gkg_agent, "_post", post):
            r = gkg_agent.ask(self.reader, "joe biden 왜?")
        self.assertEqual(r["mode"], "llm (gpt-test)")
        self.assertTrue(r["verified"])
        self.assertEqual({c[0] for c in calls}, {"https://llm.example.com/v1/chat/completions"})
        self.assertEqual({(c[1], c[2]) for c in calls}, {("sk-test-secret", "gpt-test")})
        self.assertNotIn("secret", json.dumps(r, ensure_ascii=False))

    def test_partial_llm_falls_back_to_solar_then_offline(self):
        calls, post = self.recorder(self.idx())
        with mock.patch.dict(os.environ, {**self.LLM, "LLM_BASE_URL": ""}), mock.patch.object(gkg_agent, "_post", post):
            r = gkg_agent.ask(self.reader, "joe biden 왜?")
        self.assertTrue(r["mode"].startswith("solar"))
        self.assertTrue(calls[0][0].startswith("https://api.upstage.ai"))
        self.assertEqual(calls[0][1], "up-secret")
        self.assertIn("LLM_BASE_URL", r["warning"])
        with mock.patch.dict(os.environ, {**NO_LLM, "LLM_API_KEY": "sk-x", "UPSTAGE_API_KEY": ""}),                 mock.patch.object(gkg_agent, "_post", side_effect=AssertionError("no network")):
            r = gkg_agent.ask(self.reader, "joe biden 왜?")
        self.assertEqual(r["mode"], "offline")
        self.assertIn("LLM_BASE_URL", r["warning"])

    def test_api_failure_falls_back_offline(self):
        import urllib.error

        def boom(url, key, payload):
            raise urllib.error.URLError("refused")
        with mock.patch.dict(os.environ, self.LLM), mock.patch.object(gkg_agent, "_post", boom):
            r = gkg_agent.ask(self.reader, "joe biden 왜?")
        self.assertEqual(r["mode"], "offline-fallback")
        self.assertIn("LLM API 호출 실패", r["warning"])

    def test_info_never_exposes_keys(self):
        with mock.patch.dict(os.environ, {**self.LLM, "LLM_BASE_URL": "https://user:pw@llm.example.com/v1"}):
            body = self.client.get("/api/gkg/info").get_data(as_text=True)
            info = json.loads(body)
        self.assertEqual(info["llm"], {"name": "llm", "label": "LLM", "model": "gpt-test",
                                       "host": "https://llm.example.com", "note": None, "enabled": True})
        for secret in ("sk-test-secret", "up-secret", "user:pw"):
            self.assertNotIn(secret, body)


if __name__ == "__main__":
    unittest.main()
