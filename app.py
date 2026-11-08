"""Trend Pulse dashboard server.

    python app.py            -> http://127.0.0.1:5000
    set UPSTAGE_API_KEY=...  -> enables the Solar agent (offline summary otherwise)
"""
from __future__ import annotations

import os
import threading
from dataclasses import asdict, fields

from flask import Flask, jsonify, render_template, request

from trendpulse.agent import ask
from trendpulse.data import Article
from trendpulse.engine import EngineConfig, TrendEngine

DEFAULT_START = "2016-01-01"
# Demo scene used in the presentation: Justice Scalia died on 2016-02-13.
DEMO = {"start_date": "2016-01-01", "warm_steps": 4000, "last_step": 100,
        "keyword": "scalia", "question": "scalia가 왜 순위에 올랐어?"}

_FLOAT_FIELDS = {"c", "threshold", "warmup_factor", "absorb_ratio", "half_life_days", "burst_ratio", "burst_min"}
_BOOL_FIELDS = {"phrases"}
_STR_FIELDS = {"decay_unit"}


def parse_config(body: dict, current: EngineConfig) -> EngineConfig:
    """Build and validate a new config without touching the running engine."""
    names = {f.name for f in fields(EngineConfig)}
    cfg = asdict(current)
    for key, val in body.items():
        if key not in names or val is None or val == "":
            continue
        if key in _BOOL_FIELDS:
            if isinstance(val, bool):
                cfg[key] = val
            elif str(val).lower() in ("true", "1", "on"):
                cfg[key] = True
            elif str(val).lower() in ("false", "0", "off"):
                cfg[key] = False
            else:
                raise ValueError(f"{key} must be true or false")
        elif key in _STR_FIELDS:
            cfg[key] = str(val)
        elif key in _FLOAT_FIELDS:
            cfg[key] = float(val)
        else:
            num = float(val)
            if not num.is_integer():
                raise ValueError(f"{key} must be an integer")
            cfg[key] = int(num)
    return EngineConfig(**cfg).validate()


def create_app(articles: list[Article] | None = None, start_date: str | None = DEFAULT_START) -> Flask:
    if articles is None:
        from trendpulse.data import load_stream
        articles = load_stream()
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.json.ensure_ascii = False
    engine = TrendEngine(articles)
    engine.reset(start_date=start_date)
    lock = threading.Lock()
    app.config["ENGINE"] = engine

    def bad(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        with lock:
            return jsonify(engine.state())

    @app.post("/api/step")
    def step():
        body = request.get_json(silent=True) or {}
        try:
            n = int(body.get("n", 50))
        except (TypeError, ValueError):
            return bad("n must be an integer")
        n = max(1, min(n, 20000))
        with lock:
            engine.step(n)
            return jsonify(engine.state())

    @app.post("/api/reset")
    def reset():
        body = request.get_json(silent=True) or {}
        with lock:
            try:
                config = parse_config(body, engine.config)
            except (TypeError, ValueError, OverflowError) as exc:
                return bad(str(exc))          # engine state untouched
            start = str(body.get("start_date") or DEFAULT_START)
            engine.reset(config, start_date=start)
            return jsonify(engine.state())

    @app.post("/api/demo")
    def demo():
        """Restore the presentation scene: default settings, 2016-02-16, 'scalia'."""
        with lock:
            engine.reset(EngineConfig(), start_date=DEMO["start_date"])
            engine.step(DEMO["warm_steps"])
            engine.step(DEMO["last_step"])      # small last step -> realistic rank changes
            return jsonify({**engine.state(), "demo": DEMO})

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
    def ask_agent():
        body = request.get_json(silent=True) or {}
        q = str(body.get("question", "")).strip()
        if not q:
            return bad("question required")
        # The agent takes the lock per tool call, so slow Solar calls don't block the stream.
        return jsonify(ask(engine, q, body.get("keyword"), lock=lock))

    @app.get("/api/info")
    def info():
        return jsonify({
            "articles": len(articles), "first_date": articles[0].date, "last_date": articles[-1].date,
            "solar_enabled": bool(os.environ.get("UPSTAGE_API_KEY")),
            "solar_model": os.environ.get("UPSTAGE_MODEL", "solar-pro2"),
            "demo": DEMO,
        })

    return app


if __name__ == "__main__":
    application = create_app()
    port = int(os.environ.get("PORT", "5000"))
    n = len(application.config["ENGINE"].articles)
    print(f"Trend Pulse: http://127.0.0.1:{port}  (articles: {n:,})")
    application.run(host="127.0.0.1", port=port, debug=False, threaded=True)
