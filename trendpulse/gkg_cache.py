"""Precomputed replay for the GKG dashboard (app_gkg.py).

The whole stream is replayed once with GkgEngine; every 6 hours of stream time the
first engine snapshot of that 6-hour block is turned into display rows and stored in
sqlite (``data/gdelt/processed/gkg_cache.sqlite``). The dashboard only reads the cache.
The cache is rebuilt when its fingerprint changes: input file names/sizes/mtimes,
the cache config, and the SHA-256 of the GKG modules.

Per stored snapshot (``payload`` JSON):
  tabs.major  7-day issues: 168 h window Top N per key kind, with burst/steady labels
  tabs.new    72-hour new issues: qualified keys by lift per key kind (status may be
              warming_up / insufficient, then no rows)
  tabs.trend  30-day burst/steady: candidates = 168 h window Top 2,000, labelled by
              ratio = (r168+α)/(r720+α); per key kind three groups of 10 (row field
              "group"): rising by ratio desc, cooling by ratio asc, steady by 168 h score.
              Empty while the label is warming_up / insufficient.
  countries   country mentions (type-1 places) in a separate 168 h decaying window
  gaps        missing-slot ratio per window
  events      new-issue entry events since the previous stored snapshot
Each row: key, kind, score, rate (bias-corrected, per hour), evidence (stored evidence
articles up to this point), outlets (72 h, approximate, None if not tracked), label,
gap (missing-slot ratio of the tab's window: 168 h, 72 h/168 h max, 720 h), approx (θ variant),
dgim (estimate of mentions among the last ``dgim_n`` articles; tracked keys only).

Evidence tracking: the engine tracks keys that enter any window's Top 200; the dashboard
also tracks the keys shown in the new-issue list (top_n per kind), from the hourly engine
snapshot where they first appear. Earlier articles of a key have no evidence.

Evidence articles are kept in a separate sqlite file
(``data/gdelt/processed/gkg_app_evidence.sqlite``) so running scripts/gkg_run.py does
not overwrite the dashboard's evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass

from trendpulse import gkg, gkg_engine, gkg_evidence
from trendpulse.dgim import DGIM
from trendpulse.gkg import DEFAULT_ROOT, REPO_ROOT, iter_articles, list_days, peak_rss_bytes
from trendpulse.gkg_engine import (LABELS_KO, GkgEngine, GkgEngineConfig, TimeDecayWindow,
                                   smoothed_ratio, trend_label, variant_name)

PROCESSED = os.path.join(REPO_ROOT, "data", "gdelt", "processed")
CACHE_PATH = os.path.join(PROCESSED, "gkg_cache.sqlite")
EVIDENCE_PATH = os.path.join(PROCESSED, "gkg_app_evidence.sqlite")
KINDS = {"all": None, "P": "P:", "O": "O:", "L": "L:"}
KIND_KO = {"P": "인물", "O": "기관", "L": "장소"}
CACHE_VERSION = 3
LABEL_KO_ALL = {**LABELS_KO, "new": "신규 이슈"}


@dataclass(frozen=True)
class CacheConfig:
    start: str | None = None          # None = first day folder
    end: str | None = None            # None = last day folder
    theta: float | None = None        # None = exact
    store_every_hours: float = 6.0
    top_n: int = 20                   # rows per tab and key kind
    countries_n: int = 15
    dgim_n: int = 160_000             # ≈ one day of articles
    evidence_limit: int = 20
    trend_group_n: int = 10           # rows per label group in the 30-day tab


def module_hashes() -> dict:
    out = {}
    for m in (gkg, gkg_engine, gkg_evidence):
        with open(m.__file__, "rb") as f:
            out[os.path.basename(m.__file__)] = hashlib.sha256(f.read()).hexdigest()
    with open(__file__, "rb") as f:
        out["gkg_cache.py"] = hashlib.sha256(f.read()).hexdigest()
    with open(gkg.COUNTRY_NAMES_PATH, "rb") as f:
        out["gkg_country_names.txt"] = hashlib.sha256(f.read()).hexdigest()
    return out


def resolve_range(cfg: CacheConfig, root: str) -> tuple[str, str]:
    days = list_days(root, cfg.start, cfg.end)
    if not days:
        raise FileNotFoundError(f"no day folders in range under {os.path.join(root, 'slim')}")
    return days[0], days[-1]


def fingerprint(cfg: CacheConfig, root: str = DEFAULT_ROOT) -> str:
    """Cheap input fingerprint: names, sizes and mtimes of the slim files in range."""
    start, end = resolve_range(cfg, root)
    h = hashlib.sha256()
    h.update(json.dumps({"v": CACHE_VERSION, "cfg": asdict(cfg), "range": [start, end],
                         "code": module_hashes()}, sort_keys=True).encode())
    for day in list_days(root, start, end):
        d = os.path.join(root, "slim", day)
        for name in sorted(os.listdir(d)):
            if name.endswith(".jsonl.gz") or name.endswith(".missing"):
                st = os.stat(os.path.join(d, name))
                h.update(f"{day}/{name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def read_meta(path: str = CACHE_PATH) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        db = sqlite3.connect(path)
        try:
            row = db.execute("SELECT value FROM meta WHERE key='meta'").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return json.loads(row[0]) if row else None


def kind_of(key: str) -> str:
    return key[0]


TREND_GROUPS = ("rising", "cooling", "steady")


def trend_groups(cands: list[dict], n: int = 10) -> dict:
    """30-day tab groups from labelled candidates given in 168h score order:
    rising by ratio descending, cooling by ratio ascending, steady in 168h score order;
    ties by key. Each group keeps at most n rows."""
    rising = sorted((c for c in cands if c["label"] == "rising"), key=lambda c: (-c["ratio"], c["key"]))
    cooling = sorted((c for c in cands if c["label"] == "cooling"), key=lambda c: (c["ratio"], c["key"]))
    steady = [c for c in cands if c["label"] == "steady"]
    return {"rising": rising[:n], "cooling": cooling[:n], "steady": steady[:n]}


class _Collector:
    """Turns engine snapshots into cached display payloads."""

    def __init__(self, cfg: CacheConfig, db: sqlite3.Connection, countries: TimeDecayWindow,
                 dgims: dict, evidence) -> None:
        self.cfg = cfg
        self.db = db
        self.countries = countries
        self.dgims = dgims
        self.evidence = evidence
        self.block = None
        self.last_t: int | None = None
        self.stored = 0
        self.events_seen = 0
        self.variant = variant_name(cfg.theta)

    def __call__(self, eng: GkgEngine, t: float) -> None:
        # Dashboard-only addition: keys shown in the new-issue list are tracked for evidence
        # and DGIM too (they rarely reach a Top 200 by score). Runs at every engine snapshot.
        if eng.new_ranked:
            shown = []
            for prefix in KINDS.values():
                shown += [r["key"] for r in eng.new_ranked if prefix is None or r["key"].startswith(prefix)][:self.cfg.top_n]
            self.evidence.track(shown, t, "new-issue list")
        block = int(t // (self.cfg.store_every_hours * 3600))
        if block == self.block:
            return
        self.block = block
        self.store(eng, t)

    def store(self, eng: GkgEngine, t: float) -> None:
        payload = self.payload(eng, t)
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                        (int(t), eng.articles, json.dumps(payload, ensure_ascii=False)))
        self.db.commit()
        self.stored += 1
        self.last_t = int(t)

    # ------------------------------------------------------------------ rows
    def _evidence_count(self, key: str, n_articles: int) -> int:
        ev = self.evidence
        kid = ev.tracked.get(key)
        if kid is None:
            return 0
        return ev.db.execute("SELECT COUNT(*) FROM postings WHERE key_id=? AND idx<?",
                             (kid, n_articles)).fetchone()[0]

    def _dgim(self, key: str, n_articles: int) -> dict | None:
        d = self.dgims.get(key)
        if d is None:
            return None
        dg, first_idx = d
        now = n_articles - 1
        return {"estimate": round(dg.estimate(now), 1), "window": self.cfg.dgim_n,
                "observed": min(self.cfg.dgim_n, now - first_idx + 1),
                "partial": now - first_idx + 1 < self.cfg.dgim_n, "error_bound": dg.error_bound()}

    def _row(self, eng, t, key, score, rate, label, gap, n_articles, extra=None) -> dict:
        act = eng.activity
        outlets = act.outlets(key, int(t)) if act is not None and key in act else None
        row = {"key": key, "name": key[2:], "kind": kind_of(key), "score": round(score, 3),
               "rate": None if rate is None else round(rate, 3), "label": label,
               "label_ko": LABEL_KO_ALL.get(label, label), "gap": round(gap, 4),
               "evidence": self._evidence_count(key, n_articles), "outlets": outlets,
               "approx": self.cfg.theta is not None, "dgim": self._dgim(key, n_articles)}
        if extra:
            row.update(extra)
        return row

    def _per_kind(self, ranked, make) -> dict:
        out = {}
        for kind, prefix in KINDS.items():
            rows = []
            for item in ranked:
                key = item[0] if isinstance(item, tuple) else item["key"]
                if prefix is None or key.startswith(prefix):
                    rows.append(make(item))
                    if len(rows) >= self.cfg.top_n:
                        break
            out[kind] = rows
        return out

    def payload(self, eng: GkgEngine, t: float) -> dict:
        n = eng.articles
        self.evidence.flush()
        self.countries.snapshot(t)
        det = eng.detection or {}
        gaps = det.get("gaps", {})
        v = self.variant
        w72, w168, w720 = (eng.windows.get((name, v)) for name in ("72h", "168h", "720h"))
        trend = det.get("trend") or {}
        trend_status = trend.get("status", "warming_up")
        cfg_e = eng.config

        def label_for(key):
            r168 = w168.bias_corrected_rate(key, t)
            r720 = w720.bias_corrected_rate(key, t)
            ratio = smoothed_ratio(r168, r720, cfg_e.alpha) if r168 is not None and r720 is not None else None
            if trend_status != "ok" or ratio is None:
                return ratio, (trend_status if trend_status != "ok" else "warming_up")
            return ratio, trend_label(ratio, cfg_e.trend_factor)

        def gap(name):
            return gaps.get(name, {}).get("missing_ratio", 0.0)

        g168, g720 = gap("168h"), gap("720h")
        g_new = max(gap("72h"), g168)

        def rnd(x):
            return None if x is None else round(x, 3)

        def major_row(item):
            key, s = item
            ratio, lab = label_for(key)
            return self._row(eng, t, key, s, w168.rate_from_score(s, t), lab, g168, n, {"ratio": rnd(ratio)})

        # 30-day tab: candidates = 168h Top M; three groups per key kind (see trend_groups)
        trend_rows = {k: [] for k in KINDS}
        if trend_status == "ok":
            cands = []
            for key, s in w168.top(w168.top_m):
                r168 = w168.rate_from_score(s, t)
                r720 = w720.bias_corrected_rate(key, t)
                if r168 is None or r720 is None:
                    continue
                ratio = smoothed_ratio(r168, r720, cfg_e.alpha)
                cands.append({"key": key, "score": s, "rate": r168, "rate_720h": r720, "ratio": ratio,
                              "label": trend_label(ratio, cfg_e.trend_factor)})
            for kind, prefix in KINDS.items():
                sub = [c for c in cands if prefix is None or c["key"].startswith(prefix)]
                trend_rows[kind] = [
                    self._row(eng, t, c["key"], c["score"], c["rate"], c["label"], max(g168, g720), n,
                              {"ratio": rnd(c["ratio"]), "rate_720h": rnd(c["rate_720h"]), "group": group})
                    for group, rows in trend_groups(sub, self.cfg.trend_group_n).items() for c in rows]

        new = det.get("new") or {}
        new_status = new.get("status", "warming_up")

        def new_row(r):
            s72 = w72.score(r["key"], t)
            return self._row(eng, t, r["key"], s72, r["rate_72h"], "new", g_new, n,
                             {"lift": round(r["lift"], 3), "rate_168h": round(r["rate_168h"], 3),
                              "articles_72h": r["articles_72h"], "outlets": r["outlets_72h"]})

        top_m = w168.top_m
        events = eng.new_events[self.events_seen:]
        self.events_seen = len(eng.new_events)
        return {
            "t": int(t), "articles": n, "variant": v,
            "gaps": gaps,
            "tabs": {
                "major": {"window": "168h", "status": trend_status,
                          "rows": self._per_kind(w168.top(top_m), major_row)},
                "new": {"window": "72h", "status": new_status, "label_ko": LABELS_KO.get(new_status, ""),
                        "qualified": len(eng.new_ranked),
                        "rows": self._per_kind(eng.new_ranked, new_row) if new_status == "ok"
                        else {k: [] for k in KINDS}},
                "trend": {"window": "168h", "status": trend_status,
                          "elapsed_days": trend.get("elapsed_days"),
                          "candidates": w168.top_m, "rows": trend_rows},
            },
            "countries": [{"name": k, "score": round(s, 2),
                           "rate": None if self.countries.rate_from_score(s, t) is None
                           else round(self.countries.rate_from_score(s, t), 2)}
                          for k, s in self.countries.top(self.cfg.countries_n, t)],
            "events": [{**e, "t": int(e["t"])} for e in events],
        }


def build_cache(cfg: CacheConfig = CacheConfig(), *, root: str = DEFAULT_ROOT, path: str = CACHE_PATH,
                evidence_path: str = EVIDENCE_PATH, progress=print) -> dict:
    """Replay the stream and write the cache. Returns the meta dict."""
    start, end = resolve_range(cfg, root)
    fp = fingerprint(cfg, root)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                     "CREATE TABLE snapshots (t INTEGER PRIMARY KEY, articles INTEGER, payload TEXT);")

    ecfg = GkgEngineConfig(thresholds=(cfg.theta,), detect_variant=variant_name(cfg.theta),
                           evidence_path=evidence_path)
    eng = GkgEngine(ecfg)
    countries = TimeDecayWindow(168.0, top_m=max(cfg.countries_n, 50))
    dgims: dict[str, tuple[DGIM, int]] = {}
    col = _Collector(cfg, db, countries, dgims, eng.evidence)
    eng.on_snapshot = col
    tracked = eng.evidence.tracked
    t0 = time.time()
    last_print = t0
    for a in iter_articles(start, end, root=root):
        eng.process(a)
        if a.countries:
            countries.add(a.countries, a.ts)
        for k in a.keys:
            if k in tracked:
                d = dgims.get(k)
                if d is None:
                    d = dgims[k] = (DGIM(cfg.dgim_n), a.idx)
                d[0].add(a.idx)
        if progress and time.time() - last_print > 30:
            last_print = time.time()
            progress(f"  replay: {eng.articles:,} articles, {col.stored} snapshots cached, "
                     f"{time.time() - t0:.0f} s")
    eng.finish()
    if eng._last_snap is not None and col.last_t != int(eng._last_snap):
        col.store(eng, eng._last_snap)    # always keep the final state
    seconds = time.time() - t0
    mem = eng.memory_report()
    mem["dgim"] = {"keys": len(dgims), "buckets": sum(d.num_buckets for d, _ in dgims.values())}
    rss = peak_rss_bytes()
    meta = {"version": CACHE_VERSION, "fingerprint": fp, "config": asdict(cfg),
            "engine_config": {k: v for k, v in asdict(ecfg).items() if k != "evidence_path"},
            "range": {"start": start, "end": end}, "articles": eng.articles, "snapshots": col.stored,
            "new_events": len(eng.new_events), "seconds": round(seconds, 1),
            "articles_per_s": round(eng.articles / seconds) if seconds else None,
            "peak_rss_mb": round(rss / 1e6, 1) if rss else None, "memory": mem,
            "code": module_hashes(), "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    db.execute("INSERT INTO meta VALUES ('meta', ?)", (json.dumps(meta, ensure_ascii=False),))
    db.commit()
    db.close()
    eng.close()
    if os.path.exists(path):
        os.remove(path)
    os.replace(tmp, path)
    return meta


def ensure_cache(cfg: CacheConfig = CacheConfig(), *, root: str = DEFAULT_ROOT, path: str = CACHE_PATH,
                 evidence_path: str = EVIDENCE_PATH, rebuild: bool = False, progress=print) -> dict:
    """Reuse the cache if its fingerprint matches, otherwise rebuild it."""
    meta = read_meta(path)
    fp = fingerprint(cfg, root)
    if not rebuild and meta and meta.get("fingerprint") == fp and os.path.exists(evidence_path):
        if progress:
            progress(f"cache ok: {meta['snapshots']} snapshots, {meta['articles']:,} articles "
                     f"({meta['range']['start']} .. {meta['range']['end']})")
        meta["rebuilt"] = False
        return meta
    if progress:
        progress("building cache (full replay) ...")
    meta = build_cache(cfg, root=root, path=path, evidence_path=evidence_path, progress=progress)
    meta["rebuilt"] = True
    if progress:
        progress(f"cache built: {meta['snapshots']} snapshots, {meta['articles']:,} articles in "
                 f"{meta['seconds']} s, peak RSS {meta['peak_rss_mb']} MB")
    return meta


class CacheReader:
    """Read-only access for the web app (one sqlite connection per call: thread-safe)."""

    def __init__(self, path: str = CACHE_PATH, evidence_path: str = EVIDENCE_PATH) -> None:
        self.path = path
        self.evidence_path = evidence_path
        self.meta = read_meta(path)
        if self.meta is None:
            raise FileNotFoundError(f"no cache at {path}")
        db = sqlite3.connect(path)
        try:
            rows = db.execute("SELECT t, articles FROM snapshots ORDER BY t").fetchall()
        finally:
            db.close()
        self.times = [t for t, _ in rows]
        self.articles = {t: n for t, n in rows}

    def nearest(self, t: int | None) -> int:
        if t is None:
            return self.times[-1]
        best = self.times[0]
        for x in self.times:
            if x <= t:
                best = x
            else:
                break
        return best

    def snapshot(self, t: int | None = None) -> dict:
        t = self.nearest(t)
        db = sqlite3.connect(self.path)
        try:
            row = db.execute("SELECT payload FROM snapshots WHERE t=?", (t,)).fetchone()
        finally:
            db.close()
        return json.loads(row[0])

    def find_row(self, snap: dict, key: str) -> dict | None:
        for tab in ("major", "new", "trend"):
            for r in snap["tabs"][tab]["rows"]["all"] + [x for k in ("P", "O", "L")
                                                          for x in snap["tabs"][tab]["rows"][k]]:
                if r["key"] == key:
                    return {**r, "tab": tab}
        return None

    def evidence(self, key: str, n_articles: int, limit: int = 20) -> dict:
        """Stored evidence articles for ``key`` among the first ``n_articles`` (newest first)."""
        if not os.path.exists(self.evidence_path):
            return {"tracked": False, "articles": [], "total": 0}
        db = sqlite3.connect(self.evidence_path)
        try:
            k = db.execute("SELECT key_id, first_tracked_ts FROM keys WHERE key=?", (key,)).fetchone()
            if not k:
                return {"tracked": False, "articles": [], "total": 0}
            total = db.execute("SELECT COUNT(*) FROM postings WHERE key_id=? AND idx<?",
                               (k[0], n_articles)).fetchone()[0]
            rows = db.execute(
                "SELECT a.idx, a.ts, a.domain, a.url, a.title FROM postings p JOIN articles a ON a.idx=p.idx "
                "WHERE p.key_id=? AND p.idx<? ORDER BY p.idx DESC LIMIT ?", (k[0], n_articles, limit)).fetchall()
        finally:
            db.close()
        return {"tracked": True, "first_tracked_ts": k[1], "total": total,
                "articles": [dict(zip(("idx", "ts", "domain", "url", "title"), r)) for r in rows]}
