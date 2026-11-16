"""Trend Pulse dashboard server for the GDELT GAL submission replay.

    python app_gdelt.py          -> http://127.0.0.1:5001

Same route/API shape as app.py (the HuffPost dashboard) but the engine is the
GAL replay (GalEngine): the clock is the batch observation time, the ranking is
the 7-day decaying window, and /api/keyword/<w> answers with the same reference
time as the ranking (72h numerators/denominators, outlets, publisher countries,
supporting articles — never articles beyond the current replay position).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))

from trendpulse.gal import load_articles                      # noqa: E402
from trendpulse.gal_agent import ask_gal                      # noqa: E402
from trendpulse.gal_engine import GalConfig, GalEngine        # noqa: E402

VERSION = "gdelt-submission/1.0"
# the collected week holds ~1,570 observation batches (~224/day): skip the first
# 6 days (1344) so the 7-day window and the 144h surge comparison are both usable
DEFAULT_START_BATCH = 1344


def create_gal_app(articles=None, start_batches: int = DEFAULT_START_BATCH) -> Flask:
    if articles is None:
        articles = load_articles()
    manifest_path = ROOT / "data" / "processed" / "gdelt" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    gaps = []
    for g in manifest.get("collection_gaps", []):
        from trendpulse.gal import parse_iso
        gaps.append({"start": g["start"], "end": g["end"],
                     "start_ts": parse_iso(g["start"].replace("+00:00", "")),
                     "end_ts": parse_iso(g["end"].replace("+00:00", ""))})
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.json.ensure_ascii = False
    engine = GalEngine(articles, GalConfig(gaps=gaps))
    engine.step(start_batches)
    lock = threading.Lock()
    app.config["ENGINE"] = engine

    def bad(msg, code=400):
        return jsonify({"error": msg}), code

    @app.get("/")
    def index():
        return render_template("gal.html", version=VERSION)

    @app.get("/api/state")
    def state():
        with lock:
            return jsonify(engine.state())

    @app.post("/api/step")
    def step():
        body = request.get_json(silent=True) or {}
        try:
            n = int(body.get("batches", body.get("n", 1)))
        except (TypeError, ValueError):
            return bad("batches must be an integer")
        n = max(1, min(n, 2000))
        with lock:
            engine.step(n)
            return jsonify(engine.state())

    @app.post("/api/clock")
    def clock():
        """Advance the reference time without new articles (decay + expiry apply)."""
        body = request.get_json(silent=True) or {}
        iso = str(body.get("t", "")).strip()
        try:
            with lock:
                return jsonify(engine.advance_clock(iso))
        except ValueError as exc:
            return bad(str(exc))

    @app.get("/api/keyword/<path:word>")
    def keyword(word: str):
        with lock:
            return jsonify(engine.keyword_detail(word))

    @app.post("/api/track")
    def track():
        body = request.get_json(silent=True) or {}
        w = str(body.get("keyword", "")).strip().lower()
        if not w:
            return bad("keyword required")
        with lock:
            ok, msg = engine.track(w, pinned=True)
            detail = engine.keyword_detail(w)
        return jsonify({**detail, "track_ok": ok, "track_message": msg}), (200 if ok else 409)

    @app.post("/api/ask")
    def ask():
        body = request.get_json(silent=True) or {}
        q = str(body.get("question", "")).strip()
        if not q:
            return bad("question required")
        return jsonify(ask_gal(engine, q, body.get("keyword"), lock=lock))

    @app.get("/api/info")
    def info():
        return jsonify({
            "version": VERSION,
            "articles": len(articles),
            "input_sha256": manifest.get("input_sha256"),
            "window": manifest.get("window"),
            "collection_gaps": manifest.get("collection_gaps"),
            "solar_enabled": bool(os.environ.get("UPSTAGE_API_KEY")),
            "solar_model": os.environ.get("UPSTAGE_MODEL", "solar-pro2"),
        })

    return app


if __name__ == "__main__":
    application = create_gal_app()
    port = int(os.environ.get("PORT", "5001"))
    print(f"Trend Pulse GDELT GAL: http://127.0.0.1:{port}")
    application.run(host="127.0.0.1", port=port, debug=False, threaded=True)
