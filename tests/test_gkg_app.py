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
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": ""}):
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
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "k"}), \
                mock.patch.object(gkg_agent, "_post", fake(good)):
            r = gkg_agent.ask(reader, "joe biden 왜?")
        self.assertTrue(r["mode"].startswith("solar"))
        self.assertTrue(r["verified"])
        bad = [call, {"content": "요약 [#999999]"}, {"content": "다시 [#999998]"}]
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": "k"}), \
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

    def test_kind_filter_applies(self):
        self.assertEqual(self.client.get("/api/gkg/snapshot?tab=trend&kind=O").get_json()["rows"], [])
        rows = self.client.get("/api/gkg/snapshot?tab=trend&kind=P").get_json()["rows"]
        self.assertEqual(len(rows), 7)


if __name__ == "__main__":
    unittest.main()
