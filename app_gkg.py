"""Trend Pulse GKG dashboard (gdelt-gkg branch). The HuffPost app (app.py) is unchanged.

    python app_gkg.py                         -> http://127.0.0.1:5002
    python app_gkg.py --start 2024-09-15 --end 2024-11-14 --theta 0.5
    python app_gkg.py --rebuild               (force a new full replay)
    set UPSTAGE_API_KEY=...                   -> Solar agent (offline summary otherwise)

On start the whole stream is replayed once and the snapshots are cached in
data/gdelt/processed/gkg_cache.sqlite (reused while the input files, settings and code
are unchanged). The pages only read that cache.
"""
from __future__ import annotations

import argparse
import os

from flask import Flask, jsonify, render_template, request

from trendpulse.gkg import DEFAULT_ROOT
from trendpulse.gkg_agent import ask as gkg_ask
from trendpulse.gkg_cache import (CACHE_PATH, EVIDENCE_PATH, KIND_KO, CacheConfig, CacheReader,
                                  ensure_cache)

VERSION = "gkg-0.1"
PORT = 5002
TABS = ("major", "new", "trend")
KINDS = ("all", "P", "O", "L")


def create_app(cache_path: str = CACHE_PATH, evidence_path: str = EVIDENCE_PATH) -> Flask:
    reader = CacheReader(cache_path, evidence_path)
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.json.ensure_ascii = False
    app.config["READER"] = reader

    def bad(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    def t_arg():
        raw = request.args.get("t")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except ValueError:
            raise ValueError("t must be epoch seconds")

    @app.get("/")
    def index():
        return render_template("gkg.html", version=VERSION)

    @app.get("/api/gkg/info")
    def info():
        m = reader.meta
        return jsonify({
            "version": VERSION, "range": m["range"], "articles": m["articles"],
            "snapshots": reader.times, "config": m["config"], "engine_config": m["engine_config"],
            "built_utc": m["built_utc"], "build_seconds": m["seconds"], "peak_rss_mb": m["peak_rss_mb"],
            "fingerprint": m["fingerprint"], "kinds": KIND_KO,
            "solar_enabled": bool(os.environ.get("UPSTAGE_API_KEY")),
        })

    @app.get("/api/gkg/snapshot")
    def snapshot():
        try:
            t = t_arg()
        except ValueError as exc:
            return bad(str(exc))
        tab = request.args.get("tab", "major")
        kind = request.args.get("kind", "all")
        if tab not in TABS:
            return bad(f"tab must be one of {TABS}")
        if kind not in KINDS:
            return bad(f"kind must be one of {KINDS}")
        s = reader.snapshot(t)
        tb = s["tabs"][tab]
        return jsonify({"t": s["t"], "articles": s["articles"], "variant": s["variant"], "tab": tab,
                        "kind": kind, "window": tb["window"], "status": tb["status"],
                        "status_ko": tb.get("label_ko"), "qualified": tb.get("qualified"),
                        "elapsed_days": tb.get("elapsed_days"), "rows": tb["rows"][kind],
                        "gaps": s["gaps"], "countries": s["countries"], "events": s["events"]})

    @app.get("/api/gkg/key")
    def key_detail():
        try:
            t = t_arg()
        except ValueError as exc:
            return bad(str(exc))
        key = request.args.get("key", "")
        if len(key) < 3 or key[1] != ":" or key[0] not in "POL":
            return bad("key must look like P:name, O:name or L:name")
        s = reader.snapshot(t)
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 50))
        except ValueError:
            return bad("limit must be an integer")
        return jsonify({"t": s["t"], "key": key, "row": reader.find_row(s, key),
                        "evidence": reader.evidence(key, s["articles"], limit=limit)})

    @app.post("/api/gkg/ask")
    def ask_agent():
        body = request.get_json(silent=True) or {}
        q = str(body.get("question", "")).strip()
        if not q:
            return bad("question required")
        t = body.get("t")
        try:
            t = int(t) if t not in (None, "") else None
        except (TypeError, ValueError):
            return bad("t must be epoch seconds")
        return jsonify(gkg_ask(reader, q, t, body.get("key")))

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="first day YYYY-MM-DD (default: first downloaded day)")
    ap.add_argument("--end", help="last day YYYY-MM-DD (default: last downloaded day)")
    ap.add_argument("--theta", type=float, default=None, help="deletion threshold (default: exact)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", PORT)))
    args = ap.parse_args()
    cfg = CacheConfig(start=args.start, end=args.end, theta=args.theta)
    ensure_cache(cfg, root=args.root, rebuild=args.rebuild)
    app = create_app()
    print(f"Trend Pulse GKG: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
