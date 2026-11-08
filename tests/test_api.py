"""Flask API tests with a synthetic stream (no dataset download needed)."""
import os
import unittest
from unittest import mock

from app import create_app
from tests.test_agent import make_articles


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(make_articles(), start_date=None)
        self.client = self.app.test_client()
        self.engine = self.app.config["ENGINE"]

    def test_invalid_reset_rejected_without_changing_state(self):
        self.client.post("/api/step", json={"n": 100})
        before = (self.engine.pos, dict(vars(self.engine.config)))
        for body in ({"snapshot_every": 0}, {"warmup_factor": -3}, {"k": "abc"}, {"k": 2.5},
                     {"threshold": 2}, {"c": "nan"}, {"max_tracked": 1}):
            res = self.client.post("/api/reset", json=body)
            self.assertEqual(res.status_code, 400, body)
            self.assertIn("error", res.get_json())
        self.assertEqual((self.engine.pos, dict(vars(self.engine.config))), before)
        # engine still works afterwards (the old bug: snapshot_every=0 -> ZeroDivisionError on step)
        self.assertEqual(self.client.post("/api/step", json={"n": 1}).status_code, 200)

    def test_reset_step_keyword_flow(self):
        res = self.client.post("/api/reset", json={"k": 5, "snapshot_every": 10, "c": 0.01})
        self.assertEqual(res.status_code, 200)
        st = self.client.post("/api/step", json={"n": 300}).get_json()
        self.assertEqual(st["clock"]["processed"], 300)
        self.assertEqual(len(st["ranking"]), 5)
        kw = st["ranking"][0]["keyword"]
        d = self.client.get(f"/api/keyword/{kw}").get_json()
        self.assertEqual(d["keyword"], kw)
        self.assertTrue(d["related"])
        self.assertIn("half_life", st["clock"])

    def test_track_refusal_returns_409(self):
        self.client.post("/api/reset", json={"k": 2, "max_tracked": 4, "snapshot_every": 10})
        codes = [self.client.post("/api/track", json={"keyword": w}).status_code
                 for w in ("festival", "senate", "weather")]
        self.assertEqual(codes, [200, 200, 409])               # pins capped at max_tracked // 2

    def test_ask_offline(self):
        self.client.post("/api/step", json={"n": 300})
        with mock.patch.dict(os.environ, {"UPSTAGE_API_KEY": ""}):
            res = self.client.post("/api/ask", json={"question": "festival 왜?"}).get_json()
        self.assertEqual(res["mode"], "offline")
        self.assertTrue(res["citations"])

    def test_demo_endpoint(self):
        res = self.client.post("/api/demo")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["demo"]["keyword"], "scalia")


if __name__ == "__main__":
    unittest.main()
