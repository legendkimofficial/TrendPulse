"""Trend Pulse GKG dashboard (gdelt-gkg branch). The HuffPost app (app.py) is unchanged.

    python app_gkg.py                         -> http://127.0.0.1:5002
    python app_gkg.py --start 2024-09-15 --end 2024-11-14
    python app_gkg.py --rebuild               (force a new full replay)
    python scripts/gkg_app_cache.py --start 2024-09-15 --end 2024-10-14 --theta 0.5
                                              (adds a θ variant the settings dialog can switch to)
    set LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=...   -> any OpenAI-compatible API
    set UPSTAGE_API_KEY=...                   -> Solar agent (when LLM_* is not fully set)
                                              (neither: offline summary; keys only from the environment)

On start the exact cache for the range is reused or rebuilt (one full replay) in
data/gdelt/processed/gkg_cache.sqlite. Every cache file found there (gkg_cache.sqlite,
gkg_cache_theta<θ>.sqlite) is offered as a variant. The pages only read the caches.
The layout and interactions follow the HuffPost v0.4 screen (web/templates/index.html);
the GKG copies are web/templates/gkg.html, web/static/gkg.js and web/static/gkg.css.
"""
from __future__ import annotations

import argparse
import json
import os

from flask import Flask, jsonify, render_template, request

from trendpulse.gkg import DEFAULT_ROOT, REPO_ROOT
from trendpulse.gkg_agent import ask as gkg_ask
from trendpulse.gkg_agent import public_provider
from trendpulse.gkg_cache import KIND_KO, CacheConfig, CacheReader, discover_variants, ensure_cache
from trendpulse.gkg_engine import variant_name

VERSION = "gkg-0.4"
PORT = 5002
TABS = ("major", "new", "trend")
KINDS = ("all", "P", "O", "L")
GROUPS = ("rising", "cooling", "steady")
RESULTS_JSON = os.path.join(REPO_ROOT, "experiments", "results", "gkg", "results.json")
EVENTS_JSON = os.path.join(REPO_ROOT, "experiments", "gkg_events.json")
SPARK_N = 28            # sparkline / rank-race length in stored snapshots (7 days)


def load_event_catalog(path: str = EVENTS_JSON) -> list[dict]:
    """Presets and timeline marks from the fixed event list (read only)."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        ev = json.load(f)
    out = []
    for group in ("warmup_reference_only", "events"):
        for e in ev.get(group, []):
            out.append({"id": e["id"], "name": e["name"], "time": e["first_report_utc"],
                        "match": e.get("match", []), "types": e.get("types", []),
                        "verified": e.get("verified"), "group": "warmup" if group != "events" else "E2"})
    return sorted(out, key=lambda e: e["time"])


def load_e1(path: str = RESULTS_JSON) -> dict:
    """E1 (θ vs memory and Top-20 overlap) from the experiment results, or a waiting marker."""
    if not os.path.exists(path):
        return {"status": "waiting"}
    try:
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    if "E1" not in res:
        return {"status": "waiting"}
    meta = res.get("meta", {})
    return {"status": "ok", "k": res["E1"].get("k"), "thetas": res["E1"].get("thetas"), "rows": res["E1"]["rows"],
            "command": meta.get("command"), "range": meta.get("range"), "eval_from": meta.get("eval_from"),
            "created_utc": meta.get("created_utc"),
            "stream_sha256": (meta.get("input") or {}).get("stream_sha256")}


def create_app(cache_path: str | None = None, evidence_path: str | None = None, *,
               variants: dict[str, tuple[str, str]] | None = None, default_variant: str | None = None,
               results_path: str = RESULTS_JSON, events_path: str = EVENTS_JSON) -> Flask:
    if variants is None:
        if cache_path is not None:
            variants = {"exact": (cache_path, evidence_path)}
        else:
            variants = discover_variants()
    readers: dict[str, CacheReader] = {}
    for name, (c, e) in variants.items():
        if os.path.exists(c):
            r = CacheReader(c, e)
            readers[variant_name(r.meta["config"].get("theta"))] = r
    if not readers:
        raise FileNotFoundError("no dashboard cache; run python app_gkg.py (builds it) first")
    default = default_variant if default_variant in readers else ("exact" if "exact" in readers else next(iter(readers)))
    catalog = load_event_catalog(events_path)

    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.json.ensure_ascii = False
    app.config["READER"] = readers[default]
    app.config["READERS"] = readers

    def bad(msg: str, code: int = 400):
        return jsonify({"error": msg}), code

    class BadRequest(ValueError):
        pass

    @app.errorhandler(BadRequest)
    def _bad(exc):
        return bad(str(exc))

    def reader_arg() -> tuple[str, CacheReader]:
        v = request.args.get("v") or default
        if v not in readers:
            raise BadRequest(f"v must be one of {sorted(readers)}")
        return v, readers[v]

    def t_arg():
        raw = request.args.get("t")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except ValueError:
            raise BadRequest("t must be epoch seconds")

    def tab_kind():
        tab = request.args.get("tab", "major")
        kind = request.args.get("kind", "all")
        if tab not in TABS:
            raise BadRequest(f"tab must be one of {TABS}")
        if kind not in KINDS:
            raise BadRequest(f"kind must be one of {KINDS}")
        return tab, kind

    def key_arg(raw: str) -> str:
        if len(raw) < 3 or raw[1] != ":" or raw[0] not in "POL":
            raise BadRequest("key must look like P:name, O:name or L:name")
        return raw

    @app.get("/")
    def index():
        return render_template("gkg.html", version=VERSION)

    @app.get("/api/gkg/info")
    def info():
        v, reader = reader_arg()
        m = reader.meta
        return jsonify({
            "version": VERSION, "variant": v, "default_variant": default,
            "variants": [{"name": name, "theta": r.meta["config"].get("theta"), "range": r.meta["range"],
                          "articles": r.meta["articles"], "snapshots": len(r.times), "built_utc": r.meta["built_utc"]}
                         for name, r in readers.items()],
            "range": m["range"], "articles": m["articles"], "snapshots": reader.times,
            "config": m["config"], "engine_config": m["engine_config"],
            "built_utc": m["built_utc"], "build_seconds": m["seconds"], "articles_per_s": m.get("articles_per_s"),
            "peak_rss_mb": m["peak_rss_mb"], "fingerprint": m["fingerprint"], "kinds": KIND_KO,
            "days": m.get("days", []), "events": catalog,
            "solar_enabled": public_provider()["enabled"], "llm": public_provider(),
        })

    @app.get("/api/gkg/snapshot")
    def snapshot():
        v, reader = reader_arg()
        t = t_arg()
        tab, kind = tab_kind()
        s = reader.snapshot(t)
        tb = s["tabs"][tab]
        return jsonify({"t": s["t"], "articles": s["articles"], "variant": s["variant"], "tab": tab,
                        "kind": kind, "window": tb["window"], "status": tb["status"],
                        "status_ko": tb.get("label_ko"), "qualified": tb.get("qualified"),
                        "candidates": tb.get("candidates"),
                        "elapsed_days": tb.get("elapsed_days"), "rows": tb["rows"][kind],
                        "gaps": s["gaps"], "countries": s["countries"], "events": s["events"]})

    @app.get("/api/gkg/state")
    def state():
        """Everything one screen needs at time t: rows with rank changes and sparklines, rail lists,
        KPIs, entry events up to t and (race=1) the rank race of the current list."""
        v, reader = reader_arg()
        t = t_arg()
        tab, kind = tab_kind()
        s = reader.snapshot(t)
        t = s["t"]
        idx = reader.times.index(t)
        tb = s["tabs"][tab]
        rows = [dict(r) for r in tb["rows"][kind]]
        prev_t = reader.prev_time(t)
        prev = reader.ranks(prev_t, tab, kind) if prev_t is not None else None
        exact = readers.get("exact") if v != "exact" else None
        exact_ranks = exact.ranks(t, tab, kind) if exact is not None and t in exact.articles else None
        field = "r72" if tab == "new" else "r168"
        start = reader.times[max(0, idx - SPARK_N + 1)]
        spark = reader.series([r["key"] for r in rows], t, start)["series"] if rows else {}
        gi, last = 0, None
        for i, r in enumerate(rows, 1):
            grp = r.get("group") or ""
            gi = gi + 1 if grp == last else 1
            last = grp
            r["rank"] = gi if tab == "trend" else i
            p = prev.get(r["key"]) if prev is not None else None
            r["prev_rank"] = p[0] if p and p[1] == grp else None
            r["delta"] = None if r["prev_rank"] is None else r["prev_rank"] - r["rank"]
            r["spark"] = spark.get(r["key"], {}).get(field, [])
            if exact_ranks is not None:
                e = exact_ranks.get(r["key"])
                r["exact_rank"] = e[0] if e and e[1] == grp else None
        new_tab = s["tabs"]["new"]
        major_all = {r["key"]: i for i, r in enumerate(s["tabs"]["major"]["rows"]["all"], 1)}
        burst = [{"key": r["key"], "name": r["name"], "kind": r["kind"], "lift": r.get("lift"),
                  "rate": r.get("rate"), "rate_168h": r.get("rate_168h"), "articles_72h": r.get("articles_72h"),
                  "outlets": r.get("outlets"), "evidence": r.get("evidence"), "major_rank": major_all.get(r["key"])}
                 for r in new_tab["rows"]["all"][:10]]
        trend = s["tabs"]["trend"]
        out = {
            "t": t, "idx": idx, "n": len(reader.times), "articles": s["articles"], "variant": s["variant"],
            "tab": tab, "kind": kind, "window": tb["window"], "status": tb["status"],
            "status_ko": tb.get("label_ko"), "elapsed_days": trend.get("elapsed_days"),
            "candidates": tb.get("candidates"), "qualified": new_tab.get("qualified"),
            "rows": rows, "exact_compared": exact_ranks is not None,
            "burst": {"status": new_tab["status"], "rows": burst},
            "events": reader.events_upto(t, 150),
            "kpi": {"articles": s["articles"], "articles_total": reader.meta["articles"], "stats": s.get("stats", {}),
                    "gaps": s["gaps"], "new_status": new_tab["status"], "new_qualified": new_tab.get("qualified"),
                    "new_shown": len(new_tab["rows"]["all"]), "trend_status": trend["status"],
                    "elapsed_days": trend.get("elapsed_days")},
            "countries": s["countries"],
        }
        if request.args.get("race") == "1":
            grp = request.args.get("grp", "rising") if tab == "trend" else ""
            if tab == "trend" and grp not in GROUPS:
                raise BadRequest(f"grp must be one of {GROUPS}")
            try:
                k = max(3, min(int(request.args.get("k", 10)), 10))
            except ValueError:
                raise BadRequest("k must be an integer")
            out["race"] = {**reader.race(t, tab, kind, grp, SPARK_N, k), "grp": grp}
        return jsonify(out)

    @app.get("/api/gkg/series")
    def series():
        v, reader = reader_arg()
        t = t_arg()
        keys = [key_arg(k) for k in request.args.getlist("key")][:5]
        if not keys:
            raise BadRequest("key required (repeat key=... up to 5)")
        return jsonify(reader.series(keys, t))

    @app.get("/api/gkg/search")
    def search():
        v, reader = reader_arg()
        return jsonify({"q": request.args.get("q", ""), "keys": reader.search(request.args.get("q", ""), 20)})

    @app.get("/api/gkg/key")
    def key_detail():
        v, reader = reader_arg()
        t = t_arg()
        key = key_arg(request.args.get("key", ""))
        s = reader.snapshot(t)
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 50))
        except ValueError:
            raise BadRequest("limit must be an integer")
        out = {"t": s["t"], "key": key, "row": reader.find_row(s, key),
               "evidence": reader.evidence(key, s["articles"], limit=limit)}
        if request.args.get("full") == "1":
            ser = reader.series([key], s["t"])
            out.update(rates=reader.rates_at(key, s["t"]), series={"t": ser["t"], **ser["series"][key]},
                       entries=reader.key_events(key, s["t"]),
                       cooccurring=reader.cooccurring(key, s["articles"]))
        return jsonify(out)

    @app.get("/api/gkg/e1")
    def e1():
        return jsonify(load_e1(results_path))

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
        v = body.get("v") or default
        if v not in readers:
            return bad(f"v must be one of {sorted(readers)}")
        return jsonify(gkg_ask(readers[v], q, t, body.get("key")))

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="first day YYYY-MM-DD (default: first downloaded day)")
    ap.add_argument("--end", help="last day YYYY-MM-DD (default: last downloaded day)")
    ap.add_argument("--theta", type=float, default=None, help="deletion threshold of the default variant (default: exact)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", PORT)))
    args = ap.parse_args()
    cfg = CacheConfig(start=args.start, end=args.end, theta=args.theta)
    meta = ensure_cache(cfg, root=args.root, rebuild=args.rebuild)
    app = create_app(default_variant="exact" if args.theta is None else f"theta={args.theta:g}")
    for name, r in app.config["READERS"].items():
        if r.meta["range"] != meta["range"]:
            print(f"note: variant {name} covers {r.meta['range']} (default covers {meta['range']})")
    print(f"Trend Pulse GKG: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
