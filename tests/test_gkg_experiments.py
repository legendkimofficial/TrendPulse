"""gdelt-gkg P4: experiment runner pieces on a small fake stream."""
import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments"))
import run_gkg_experiments as rx  # noqa: E402

T0 = datetime(2024, 9, 15, tzinfo=timezone.utc)


def fake_root(tmp, days=3):
    root = os.path.join(tmp, "gdelt")
    t, i = T0, 0
    while t < T0 + timedelta(days=days):
        d = os.path.join(root, "slim", t.strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        with gzip.open(os.path.join(d, t.strftime("%H%M") + ".jsonl.gz"), "wt", encoding="utf-8") as f:
            for j in range(5):
                persons = [f"Person {j % 3}"] + (["Han Kang"] if t >= T0 + timedelta(days=2) and j < 3 else [])
                f.write(json.dumps({"id": f"x{i}", "ts": t.strftime("%Y%m%d%H%M%S"), "domain": f"d{j}.com",
                                    "url": f"u{i}", "title": "t", "persons": persons, "orgs": [],
                                    "places": [], "themes": [], "tone": None}) + "\n")
                i += 1
        t += timedelta(minutes=15)
    return root


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gkg_rx_")
        self.root = fake_root(self.tmp)
        self.args = argparse.Namespace(start="2024-09-15", end="2024-09-17", eval_from="2024-09-16",
                                       root=self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_run_e1_e4_and_hash(self):
        m = rx.MainRun(self.args, {"E1", "E4"}, {})
        m.run(3 * 96 * 5)
        rows = rx.summarize_e1(m)
        self.assertEqual(len(rows), 15)                   # 3 windows x (exact + 4 thetas)
        exact = [r for r in rows if r["theta"] is None]
        self.assertTrue(all(r["overlap_mean"] == 1.0 and r["rel_err_max"] == 0.0 for r in exact))
        # evaluation every 6 hours from 2024-09-16 00:00: 8 blocks
        self.assertTrue(all(r["eval_times"] == 8 for r in rows))
        for r in rows:                                     # pruning never over-estimates
            self.assertLessEqual(r["keys_mean"], exact[0]["keys_mean"])
        self.assertEqual(m.articles, 3 * 96 * 5)
        e4 = rx.summarize_e4(m)
        self.assertTrue(e4["keys"])
        self.assertTrue(e4["skipped"])                     # fewer than 100 keys in the fake stream

    def test_e2_capture_uses_new_issue_top20(self):
        m = rx.MainRun(self.args, {"E2"}, {"events": [
            {"id": "han_kang", "name": "x", "first_report_utc": "2024-09-17T00:00:00Z", "match": ["han kang"],
             "types": ["P"], "verified": True}]})

        class Eng:
            detection = {"new": {"status": "ok"}}
            new_ranked = [{"key": "O:han kang foundation", "lift": 2.0}, {"key": "P:han kang", "lift": 1.5}]
        t = int((T0 + timedelta(days=2, hours=3)).timestamp())
        m.e2_capture("exact", Eng(), t)
        out = rx.summarize_e2(m, m.events)["events"][0]
        self.assertEqual(out["exact"]["key"], "P:han kang")   # type filter skips the O: key
        self.assertEqual(out["exact"]["rank"], 2)
        self.assertEqual(out["exact"]["delay_h"], 3.0)
        self.assertTrue(out["theta=0.5"]["missed"])

    def test_e5_drop_is_seeded(self):
        ref_run = rx.MainRun(self.args, {"E5"}, {})
        ref_run.run(3 * 96 * 5)
        a = rx.e5_run(self.args, 0.05, ref_run.ref_top20, 3 * 96 * 5)
        b = rx.e5_run(self.args, 0.05, ref_run.ref_top20, 3 * 96 * 5)
        self.assertEqual(a["dropped_sha256"], b["dropped_sha256"])
        self.assertEqual(a["slots_dropped"], round(3 * 96 * 0.05))
        self.assertEqual(a["articles"], (3 * 96 - a["slots_dropped"]) * 5)
        self.assertEqual(a["eval_times"], 8)

    def test_top_section_goes_under_title_and_is_replaced(self):
        p = os.path.join(self.tmp, "summary.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Title\n\n## Old section\ntext\n")
        rx.write_top_section(p, "x", "## New\none")
        rx.write_top_section(p, "x", "## New\ntwo")
        with open(p, encoding="utf-8") as f:
            s = f.read()
        self.assertTrue(s.startswith("# Title\n\n<!-- x:begin -->\n## New\ntwo\n<!-- x:end -->"))
        self.assertIn("## Old section", s)
        self.assertEqual(s.count("x:begin"), 1)

    def test_events_warning(self):
        warnings = []
        ev = rx.load_events(warnings)
        self.assertIn("events", ev)
        self.assertTrue(any("us_election" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
