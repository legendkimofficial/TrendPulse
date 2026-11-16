"""gdelt-gkg: slim stream reader, key rules and input manifest (fake slim files in a temp dir)."""
import csv
import gzip
import json
import os
import shutil
import tempfile
import types
import unittest

from trendpulse.gkg import (GkgConfig, StreamStats, U64Set, build_manifest, is_self_org,
                            iter_articles, make_keys, outlet_name)


def rec(i, ts, url, domain="example.com", persons=(), orgs=(), places=(), title="t", tone=1.5):
    return {"id": f"x-{i}", "ts": ts, "domain": domain, "url": url, "title": title,
            "persons": list(persons), "orgs": list(orgs), "places": [list(p) for p in places],
            "themes": ["T"], "tone": tone}


def write_slot(root, day, hhmm, records):
    d = os.path.join(root, "slim", day)
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, f"{hhmm}.jsonl.gz"), "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def mark_missing(root, day, hhmm):
    d = os.path.join(root, "slim", day)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{hhmm}.missing"), "w").close()


class FakeSlim(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gkg_test_")
        # written out of order on purpose: the reader must sort folders and files
        write_slot(self.root, "2024-10-02", "0000", [
            rec(5, "20241002000000", "https://a.com/5", persons=["Kamala  Harris"]),
            rec(6, "20241002000000", "https://a.com/1", persons=["dup"]),           # duplicate URL
        ])
        write_slot(self.root, "2024-10-01", "0015", [
            rec(3, "20241001001500", "https://a.com/3"),
            rec(4, "20241001001500", "https://a.com/4"),
        ])
        write_slot(self.root, "2024-10-01", "0000", [
            rec(1, "20241001000000", "https://a.com/1", domain="energycentral.com",
                persons=["Joe Biden", "joe  biden"],
                orgs=["Energy Central", "energy central power industry network", "NATO"],
                places=[["Ukraine", 1, "UP"], ["Kyiv, Kyyiv, Ukraine", 4, "UP"], ["Texas, United States", 2, "US"],
                        ["Ukraine", 1, "UP"]]),
            rec(2, "20241001000000", "https://a.com/2", tone=None),
        ])
        mark_missing(self.root, "2024-10-01", "0030")
        with open(os.path.join(self.root, "download_log.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["file", "status"])
            w.writerow(["20241001000000", "ok"])
            w.writerow(["20241001003000", "missing"])
            w.writerow(["20241001004500", "error"])
            w.writerow(["20241001010000", "missing"])   # log only, no .missing file

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, **kw):
        st = StreamStats()
        arts = list(iter_articles(root=self.root, stats=st, **kw))
        return arts, st


class StreamTest(FakeSlim):
    def test_is_generator(self):
        g = iter_articles(root=self.root)
        self.assertIsInstance(g, types.GeneratorType)
        self.assertEqual(next(g).url, "https://a.com/1")

    def test_time_order_and_idx(self):
        arts, st = self.read()
        self.assertEqual([a.url for a in arts],
                         ["https://a.com/1", "https://a.com/2", "https://a.com/3", "https://a.com/4", "https://a.com/5"])
        self.assertEqual([a.idx for a in arts], list(range(5)))
        self.assertEqual(arts[0].ts, 1727740800)            # 2024-10-01T00:00:00Z
        self.assertEqual(arts[2].ts - arts[0].ts, 15 * 60)
        self.assertEqual(st.ts_out_of_order, 0)
        self.assertEqual(st.per_day, {"2024-10-01": 4, "2024-10-02": 1})

    def test_start_end_inclusive(self):
        arts, _ = self.read(start="2024-10-02", end="2024-10-02")
        # a.com/1 first appears on 10-01, which is outside this range, so here it is kept
        self.assertEqual([a.url for a in arts], ["https://a.com/5", "https://a.com/1"])
        self.assertEqual(arts[0].idx, 0)
        arts, _ = self.read(end="2024-10-01")
        self.assertEqual(len(arts), 4)

    def test_keys_countries_and_self_name(self):
        arts, st = self.read()
        a = arts[0]
        self.assertEqual(a.keys, ("P:joe biden", "O:nato", "L:kyiv, kyyiv, ukraine", "L:texas, united states"))
        self.assertEqual(a.countries, ("ukraine",))
        self.assertEqual(st.self_orgs_dropped, 2)
        self.assertEqual(arts[4].keys, ("P:kamala harris",))
        self.assertIsNone(arts[1].tone)
        self.assertEqual(arts[0].tone, 1.5)

    def test_include_countries(self):
        st = StreamStats()
        a = next(iter_articles(root=self.root, stats=st, config=GkgConfig(include_countries=True)))
        self.assertIn("L:ukraine", a.keys)
        self.assertEqual(a.countries, ("ukraine",))

    def test_duplicate_url_first_wins(self):
        arts, st = self.read()
        self.assertEqual(st.duplicate_urls, 1)
        self.assertEqual(sum(a.url == "https://a.com/1" for a in arts), 1)
        self.assertEqual(arts[0].keys[0], "P:joe biden")   # the first one, not "dup"
        arts, st = self.read(config=GkgConfig(dedup_urls=False))
        self.assertEqual(len(arts), 6)

    def test_missing_and_bad_files_are_skipped(self):
        d = os.path.join(self.root, "slim", "2024-10-01")
        open(os.path.join(d, "0100.jsonl.gz.part"), "w").close()
        with gzip.open(os.path.join(d, "0045.jsonl.gz"), "wt", encoding="utf-8") as f:
            f.write("{not json\n\n")
        arts, st = self.read()
        self.assertEqual(len(arts), 5)
        self.assertEqual(st.bad_lines, 1)
        self.assertEqual(st.files_per_day["2024-10-01"], ["0000", "0015", "0045"])


class KeyRuleTest(unittest.TestCase):
    def test_outlet_name(self):
        self.assertEqual(outlet_name("energycentral.com"), "energycentral")
        self.assertEqual(outlet_name("www.bbc.co.uk"), "bbc")
        self.assertEqual(outlet_name("news.yahoo.com"), "yahoo")
        self.assertEqual(outlet_name("abc.net.au"), "abc")
        self.assertEqual(outlet_name("fox-news.com"), "foxnews")

    def test_is_self_org(self):
        self.assertTrue(is_self_org("energy central", "energycentral"))
        self.assertTrue(is_self_org("energy central power industry network", "energycentral"))
        self.assertFalse(is_self_org("nato", "energycentral"))
        self.assertTrue(is_self_org("abc news", "abc"))       # short outlet: word boundary
        self.assertFalse(is_self_org("abcam", "abc"))
        self.assertFalse(is_self_org("anything", ""))

    def test_make_keys_ignores_bad_places(self):
        keys, countries, dropped = make_keys(
            {"domain": "x.com", "persons": [" "], "orgs": [], "places": [["", 4, "US"], ["Paris", "bad", "FR"], ["Lyon"]]},
            GkgConfig())
        self.assertEqual((keys, countries, dropped), ((), (), 0))

    def test_u64set(self):
        s = U64Set(4)
        for i in range(1, 2000):
            self.assertTrue(s.add(i * 0x9E3779B97F4A7C15 % (1 << 64)))
        self.assertFalse(s.add(1 * 0x9E3779B97F4A7C15 % (1 << 64)))
        self.assertEqual(len(s), 1999)
        self.assertTrue(s.add(0))
        self.assertIn(0, s)


class ManifestTest(FakeSlim):
    def test_manifest_counts_and_missing(self):
        out = os.path.join(self.root, "out", "input_manifest.json")
        m = build_manifest("2024-10-01", "2024-10-03", root=self.root, out_path=out, command="test")
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["hashes"], m["hashes"])
        self.assertEqual(m["totals"]["articles"], 5)
        self.assertEqual(m["totals"]["duplicate_urls_removed"], 1)
        self.assertEqual(m["totals"]["self_orgs_dropped"], 2)
        self.assertEqual(m["keys"]["distinct"], {"persons": 2, "orgs": 1, "places": 2, "countries": 1})
        days = {d["day"]: d for d in m["per_day"]}
        self.assertEqual(set(days), {"2024-10-01", "2024-10-02", "2024-10-03"})
        d1 = days["2024-10-01"]
        self.assertEqual(d1["articles"], 4)
        self.assertEqual(d1["files_read"], 2)
        self.assertEqual(d1["missing_on_server"], ["0030", "0100"])
        self.assertEqual(d1["log_error_slots"], ["0045"])
        self.assertEqual(len(d1["not_downloaded"]), 96 - 2 - 2)
        self.assertEqual(days["2024-10-03"]["files_read"], 0)
        self.assertEqual(len(days["2024-10-03"]["not_downloaded"]), 96)
        self.assertEqual(m["slots"], {"expected": 288, "read": 3, "missing_on_server": 2, "not_downloaded": 283})
        self.assertTrue(any("no .missing file" in x for x in m["log_mismatch"]))

    def test_hash_reproducible_and_sensitive(self):
        m1 = build_manifest("2024-10-01", "2024-10-02", root=self.root, out_path=None)
        m2 = build_manifest("2024-10-01", "2024-10-02", root=self.root, out_path=None)
        self.assertEqual(m1["hashes"]["stream_sha256"], m2["hashes"]["stream_sha256"])
        self.assertEqual(m1["hashes"]["raw_files_sha256"], m2["hashes"]["raw_files_sha256"])
        # a config change changes the normalized stream
        m3 = build_manifest("2024-10-01", "2024-10-02", root=self.root, out_path=None,
                            config=GkgConfig(include_countries=True))
        self.assertNotEqual(m1["hashes"]["stream_sha256"], m3["hashes"]["stream_sha256"])
        # a data change changes both hashes
        write_slot(self.root, "2024-10-02", "0015", [rec(9, "20241002001500", "https://a.com/9")])
        m4 = build_manifest("2024-10-01", "2024-10-02", root=self.root, out_path=None)
        self.assertNotEqual(m1["hashes"]["stream_sha256"], m4["hashes"]["stream_sha256"])
        self.assertNotEqual(m1["hashes"]["raw_files_sha256"], m4["hashes"]["raw_files_sha256"])


if __name__ == "__main__":
    unittest.main()
