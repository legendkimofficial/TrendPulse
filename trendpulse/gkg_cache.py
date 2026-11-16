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

Cache version 4 (v0.4-style dashboard) adds, next to ``snapshots``:
  ranks   (t, tab, kind, grp, rank, key)   every displayed row; rank within the trend group
  watch   (key, first_t)                   keys whose rates are recorded: evidence-tracked keys
                                           (any window's Top 200) plus every displayed key, from
                                           the first stored snapshot where they qualify
  series  (key, t, r72, r168, r720)        bias-corrected rates per hour of watched keys
  events  (t, key, kind, rank, lift)       new-issue entry events
  payload.stats                            window key counts and estimated decay-window bytes
  meta.days                                articles per UTC day and missing 15-minute slots
A θ variant lives in its own pair of files (``gkg_cache_theta0.5.sqlite`` and
``gkg_app_evidence_theta0.5.sqlite``); the dashboard offers every cache file it finds.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass

from trendpulse import gkg, gkg_engine, gkg_evidence
from trendpulse.dgim import DGIM
from trendpulse.gkg import DEFAULT_ROOT, REPO_ROOT, iter_articles, list_days, peak_rss_bytes
from trendpulse.gkg_engine import (LABELS_KO, GkgEngine, GkgEngineConfig, TimeDecayWindow,
                                   smoothed_ratio, trend_label, variant_name)

PROCESSED = os.path.join(REPO_ROOT, "data", "gdelt", "processed")
KINDS = {"all": None, "P": "P:", "O": "O:", "L": "L:"}
KIND_KO = {"P": "인물", "O": "기관", "L": "장소"}
CACHE_VERSION = 4


def cache_paths(theta: float | None = None, directory: str = PROCESSED) -> tuple[str, str]:
    """(cache, evidence) file paths of a θ variant (None = exact)."""
    tag = "" if theta is None else f"_theta{theta:g}"
    return (os.path.join(directory, f"gkg_cache{tag}.sqlite"),
            os.path.join(directory, f"gkg_app_evidence{tag}.sqlite"))


CACHE_PATH, EVIDENCE_PATH = cache_paths(None)
_CACHE_NAME = re.compile(r"gkg_cache(?:_theta([0-9.]+))?\.sqlite")


def discover_variants(directory: str = PROCESSED) -> dict[str, tuple[str, str]]:
    """Every cache file in ``directory``: {variant name: (cache, evidence)}; exact first."""
    found = []
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            m = _CACHE_NAME.fullmatch(name)
            if m:
                theta = float(m.group(1)) if m.group(1) else None
                found.append((theta is not None, theta or 0.0, variant_name(theta),
                              cache_paths(theta, directory)))
    return {v: paths for _, _, v, paths in sorted(found)}
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
        self.watch: dict[str, int] = {}

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
        ti = int(t)
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                        (ti, eng.articles, json.dumps(payload, ensure_ascii=False)))
        shown = []
        for tab, tb in payload["tabs"].items():
            for kind, rows in tb["rows"].items():
                gi, last = 0, None
                for i, r in enumerate(rows, 1):
                    grp = r.get("group") or ""
                    gi = gi + 1 if grp == last else 1
                    last = grp
                    shown.append((ti, tab, kind, grp, gi if tab == "trend" else i, r["key"]))
        self.db.executemany("INSERT INTO ranks VALUES (?,?,?,?,?,?)", shown)
        self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?)",
                            [(e["t"], e["key"], e["kind"], e["rank"], e.get("lift")) for e in payload["events"]])
        self._series(eng, t, [r[5] for r in shown])
        self.db.commit()
        self.stored += 1
        self.last_t = int(t)

    def _series(self, eng: GkgEngine, t: float, shown: list[str]) -> None:
        """Rates of watched keys (evidence-tracked ∪ displayed so far) at this snapshot."""
        new = list(dict.fromkeys(k for k in list(self.evidence.tracked) + shown if k not in self.watch))
        for k in new:
            self.watch[k] = int(t)
        self.db.executemany("INSERT OR IGNORE INTO watch VALUES (?,?)", [(k, int(t)) for k in new])
        v = self.variant
        ws = [eng.windows.get((name, v)) for name in ("72h", "168h", "720h")]

        def r(w, key):
            if w is None:
                return None
            x = w.bias_corrected_rate(key, t)
            return None if x is None else round(x, 4)
        self.db.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?)",
                            [(k, int(t), r(ws[0], k), r(ws[1], k), r(ws[2], k)) for k in self.watch])

    def _stats(self, eng: GkgEngine) -> dict:
        v = self.variant
        keys, total = {}, 0
        for name in ("72h", "168h", "720h"):
            w = eng.windows.get((name, v))
            if w is not None:
                m = w.memory_bytes()
                keys[name] = m["keys"]
                total += m["estimate_bytes"]
        return {"keys": keys, "decay_bytes": total, "evidence_keys": len(self.evidence.tracked),
                "dgim_keys": len(self.dgims), "watched": len(self.watch)}

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
            "gaps": gaps, "stats": self._stats(eng),
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


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE snapshots (t INTEGER PRIMARY KEY, articles INTEGER, payload TEXT);
CREATE TABLE ranks (t INTEGER, tab TEXT, kind TEXT, grp TEXT, rank INTEGER, key TEXT);
CREATE INDEX ranks_tab ON ranks (tab, kind, t);
CREATE INDEX ranks_key ON ranks (key, t);
CREATE TABLE watch (key TEXT PRIMARY KEY, first_t INTEGER) WITHOUT ROWID;
CREATE TABLE series (key TEXT, t INTEGER, r72 REAL, r168 REAL, r720 REAL, PRIMARY KEY (key, t)) WITHOUT ROWID;
CREATE TABLE events (t INTEGER, key TEXT, kind TEXT, rank INTEGER, lift REAL);
CREATE INDEX events_t ON events (t);
"""


def day_table(root: str, start: str, end: str, per_day: dict[int, int]) -> list[dict]:
    """Articles per UTC day (from the replay) and the day's missing 15-minute slots (HHMM.missing)."""
    out = []
    for day in list_days(root, start, end):
        d = os.path.join(root, "slim", day)
        names = os.listdir(d)
        dn = int(calendar.timegm(time.strptime(day, "%Y-%m-%d"))) // 86400
        out.append({"date": day, "articles": per_day.get(dn, 0),
                    "missing": sorted(n[:4] for n in names if n.endswith(".missing")),
                    "files": sum(1 for n in names if n.endswith(".jsonl.gz"))})
    return out


def build_cache(cfg: CacheConfig = CacheConfig(), *, root: str = DEFAULT_ROOT, path: str | None = None,
                evidence_path: str | None = None, progress=print) -> dict:
    """Replay the stream and write the cache. Returns the meta dict."""
    path, evidence_path = _paths(cfg, path, evidence_path)
    start, end = resolve_range(cfg, root)
    fp = fingerprint(cfg, root)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.executescript(SCHEMA)

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
    per_day: dict[int, int] = {}
    for a in iter_articles(start, end, root=root):
        eng.process(a)
        d = a.ts // 86400
        per_day[d] = per_day.get(d, 0) + 1
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
            "code": module_hashes(), "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "days": day_table(root, start, end, per_day)}
    db.execute("INSERT INTO meta VALUES ('meta', ?)", (json.dumps(meta, ensure_ascii=False),))
    db.commit()
    db.close()
    eng.close()
    if os.path.exists(path):
        os.remove(path)
    os.replace(tmp, path)
    return meta


def _paths(cfg: CacheConfig, path: str | None, evidence_path: str | None) -> tuple[str, str]:
    c, e = cache_paths(cfg.theta)
    return path or c, evidence_path or e


def ensure_cache(cfg: CacheConfig = CacheConfig(), *, root: str = DEFAULT_ROOT, path: str | None = None,
                 evidence_path: str | None = None, rebuild: bool = False, progress=print) -> dict:
    """Reuse the cache if its fingerprint matches, otherwise rebuild it. Default files depend on
    cfg.theta (see cache_paths)."""
    path, evidence_path = _paths(cfg, path, evidence_path)
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

    # ------------------------------------------------------------------ v4 tables (dashboard v0.4 layout)
    def _q(self, sql: str, args=(), path: str | None = None) -> list[tuple]:
        db = sqlite3.connect(path or self.path)
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def prev_time(self, t: int) -> int | None:
        i = self.times.index(t)
        return self.times[i - 1] if i > 0 else None

    def ranks(self, t: int, tab: str, kind: str) -> dict[str, tuple[int, str]]:
        return {k: (r, g) for k, r, g in self._q(
            "SELECT key, rank, grp FROM ranks WHERE tab=? AND kind=? AND t=?", (tab, kind, t))}

    def race(self, t: int, tab: str, kind: str, grp: str = "", n: int = 28, k: int = 10) -> dict:
        """Ranks (≤ k) of the displayed rows over the last n stored snapshots up to t."""
        i = self.times.index(self.nearest(t))
        times = self.times[max(0, i - n + 1): i + 1]
        rows = self._q("SELECT t, key, rank FROM ranks WHERE tab=? AND kind=? AND grp=? AND t>=? AND t<=? AND rank<=?",
                       (tab, kind, grp, times[0], times[-1], k))
        pos = {x: j for j, x in enumerate(times)}
        series: dict[str, list] = {}
        for tt, key, rank in rows:
            series.setdefault(key, [None] * len(times))[pos[tt]] = rank
        return {"t": times, "k": k, "series": series}

    def series(self, keys: list[str], t: int | None = None, start: int | None = None) -> dict:
        """Rates per hour (r72, r168, r720) of watched keys at every stored snapshot in [start, t]."""
        t = self.nearest(t)
        times = [x for x in self.times if x <= t and (start is None or x >= start)]
        pos = {x: j for j, x in enumerate(times)}
        out = {}
        for key in keys:
            s = {"r72": [None] * len(times), "r168": [None] * len(times), "r720": [None] * len(times)}
            first = self._q("SELECT first_t FROM watch WHERE key=?", (key,))
            if times:
                for tt, a, b, c in self._q("SELECT t, r72, r168, r720 FROM series WHERE key=? AND t>=? AND t<=?",
                                           (key, times[0], times[-1])):
                    j = pos.get(tt)
                    if j is not None:
                        s["r72"][j], s["r168"][j], s["r720"][j] = a, b, c
            s["watched_from"] = first[0][0] if first else None
            out[key] = s
        return {"t": times, "series": out}

    def rates_at(self, key: str, t: int) -> dict | None:
        r = self._q("SELECT r72, r168, r720 FROM series WHERE key=? AND t=?", (key, t))
        return dict(zip(("r72", "r168", "r720"), r[0])) if r else None

    def search(self, q: str, limit: int = 20) -> list[str]:
        """Watched keys whose name contains q (prefix matches first)."""
        q = " ".join(q.lower().split())
        if not q:
            return []
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = [k for (k,) in self._q("SELECT key FROM watch WHERE substr(key, 3) LIKE ? ESCAPE '!'", (like,))]
        rows.sort(key=lambda k: (not k[2:].startswith(q), len(k), k))
        return rows[:limit]

    def events_upto(self, t: int, limit: int = 200) -> dict:
        rows = self._q("SELECT t, key, kind, rank, lift FROM events WHERE t<=? ORDER BY t DESC, rank LIMIT ?",
                       (t, limit))
        counts = dict(self._q("SELECT kind, COUNT(*) FROM events WHERE t<=? GROUP BY kind", (t,)))
        return {"total": sum(counts.values()), "first": counts.get("first", 0),
                "reentry": counts.get("re-entry", 0),
                "rows": [dict(zip(("t", "key", "kind", "rank", "lift"), r)) for r in rows]}

    def key_events(self, key: str, t: int) -> list[dict]:
        return [dict(zip(("t", "kind", "rank", "lift"), r)) for r in self._q(
            "SELECT t, kind, rank, lift FROM events WHERE key=? AND t<=? ORDER BY t", (key, t))]

    def cooccurring(self, key: str, n_articles: int, limit: int = 12, sample: int = 300) -> dict:
        """Other tracked keys in the key's newest ``sample`` evidence articles, and their outlets.
        Only postings that were stored count (tracked keys, 30 per key per UTC day)."""
        if not os.path.exists(self.evidence_path):
            return {"keys": [], "domains": [], "articles": 0}
        db = sqlite3.connect(self.evidence_path)
        try:
            k = db.execute("SELECT key_id FROM keys WHERE key=?", (key,)).fetchone()
            if not k:
                return {"keys": [], "domains": [], "articles": 0}
            idx = [i for (i,) in db.execute("SELECT idx FROM postings WHERE key_id=? AND idx<? "
                                            "ORDER BY idx DESC LIMIT ?", (k[0], n_articles, sample))]
            if not idx:
                return {"keys": [], "domains": [], "articles": 0}
            marks = ",".join("?" * len(idx))
            keys = db.execute(f"SELECT k.key, COUNT(*) c FROM postings p JOIN keys k ON k.key_id=p.key_id "
                              f"WHERE p.idx IN ({marks}) AND p.key_id<>? GROUP BY k.key ORDER BY c DESC, k.key "
                              f"LIMIT ?", (*idx, k[0], limit)).fetchall()
            doms = db.execute(f"SELECT domain, COUNT(*) c FROM articles WHERE idx IN ({marks}) "
                              f"GROUP BY domain ORDER BY c DESC, domain LIMIT 8", idx).fetchall()
        finally:
            db.close()
        return {"keys": keys, "domains": doms, "articles": len(idx)}
