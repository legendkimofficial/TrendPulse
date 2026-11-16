"""GKG experiments E1-E5 (gdelt-gkg branch).

  python experiments/run_gkg_experiments.py --quick              # one day (2024-10-01), results in .../gkg/quick/
  python experiments/run_gkg_experiments.py                      # 2024-09-15 .. 2024-11-14
  python experiments/run_gkg_experiments.py --only E5            # some experiments; others kept in results.json

Outputs: experiments/results/gkg/results.json, summary.md (experiment section at the top: command,
period, input hash), figures/*.png. --quick writes the same files under experiments/results/gkg/quick/.

Replays
  main: engine A = windows 72h/168h/720h x {exact, θ 0.1, 0.3, 1.0}, detection on exact;
        engine B = the same windows with θ 0.5, detection on θ 0.5. Both see the same stream in one
        pass (E1-E4). Snapshots every 60 min of stream time; evaluation at the first snapshot of every
        6-hour block from --eval-from (default 2024-10-01).
  E5:   168h exact only, once with 2 % and once with 5 % of the 15-minute slots removed (seeded).

E1 memory and ranking per (window, θ) against exact: stored keys, memory estimate (dict + floats),
   Top-20 overlap (mean / min), share of evaluation times with an identical Top-20 order, relative
   score error over the exact Top-20 keys at each evaluation time (pooled).
E2 new-issue capture: first time a key matching an event (substring of the key name, allowed types)
   is in the 72-hour new-issue Top 20 (qualified keys by lift), delay to first_report_utc; exact and θ 0.5.
   E2 revised rule (post-hoc, added 2026-11-17 after seeing the original results; the original rule and
   its results are kept unchanged): the event's match phrase must equal the last word(s) of one
   comma-separated part of the key name (word units: "milton" matches "hurricane milton" and
   "milton, florida" but not "milton keynes"), and only entries at or after first_report_utc - 24 h
   count. Keys that match by name but not by the event's types are reported as "types 불일치".
   Candidates: matching keys among all qualified new-issue rows after the cut-off (rank may exceed 20).
E3 burst/steady at 00:00 UTC of 2024-10-15, 11-01, 11-08, 11-14: 30-day tab groups (168h Top 2,000
   candidates, F = 1.5), label counts among candidates, reference keys' labels (also daily).
E4 DGIM vs exact sliding count (trendpulse/dgim.py, sliding.py) for the exact 168h Top 10 and keys at
   ranks 100 / 1,000 / 10,000 (chosen at the first evaluation time), (N, r) in {(10000,2), (50000,2), (50000,4)}.
E5 robustness to gaps: Top-20 overlap of the 168h window with 2 % / 5 % of slots removed vs the full run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shlex
import sys
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from trendpulse import dgim as dgim_mod, gkg, gkg_cache, gkg_engine, sliding  # noqa: E402
from trendpulse.dgim import DGIM  # noqa: E402
from trendpulse.gkg import (DEFAULT_ROOT, StreamStats, _file_sha256, _git_state, canonical_line,  # noqa: E402
                            iter_articles, list_days, peak_rss_bytes)
from trendpulse.gkg_cache import trend_groups  # noqa: E402
from trendpulse.gkg_engine import (HALF_LIVES, GkgEngine, GkgEngineConfig, smoothed_ratio,  # noqa: E402
                                   trend_label, variant_name)
from trendpulse.sliding import ExactWindowCount  # noqa: E402

RESULTS = os.path.join(REPO, "experiments", "results", "gkg")
EVENTS_PATH = os.path.join(REPO, "experiments", "gkg_events.json")
THETAS = (0.1, 0.3, 0.5, 1.0)
K = 20
BLOCK = 6 * 3600
E3_DATES = ("2024-10-15", "2024-11-01", "2024-11-08", "2024-11-14")
E4_CONFIGS = ((10000, 2), (50000, 2), (50000, 4))
E4_RANKS = {"high": 100, "mid": 1000, "low": 10000}
E5_DROPS = (0.02, 0.05)
E2_PRE_HOURS = 24              # revised E2 rule: entries before first report - 24 h do not count
E2_CANDIDATES = 5
SEED = 20241001
# measured on this machine (articles per second, including reading); used only for the first estimate
RATE_MAIN, RATE_E5 = 3400.0, 12000.0       # --quick on 2024-10-01: 3,400/s and 13,000/s
ALL = ("E1", "E2", "E3", "E4", "E5")


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def utc_ts(s: str) -> int:
    """'2024-10-10T00:30:00Z' -> epoch seconds."""
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def iso(ts) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def day_ts(d: str) -> int:
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())


def count_articles(root: str, start: str, end: str) -> int:
    """Article count from download_log.csv (latest status per file); for the estimate only."""
    want = {d.replace("-", "") for d in list_days(root, start, end)}
    last = {}
    path = os.path.join(root, "download_log.csv")
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("file", "")[:8] in want:
                    last[r["file"]] = r
    return sum(int(r.get("kept") or 0) for r in last.values() if r.get("status") == "ok")


class Progress:
    def __init__(self, label: str, total: int) -> None:
        self.label, self.total = label, max(total, 1)
        self.t0 = self.last = time.time()

    def tick(self, n: int, extra: str = "") -> None:
        now = time.time()
        if now - self.last < 30:
            return
        self.last = now
        el = now - self.t0
        rate = n / el if el else 0
        eta = (self.total - n) / rate if rate else 0
        rss = peak_rss_bytes()
        print(f"  [{self.label}] {n:,}/{self.total:,} ({n / self.total:.0%}) {rate:,.0f}/s, "
              f"elapsed {el / 60:.1f} min, ETA {eta / 60:.1f} min, peak RSS {(rss or 0) / 1e9:.2f} GB {extra}",
              flush=True)


# --------------------------------------------------------------------------- main replay
class MainRun:
    def __init__(self, args, exps: set[str], events: dict) -> None:
        self.args = args
        self.exps = exps
        self.events = events
        self.eval_from = day_ts(args.eval_from)
        full = bool(exps & {"E1", "E2", "E3", "E4"})
        if full:
            wins = HALF_LIVES
            th_a = (None,) + tuple(t for t in THETAS if t != 0.5) if "E1" in exps else (None,)
        else:                                   # E5 only: reference 168h exact
            wins, th_a = (("168h", 168.0),), (None,)
        self.engA = GkgEngine(GkgEngineConfig(windows=wins, thresholds=th_a, detect=full), on_snapshot=self.on_a)
        self.engB = None
        if exps & {"E1", "E2"}:
            self.engB = GkgEngine(GkgEngineConfig(windows=HALF_LIVES, thresholds=(0.5,),
                                                  detect_variant=variant_name(0.5)), on_snapshot=self.on_b)
        self.block_a = self.block_b = None
        self.pending: dict[int, dict] = {}       # block -> exact top20 per window (from A)
        self.ref_top20: dict[int, dict] = {}     # block -> {window: [keys]} exact (E5 reference)
        self.e1: dict = {}                       # (window, variant) -> list of per-time dicts
        self.e2 = {"exact": {}, "theta=0.5": {}}
        self.e2r = {"exact": {}, "theta=0.5": {}}       # revised rule: event id -> key -> candidate
        self._tails: dict[str, tuple] = {}
        self._e2_events = []
        for group in ("events", "warmup_reference_only"):
            for ev in events.get(group, []):
                first = utc_ts(ev["first_report_utc"])
                self._e2_events.append({"id": ev["id"], "types": ev["types"], "first": first,
                                        "cutoff": first - E2_PRE_HOURS * 3600,
                                        "match": [tuple(words(m)) for m in ev["match"]]})
        self._e2_by_last: dict[str, list] = {}
        for ev in self._e2_events:
            for m in ev["match"]:
                self._e2_by_last.setdefault(m[-1], []).append((ev, m))
        self.e3_dates = {day_ts(d): d for d in E3_DATES}
        self.e3: dict = {"dates": {}, "daily": []}
        self.e3_done: set[int] = set()
        self.e4_keys: dict | None = None
        self.e4_track: dict = {}
        self.e4_rows: list = []
        self.sha = hashlib.sha256()
        self.articles = 0

    # ------------------------------------------------------------------ helpers
    def windows_of(self, eng):
        return {(n, v): w for (n, v), w in eng.windows.items()}

    def on_a(self, eng, t):
        self.e2_capture("exact", eng, t)
        if t < self.eval_from:
            return
        block = int(t // BLOCK)
        if block == self.block_a:
            return
        self.block_a = block
        tops = {}
        for (name, v), w in eng.windows.items():
            if v == "exact":
                tops[name] = w.top(K)
        self.ref_top20[block] = {n: [k for k, _ in rows] for n, rows in tops.items()}
        if "E1" in self.exps:
            for (name, v), w in eng.windows.items():
                self.e1_row(name, v, w, tops[name], t)
            if self.engB is not None:
                self.pending[block] = {"t": t, "tops": tops}
        if "E3" in self.exps:
            self.e3_capture(eng, t)
        if "E4" in self.exps:
            self.e4_capture(eng, t)

    def on_b(self, eng, t):
        self.e2_capture("theta=0.5", eng, t)
        if t < self.eval_from or "E1" not in self.exps:
            return
        block = int(t // BLOCK)
        if block == self.block_b:
            return
        self.block_b = block
        p = self.pending.pop(block, None)
        if p is None:
            return
        for (name, v), w in eng.windows.items():
            self.e1_row(name, v, w, p["tops"][name], t)

    def e1_row(self, name, v, w, exact_top, t):
        mine = w.top(K)
        ek = [k for k, _ in exact_top]
        mk = [k for k, _ in mine]
        errs = []
        for k, s in exact_top:
            if s > 0:
                errs.append(abs(s - w.score(k, t)) / s)
        mem = w.memory_bytes()
        self.e1.setdefault((name, v), []).append({
            "t": int(t), "keys": len(w), "bytes": mem["estimate_bytes"],
            "overlap": len(set(ek) & set(mk)) / max(len(ek), 1), "same_order": ek == mk,
            "err_mean": sum(errs) / len(errs) if errs else 0.0, "err_max": max(errs) if errs else 0.0,
            "errs": errs})

    def e2_capture(self, variant, eng, t):
        if "E2" not in self.exps:
            return
        ranked = eng.new_ranked[:K] if (eng.detection or {}).get("new", {}).get("status") == "ok" else []
        if not ranked:
            return
        got = self.e2[variant]
        for group in ("events", "warmup_reference_only"):
            for ev in self.events.get(group, []):
                if ev["id"] in got:
                    continue
                for rank, r in enumerate(ranked, 1):
                    key = r["key"]
                    if key[0] in ev["types"] and any(m in key[2:] for m in ev["match"]):
                        got[ev["id"]] = {"t": int(t), "key": key, "rank": rank, "lift": round(r["lift"], 3)}
                        break
        self.e2_revised(variant, eng.new_ranked, int(t))

    def name_parts(self, key: str) -> tuple:
        """Word lists of the comma-separated parts of a key name (cached)."""
        parts = self._tails.get(key)
        if parts is None:
            parts = self._tails[key] = tuple(tuple(words(c)) for c in key[2:].split(","))
        return parts

    def e2_revised(self, variant: str, ranked: list, t: int) -> None:
        """Revised E2 rule over every qualified new-issue row (word-unit match, cut-off first report - 24 h)."""
        by_last = self._e2_by_last
        cands = self.e2r[variant]
        for rank, r in enumerate(ranked, 1):
            key = r["key"]
            parts = self.name_parts(key)
            for part in parts:
                if not part or part[-1] not in by_last:
                    continue
                for ev, m in by_last[part[-1]]:
                    if t < ev["cutoff"] or part[-len(m):] != m:
                        continue
                    c = cands.setdefault(ev["id"], {}).get(key)
                    if c is None:
                        c = cands[ev["id"]][key] = {"key": key, "type_ok": key[0] in ev["types"], "first_t": t,
                                                    "first_rank": rank, "best_rank": rank, "best_t": t,
                                                    "top20_t": None, "top20_rank": None}
                    if rank < c["best_rank"]:
                        c["best_rank"], c["best_t"] = rank, t
                    if rank <= K and c["top20_t"] is None:
                        c["top20_t"], c["top20_rank"], c["top20_lift"] = t, rank, round(r["lift"], 3)

    def e3_capture(self, eng, t):
        det = eng.detection or {}
        status = (det.get("trend") or {}).get("status")
        day = int(t // 86400) * 86400
        w168, w720 = eng.window("168h"), eng.window("720h")
        a = eng.config.alpha

        def label(key):
            r168, r720 = w168.bias_corrected_rate(key, t), w720.bias_corrected_rate(key, t)
            if r168 is None or r720 is None:
                return None, None
            ratio = smoothed_ratio(r168, r720, a)
            return ratio, (trend_label(ratio, eng.config.trend_factor) if status == "ok" else status)

        if t == day and day not in self.e3_done:          # daily reference keys at 00:00 UTC
            self.e3_done.add(day)
            row = {"date": iso(t)[:10], "status": status, "keys": {}}
            for ref in self.events.get("flash_vs_steady_reference", []):
                for m in ref["match"]:
                    key = f"{ref['types'][0]}:{m}"
                    ratio, lab = label(key)
                    row["keys"][key] = {"ratio": None if ratio is None else round(ratio, 3), "label": lab,
                                        "rate_168h": round(w168.bias_corrected_rate(key, t) or 0, 2)}
            self.e3["daily"].append(row)
        if t in self.e3_dates:
            cands = []
            for key, s in w168.top(w168.top_m):
                ratio, lab = label(key)
                if ratio is not None:
                    cands.append({"key": key, "score": s, "ratio": ratio, "label": lab})
            counts = {}
            for c in cands:
                counts[c["label"]] = counts.get(c["label"], 0) + 1
            groups = trend_groups(cands, 10) if status == "ok" else {}
            self.e3["dates"][self.e3_dates[t]] = {
                "t": int(t), "status": status, "candidates": len(cands), "counts": counts,
                "groups": {g: [{"key": c["key"], "ratio": round(c["ratio"], 3), "score": round(c["score"], 1)}
                               for c in rows] for g, rows in groups.items()}}

    def e4_capture(self, eng, t):
        now = eng.articles - 1
        if self.e4_keys is None:
            w = eng.window("168h")
            top10 = [k for k, _ in w.top(10)]
            full = w.full_top(max(E4_RANKS.values()), t)
            keys = {k: "top10" for k in top10}
            self.e4_skipped = []
            for grp, rank in E4_RANKS.items():
                if len(full) >= rank:
                    keys.setdefault(full[rank - 1][0], f"{grp} (rank {rank})")
                else:
                    self.e4_skipped.append(f"{grp} (rank {rank}): only {len(full)} keys at selection")
            self.e4_keys = keys
            self.e4_start_idx = eng.articles
            for k in keys:
                self.e4_track[k] = {(n, r): (DGIM(n, r), ExactWindowCount(n)) for n, r in E4_CONFIGS}
            return
        for k, cfgs in self.e4_track.items():
            for (n, r), (dg, ex) in cfgs.items():
                exact = ex.count(now)
                est = dg.estimate(now)
                self.e4_rows.append({"t": int(t), "key": k, "group": self.e4_keys[k], "N": n, "r": r,
                                     "exact": exact, "estimate": est,
                                     "abs_err": abs(est - exact), "rel_err": abs(est - exact) / exact if exact else None,
                                     "dgim_bits": dg.memory_bits(), "exact_bits": ex.memory_bits(),
                                     "buckets": dg.num_buckets, "full_window": now - self.e4_start_idx + 1 >= n})

    # ------------------------------------------------------------------ run
    def run(self, total: int):
        prog = Progress("main", total)
        track = self.e4_track
        st = StreamStats()
        for a in iter_articles(self.args.start, self.args.end, root=self.args.root, stats=st):
            self.sha.update(canonical_line(a))
            self.engA.process(a)
            if self.engB is not None:
                self.engB.process(a)
            if track:
                for k in a.keys:
                    cfgs = track.get(k)
                    if cfgs:
                        for dg, ex in cfgs.values():
                            dg.add(a.idx)
                            ex.add(a.idx)
            self.articles += 1
            prog.tick(self.articles, f"windows A {len(self.engA.windows)}"
                      + (f", B {len(self.engB.windows)}" if self.engB else ""))
        self.engA.finish()
        if self.engB is not None:
            self.engB.finish()
        self.stats = st
        self.seconds = time.time() - prog.t0


# --------------------------------------------------------------------------- E5 replays
def e5_run(args, frac: float, ref: dict, total: int) -> dict:
    slots = []
    for day in list_days(args.root, args.start, args.end):
        for name in sorted(os.listdir(os.path.join(args.root, "slim", day))):
            if name.endswith(".jsonl.gz"):
                slots.append(f"{day}/{name[:4]}")
    rng = random.Random(f"{SEED}-{frac}")
    drop = set(rng.sample(slots, round(len(slots) * frac)))
    drop_hash = hashlib.sha256("\n".join(sorted(drop)).encode()).hexdigest()
    rows = []
    state = {"block": None}
    eval_from = day_ts(args.eval_from)

    def on_snap(eng, t):
        if t < eval_from:
            return
        block = int(t // BLOCK)
        if block == state["block"] or block not in ref:
            return
        state["block"] = block
        mine = [k for k, _ in eng.window("168h").top(K)]
        base = ref[block]["168h"]
        g = eng.gaps(t)["168h"]
        rows.append({"t": int(t), "overlap": len(set(mine) & set(base)) / max(len(base), 1),
                     "same_order": mine == base,
                     "gap_168h": g["missing_ratio"], "held": g["insufficient"]})

    eng = GkgEngine(GkgEngineConfig(windows=(("168h", 168.0),), thresholds=(None,), detect=False),
                    on_snapshot=on_snap)
    prog = Progress(f"E5 {frac:.0%}", round(total * (1 - frac)))
    n = 0
    for a in iter_articles(args.start, args.end, root=args.root):
        if f"{iso(a.ts)[:10]}/{iso(a.ts)[11:13]}{iso(a.ts)[14:16]}" in drop:
            continue
        eng.process(a)
        n += 1
        prog.tick(n)
    eng.finish()
    ov = [r["overlap"] for r in rows]
    return {"drop_fraction": frac, "seed": f"{SEED}-{frac}", "slots_total": len(slots), "slots_dropped": len(drop),
            "dropped_sha256": drop_hash, "articles": n, "eval_times": len(rows),
            "overlap_mean": sum(ov) / len(ov) if ov else None, "overlap_min": min(ov) if ov else None,
            "same_order_share": sum(r["same_order"] for r in rows) / len(rows) if rows else None,
            "gap_168h_mean": sum(r["gap_168h"] for r in rows) / len(rows) if rows else None,
            "held_share": sum(r["held"] for r in rows) / len(rows) if rows else None,
            "seconds": round(time.time() - prog.t0, 1), "rows": rows}


# --------------------------------------------------------------------------- summaries
def summarize_e1(main: MainRun) -> list[dict]:
    out = []
    for (name, v), rows in sorted(main.e1.items(), key=lambda kv: (dict(HALF_LIVES)[kv[0][0]],
                                                                    -1 if kv[0][1] == "exact" else float(kv[0][1][6:]))):
        errs = [e for r in rows for e in r["errs"]]
        out.append({
            "window": name, "variant": v, "theta": None if v == "exact" else float(v[6:]), "eval_times": len(rows),
            "keys_mean": round(sum(r["keys"] for r in rows) / len(rows)), "keys_final": rows[-1]["keys"],
            "mb_mean": round(sum(r["bytes"] for r in rows) / len(rows) / 1e6, 2),
            "mb_final": round(rows[-1]["bytes"] / 1e6, 2),
            "overlap_mean": round(sum(r["overlap"] for r in rows) / len(rows), 4),
            "overlap_min": min(r["overlap"] for r in rows),
            "same_order_share": round(sum(r["same_order"] for r in rows) / len(rows), 4),
            "rel_err_mean": sum(errs) / len(errs) if errs else 0.0, "rel_err_max": max(errs) if errs else 0.0,
        })
    return out


def summarize_e2(main: MainRun, events: dict) -> dict:
    out = {}
    for group in ("events", "warmup_reference_only"):
        rows = []
        for ev in events.get(group, []):
            first = day_ts(ev["first_report_utc"][:10]) + int(ev["first_report_utc"][11:13]) * 3600 + \
                int(ev["first_report_utc"][14:16]) * 60
            row = {"id": ev["id"], "name": ev["name"], "first_report_utc": ev["first_report_utc"],
                   "verified": ev.get("verified")}
            for v in ("exact", "theta=0.5"):
                hit = main.e2[v].get(ev["id"])
                row[v] = ({**hit, "time": iso(hit["t"]), "delay_h": round((hit["t"] - first) / 3600, 2)}
                          if hit else {"missed": True})
            rows.append(row)
        out[group] = rows
    return out


def summarize_e2_revised(main: MainRun, events: dict) -> dict:
    """Revised (post-hoc) E2 rule; see the module docstring. Original results stay in res["E2"]."""
    out = {"rule": {"match": "match phrase = last word(s) of a comma-separated part of the key name (word units)",
                    "cutoff_hours_before_first_report": E2_PRE_HOURS, "top": K,
                    "types": "event types unchanged; name matches of other kinds reported as types 불일치",
                    "candidates": f"up to {E2_CANDIDATES} matching keys among all qualified rows after the cut-off, "
                                  f"ordered by best rank then first entry"}}
    for group in ("events", "warmup_reference_only"):
        rows = []
        for ev in events.get(group, []):
            first = utc_ts(ev["first_report_utc"])
            row = {"id": ev["id"], "name": ev["name"], "first_report_utc": ev["first_report_utc"],
                   "types": ev["types"], "verified": ev.get("verified")}
            for v in ("exact", "theta=0.5"):
                cands = list(main.e2r[v].get(ev["id"], {}).values())

                def entry(c):
                    return {"key": c["key"], "rank": c["top20_rank"], "lift": c.get("top20_lift"), "t": c["top20_t"],
                            "time": iso(c["top20_t"]), "delay_h": round((c["top20_t"] - first) / 3600, 2)}
                hits = sorted((c for c in cands if c["type_ok"] and c["top20_t"] is not None),
                              key=lambda c: (c["top20_t"], c["top20_rank"]))
                other = sorted((c for c in cands if not c["type_ok"] and c["top20_t"] is not None),
                               key=lambda c: (c["top20_t"], c["top20_rank"]))
                if hits:
                    res = {"status": "captured", **entry(hits[0])}
                elif other:
                    res = {"status": "types_mismatch", **entry(other[0])}
                else:
                    res = {"status": "missed"}
                top = sorted(cands, key=lambda c: (c["best_rank"], c["first_t"], c["key"]))[:E2_CANDIDATES]
                res["candidates_total"] = len(cands)
                res["candidates"] = [{
                    "key": c["key"], "type_ok": c["type_ok"], "best_rank": c["best_rank"], "best_time": iso(c["best_t"]),
                    "first_time": iso(c["first_t"]), "first_delay_h": round((c["first_t"] - first) / 3600, 2),
                    "top20_time": iso(c["top20_t"]) if c["top20_t"] is not None else None,
                    "top20_delay_h": round((c["top20_t"] - first) / 3600, 2) if c["top20_t"] is not None else None,
                } for c in top]
                row[v] = res
            rows.append(row)
        out[group] = rows
    return out


def summarize_e4(main: MainRun) -> dict:
    rows = [r for r in main.e4_rows if r["full_window"]]
    per = []
    for n, r in E4_CONFIGS:
        sel = [x for x in rows if x["N"] == n and x["r"] == r]
        rel = [x["rel_err"] for x in sel if x["rel_err"] is not None]
        per.append({"N": n, "r": r, "samples": len(sel), "bound": 1 / (2 * (r - 1)),
                    "rel_err_mean": sum(rel) / len(rel) if rel else None, "rel_err_max": max(rel) if rel else None,
                    "abs_err_mean": sum(x["abs_err"] for x in sel) / len(sel) if sel else None,
                    "dgim_bits_mean": sum(x["dgim_bits"] for x in sel) / len(sel) if sel else None,
                    "exact_bits_mean": sum(x["exact_bits"] for x in sel) / len(sel) if sel else None,
                    "by_group": {}})
        for g in sorted({x["group"] for x in sel}):
            gs = [x for x in sel if x["group"] == g]
            grel = [x["rel_err"] for x in gs if x["rel_err"] is not None]
            per[-1]["by_group"][g] = {
                "keys": len({x["key"] for x in gs}),
                "exact_mean": sum(x["exact"] for x in gs) / len(gs),
                "rel_err_mean": sum(grel) / len(grel) if grel else None,
                "dgim_bits_mean": sum(x["dgim_bits"] for x in gs) / len(gs),
                "exact_bits_mean": sum(x["exact_bits"] for x in gs) / len(gs)}
    return {"keys": main.e4_keys, "selected_at": iso(main.eval_from), "configs": per,
            "skipped": getattr(main, "e4_skipped", []),
            "note": "samples only after the key has been tracked for N articles (full window)"}


# --------------------------------------------------------------------------- figures
COL = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "ink": "#0b0b0b",
       "ink2": "#52514e", "grid": "#e4e3df", "surface": "#fcfcfb", "ref": "#8a8984", "violet": "#6250d6"}


def setup_mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR") if f in names),
                "DejaVu Sans")
    plt.rcParams.update({
        "font.family": font, "axes.unicode_minus": False, "font.size": 11,
        "figure.facecolor": COL["surface"], "axes.facecolor": COL["surface"],
        "savefig.facecolor": COL["surface"], "axes.edgecolor": COL["grid"],
        "axes.labelcolor": COL["ink2"], "xtick.color": COL["ink2"], "ytick.color": COL["ink2"],
        "axes.grid": True, "grid.color": COL["grid"], "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
        "axes.titlesize": 13, "axes.titlecolor": COL["ink"], "axes.titlelocation": "left",
        "legend.frameon": False, "lines.linewidth": 2,
    })
    return plt


def figures(res: dict, fig_dir: str) -> list[str]:
    plt = setup_mpl()
    os.makedirs(fig_dir, exist_ok=True)
    made = []
    colors = {"72h": COL["orange"], "168h": COL["blue"], "720h": COL["aqua"]}
    if res.get("E1"):
        fig, ax = plt.subplots(figsize=(8, 5))
        for name, _ in HALF_LIVES:
            rows = [r for r in res["E1"]["rows"] if r["window"] == name]
            if not rows:
                continue
            xs = [r["mb_mean"] for r in rows]
            ys = [r["overlap_mean"] for r in rows]
            ax.plot(xs, ys, "o-", color=colors[name], label=f"{name} 창")
            for r in rows:
                ax.annotate("정확" if r["theta"] is None else f"θ={r['theta']:g}", (r["mb_mean"], r["overlap_mean"]),
                            textcoords="offset points", xytext=(5, -12), fontsize=9, color=COL["ink2"])
        ax.set_xscale("log")
        ax.set_xlabel("감쇠 점수 메모리 (MB, 평가 시점 평균, 로그 축)")
        ax.set_ylabel("Top-20 겹침 (평균)")
        ax.set_title("E1 θ별 메모리와 Top-20 겹침")
        ax.legend()
        fig.tight_layout()
        p = os.path.join(fig_dir, "e1_memory_vs_overlap.png")
        fig.savefig(p, dpi=180)
        plt.close(fig)
        made.append(p)
    if res.get("E2"):
        rows = res["E2"]["events"]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        xs = range(len(rows))
        for j, (v, c) in enumerate((("exact", COL["blue"]), ("theta=0.5", COL["orange"]))):
            ys = [r[v].get("delay_h") for r in rows]
            ax.bar([x + (j - 0.5) * 0.38 for x in xs], [y if y is not None else 0 for y in ys], width=0.38,
                   color=c, label="정확" if v == "exact" else "θ=0.5")
            for x, y in zip(xs, ys):
                if y is None:
                    ax.text(x + (j - 0.5) * 0.38, 0.5, "놓침", ha="center", fontsize=9, color=c, rotation=90)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([r["id"] for r in rows])
        ax.set_ylabel("첫 보도 → 신규 Top 20 진입 (시간)")
        ax.set_title("E2 신규 이슈 포착 지연")
        ax.legend()
        fig.tight_layout()
        p = os.path.join(fig_dir, "e2_delay.png")
        fig.savefig(p, dpi=180)
        plt.close(fig)
        made.append(p)
    if res.get("E2_revised"):
        rows = res["E2_revised"]["events"]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        xs = range(len(rows))
        for j, (v, c) in enumerate((("exact", COL["blue"]), ("theta=0.5", COL["orange"]))):
            label = "정확" if v == "exact" else "θ=0.5"
            for x, r in zip(xs, rows):
                h = r[v]
                xx = x + (j - 0.5) * 0.38
                if h["status"] == "captured":
                    ax.bar([xx], [h["delay_h"]], width=0.38, color=c, label=label)
                    label = None                       # one legend entry per variant
                else:
                    ax.text(xx, 0.5, "types 불일치" if h["status"] == "types_mismatch" else "놓침",
                            ha="center", fontsize=9, color=c, rotation=90)
        ax.axhline(0, color=COL["ink2"], lw=0.8)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([r["id"] for r in rows])
        ax.set_ylabel("첫 보도 → 신규 Top 20 진입 (시간)")
        ax.set_title("E2 사후 수정 규칙 (단어 단위 매칭, 첫 보도 -24h 이후)")
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend()
        fig.tight_layout()
        p = os.path.join(fig_dir, "e2_delay_revised.png")
        fig.savefig(p, dpi=180)
        plt.close(fig)
        made.append(p)
    if res.get("E4") and res["E4"]["configs"]:
        cf = res["E4"]["configs"]
        fig, axs = plt.subplots(1, 2, figsize=(10, 4.2))
        labels = [f"N={c['N']:,}\nr={c['r']}" for c in cf]
        axs[0].bar(labels, [c["rel_err_mean"] or 0 for c in cf], color=COL["violet"], label="평균")
        axs[0].plot(labels, [c["bound"] for c in cf], "_", markersize=40, color=COL["orange"], label="이론 상한")
        axs[0].set_title("E4 DGIM 상대 오차")
        axs[0].legend()
        x = range(len(cf))
        axs[1].bar([i - 0.2 for i in x], [c["dgim_bits_mean"] or 0 for c in cf], width=0.4, color=COL["violet"], label="DGIM")
        axs[1].bar([i + 0.2 for i in x], [c["exact_bits_mean"] or 0 for c in cf], width=0.4, color=COL["ref"], label="정확(위치 목록)")
        axs[1].set_xticks(list(x))
        axs[1].set_xticklabels(labels)
        axs[1].set_yscale("log")
        axs[1].set_title("키당 논리 메모리 (비트, 로그)")
        axs[1].legend()
        fig.tight_layout()
        p = os.path.join(fig_dir, "e4_dgim.png")
        fig.savefig(p, dpi=180)
        plt.close(fig)
        made.append(p)
    if res.get("E5") and res["E5"]["runs"]:
        fig, ax = plt.subplots(figsize=(9, 4))
        for run, c in zip(res["E5"]["runs"], (COL["blue"], COL["orange"])):
            ts = [datetime.fromtimestamp(r["t"], timezone.utc) for r in run["rows"]]
            ax.plot(ts, [r["overlap"] for r in run["rows"]], color=c, label=f"슬롯 {run['drop_fraction']:.0%} 제거")
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("168h Top-20 겹침")
        ax.set_title("E5 빈 구간 견고성")
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        p = os.path.join(fig_dir, "e5_overlap.png")
        fig.savefig(p, dpi=180)
        plt.close(fig)
        made.append(p)
    return made


# --------------------------------------------------------------------------- report
def pct(x):
    return "–" if x is None else f"{x:.1%}"


def report(res: dict, warnings: list[str]) -> str:
    m = res["meta"]
    L = ["## GKG experiments (E1–E5)", ""]
    if warnings:
        L += [f"> **경고:** {w}" for w in warnings] + [""]
    prov = res.get("provenance") or {}
    L += [f"- 실행 명령: `{m['command']}`",
          f"- 기간: {m['range']['start']} .. {m['range']['end']} (UTC), 평가 시작 {m['eval_from']}, 6시간 간격",
          f"- 입력 해시: stream SHA-256 `{m['input'].get('stream_sha256')}` ({m['input'].get('articles', 0):,} articles; "
          f"정의는 input_manifest.json 과 같음)",
          f"- git {m['git']['commit']} (dirty={m['git']['dirty']}), 시드 {SEED}, 결과 `results.json`, 그림 `figures/`",
          f"- 실행 시간: " + ", ".join(f"{k} {v / 60:.1f} min" for k, v in m["seconds"].items())
          + f", peak RSS {m.get('peak_rss_gb')} GB", ""]
    if len({json.dumps(p, sort_keys=True) for p in prov.values()}) > 1:
        L += ["실험별 실행 기록 (`--only` 로 일부만 다시 돌린 경우 실험마다 다름):", "",
              "| 실험 | 명령 | 실행 시각 (UTC) | git (시작 시) | 입력 해시 / 기사 수 |", "|---|---|---|---|---|"]
        for e in sorted(prov):
            p = prov[e]
            L.append(f"| {e} | `{p['command']}` | {p['created_utc']} | {(p['git'].get('commit') or '')[:7]} "
                     f"(dirty={p['git'].get('dirty')}) | {(p.get('stream_sha256') or '')[:12]}… / {p.get('articles', 0):,} |")
        L.append("")
    if res.get("E1"):
        L += ["### E1 메모리와 순위 (정확 기준 대비)", "",
              "| 창 | θ | 키 수(평균) | 메모리 MB(평균, 키 문자열 제외 추정) | Top-20 겹침 평균 / 최소 | 순서 동일 | 점수 상대오차 평균 / 최대 |",
              "|---|---|---:|---:|---:|---:|---:|"]
        for r in res["E1"]["rows"]:
            L.append(f"| {r['window']} | {'정확' if r['theta'] is None else r['theta']} | {r['keys_mean']:,} | "
                     f"{r['mb_mean']} | {r['overlap_mean']:.3f} / {r['overlap_min']:.2f} | {pct(r['same_order_share'])} | "
                     f"{pct(r['rel_err_mean'])} / {pct(r['rel_err_max'])} |")
        L += ["", "[해석] 점수 상대오차는 각 평가 시점의 정확 기준 Top-20 키에서 |정확−근사|/정확 을 모두 모아 평균·최대를 낸 값. "
              "메모리는 dict + float 추정(키 문자열 제외): 키 문자열은 모든 창이 공유하므로 따로 세며, 실제 프로세스 메모리는 더 크다.",
              "",
              "[해석] Top-20 겹침이 θ와 상관없이 1.000인 것은 정상이다. 상위 20개 키의 점수는 수천~수만이라 θ(≤ 1)보다 훨씬 커서 "
              "삭제 대상이 되지 않고, 삭제는 점수가 θ 밑으로 떨어진 꼬리 키에서만 일어난다. 그러므로 E1은 \"메모리를 줄여도 "
              "상위 순위는 그대로\"를 보여 줄 뿐이고, θ가 순위 판단에 주는 영향(막 떠오르는 키를 일찍 지워 늦게 잡는지)은 "
              "E2(정확 vs θ=0.5 신규 포착)에서 본다.", ""]
    if res.get("E2"):
        L += ["### E2 신규 이슈 포착 (72시간 신규 Top 20, 첫 진입)", "",
              "| 사건 | 첫 보도 (UTC) | 정확: 진입 시각 / 지연 h / 키 | θ=0.5: 진입 시각 / 지연 h / 키 |", "|---|---|---|---|"]

        def cell(h):
            return "놓침" if h.get("missed") else f"{h['time']} / {h['delay_h']:+.1f} / {h['key']} (#{h['rank']})"
        for r in res["E2"]["events"]:
            L.append(f"| {r['id']} {r['name']} | {r['first_report_utc'][:16].replace('T', ' ')} | {cell(r['exact'])} | {cell(r['theta=0.5'])} |")
        if res["E2"].get("warmup_reference_only"):
            L += ["", "참고(준비 기간 사건, 신규 판별은 7일 준비 뒤라 포착 대상 아님):", ""]
            for r in res["E2"]["warmup_reference_only"]:
                L.append(f"- {r['id']} {r['name']}: 정확 {cell(r['exact'])}; θ=0.5 {cell(r['theta=0.5'])}")
        L.append("")
    if res.get("E2_revised"):
        rv = res["E2_revised"]
        L += ["### E2 사후 수정 규칙 (2026-11-17, 원래 결과를 본 뒤 추가; 위 원래 표는 그대로 둠)", "",
              f"- 이름 매칭: 사건 match 문자열이 키 이름의 쉼표로 나뉜 한 부분의 끝 단어(들)과 단어 단위로 같아야 함 "
              f"(\"milton\" → \"hurricane milton\", \"milton, florida\" 는 맞고 \"milton keynes\" 는 아님). 예외 목록 없음.",
              f"- 진입 시각이 first_report_utc − {E2_PRE_HOURS}h 이후인 것만 인정. types 는 고치지 않음: 이름은 맞지만 "
              f"종류가 types 밖이면 \"types 불일치\".",
              f"- 후보: 수정 규칙에 맞는 키를 컷오프 이후 신규 조건 통과 행 전체(순위 20 밖 포함)에서 모아 최고 순위 순 상위 {E2_CANDIDATES}개.",
              "", "| 사건 | 첫 보도 (UTC) | types | 정확: 결과 / 진입 시각 / 지연 h / 키 | θ=0.5: 결과 / 진입 시각 / 지연 h / 키 |",
              "|---|---|---|---|---|"]

        def rcell(h):
            if h["status"] == "missed":
                return "놓침"
            lab = "포착" if h["status"] == "captured" else "types 불일치"
            return f"{lab} / {h['time']} / {h['delay_h']:+.1f} / {h['key']} (#{h['rank']})"
        for r in rv["events"]:
            L.append(f"| {r['id']} {r['name']} | {r['first_report_utc'][:16].replace('T', ' ')} | {','.join(r['types'])} | "
                     f"{rcell(r['exact'])} | {rcell(r['theta=0.5'])} |")
        if rv.get("warmup_reference_only"):
            L += ["", "참고(준비 기간 사건):", ""]
            for r in rv["warmup_reference_only"]:
                L.append(f"- {r['id']} {r['name']}: 정확 {rcell(r['exact'])}; θ=0.5 {rcell(r['theta=0.5'])}")
        L += ["", f"사건별 후보 키 (정확, 최고 순위 순 상위 {E2_CANDIDATES}개; 첫 등장 = 컷오프 이후 처음 신규 조건 통과, "
              "Top 20 = 처음 20위 안에 든 시각):", "",
              "| 사건 | 키 | types 맞음 | 최고 순위 (시각) | 첫 등장 (지연 h) | Top 20 진입 (지연 h) |", "|---|---|---|---|---|---|"]
        for r in rv["events"] + rv.get("warmup_reference_only", []):
            cs = r["exact"]["candidates"]
            if not cs:
                L.append(f"| {r['id']} | (후보 없음) | | | | |")
            for c in cs:
                L.append(f"| {r['id']} | {c['key']} | {'예' if c['type_ok'] else '아니오'} | {c['best_rank']} ({c['best_time']}) | "
                         f"{c['first_time']} ({c['first_delay_h']:+.1f}) | "
                         + (f"{c['top20_time']} ({c['top20_delay_h']:+.1f})" if c["top20_time"] else "–") + " |")
        L.append("")
    if res.get("E3"):
        L += ["### E3 반짝/꾸준 (00:00 UTC, 168h Top 2,000 후보)", ""]
        missing = [d for d in E3_DATES if d not in res["E3"]["dates"]]
        if missing:
            L += [f"평가 날짜 중 기간 밖이거나 스냅샷이 없는 날: {', '.join(missing)}", ""]
        for d, x in res["E3"]["dates"].items():
            cnt = ", ".join(f"{k} {v}" for k, v in sorted(x["counts"].items()))
            L.append(f"**{d}** (상태 {x['status']}; 후보 {x['candidates']}: {cnt})")
            for g, ko in (("rising", "최근 급증"), ("cooling", "식는 중"), ("steady", "꾸준")):
                rows = x["groups"].get(g, [])
                L.append(f"- {ko}: " + (", ".join(f"{r['key'][2:]} ({r['ratio']})" for r in rows) or "없음"))
            L.append("")
        if res["E3"]["daily"]:
            keys = sorted({k for r in res["E3"]["daily"] for k in r["keys"]})
            L += ["참고 키 분류 변화 (매일 00:00 UTC, ratio / 라벨):", "",
                  "| 날짜 | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
            for r in res["E3"]["daily"]:
                if r["date"] < "2024-10-15" and r["date"] not in E3_DATES:
                    continue
                L.append(f"| {r['date']} | " + " | ".join(
                    f"{r['keys'][k]['ratio']} {r['keys'][k]['label']}" if k in r["keys"] else "–" for k in keys) + " |")
            L.append("")
    if res.get("E4"):
        L += ["### E4 DGIM (근사) vs 정확 슬라이딩 개수", "",
              f"키 {len(res['E4']['keys'] or {})}개, {res['E4']['selected_at']}에 선택: "
              + ", ".join(f"{k} [{g}]" for k, g in (res["E4"]["keys"] or {}).items())
              + ("".join(f"; 건너뜀 {x}" for x in res["E4"].get("skipped", []))), "",
              "| N | r | 표본 | 상대오차 평균 / 최대 | 이론 상한 | 키당 비트 DGIM / 정확 |", "|---:|---:|---:|---:|---:|---:|"]
        for c in res["E4"]["configs"]:
            L.append(f"| {c['N']:,} | {c['r']} | {c['samples']:,} | {pct(c['rel_err_mean'])} / {pct(c['rel_err_max'])} | "
                     f"{pct(c['bound'])} | {c['dgim_bits_mean'] or 0:,.0f} / {c['exact_bits_mean'] or 0:,.0f} |")
        L += ["", "빈도 그룹별(평균 정확 개수, 상대오차, 비트 DGIM / 정확):", ""]
        for c in res["E4"]["configs"]:
            for g, x in c["by_group"].items():
                L.append(f"- N={c['N']:,} r={c['r']} · {g}: {x['exact_mean']:,.0f}, {pct(x['rel_err_mean'])}, "
                         f"{x['dgim_bits_mean']:,.0f} / {x['exact_bits_mean']:,.0f}")
        L.append("")
    if res.get("E5"):
        L += ["### E5 빈 구간 견고성 (168h 창, 정확)", "",
              "| 제거 비율 | 제거 슬롯 | 평가 시점 | Top-20 겹침 평균 / 최소 | 순서 동일 | 168h 빈 구간 평균 | 근거 부족 보류 비율 |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
        for r in res["E5"]["runs"]:
            L.append(f"| {r['drop_fraction']:.0%} | {r['slots_dropped']:,}/{r['slots_total']:,} | {r['eval_times']} | "
                     f"{(r['overlap_mean'] or 0):.3f} / {(r['overlap_min'] or 0):.2f} | {pct(r['same_order_share'])} | "
                     f"{pct(r['gap_168h_mean'])} | {pct(r['held_share'])} |")
        L += ["", "제거 슬롯 목록은 시드로 재현되며 SHA-256 은 results.json 의 dropped_sha256.", ""]
    return "\n".join(L)


def write_top_section(path: str, tag: str, block: str) -> None:
    """Put the section right under the title of summary.md (replace it if it exists)."""
    begin, end = f"<!-- {tag}:begin -->", f"<!-- {tag}:end -->"
    block = f"{begin}\n{block.strip()}\n{end}"
    text = "# GKG experiments summary\n"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        text = head + block + rest.split(end, 1)[1]
    else:
        lines = text.split("\n", 1)
        text = lines[0] + "\n\n" + block + "\n" + (("\n" + lines[1].lstrip("\n")) if len(lines) > 1 else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def load_events(warnings: list[str]) -> dict:
    if not os.path.exists(EVENTS_PATH):
        warnings.append(f"{os.path.relpath(EVENTS_PATH, REPO)} 가 없어 E2·E3 참고 사건 없이 실행했다.")
        return {}
    with open(EVENTS_PATH, encoding="utf-8") as f:
        ev = json.load(f)
    for group in ("events", "warmup_reference_only", "flash_vs_steady_reference"):
        for e in ev.get(group, []):
            if not e.get("verified", False):
                warnings.append(f"gkg_events.json 의 {group}/{e['id']} 는 verified=false (출처 미확인).")
    return ev


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2024-09-15")
    ap.add_argument("--end", default="2024-11-14")
    ap.add_argument("--eval-from", default="2024-10-01")
    ap.add_argument("--quick", action="store_true", help="one day (2024-10-01) into experiments/results/gkg/quick/")
    ap.add_argument("--only", help="comma list, e.g. E1,E2")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args()
    if args.quick:
        args.start = args.end = args.eval_from = "2024-10-01"
    exps = set(ALL) if not args.only else {x.strip().upper() for x in args.only.split(",") if x.strip()}
    bad = exps - set(ALL)
    if bad:
        sys.exit(f"unknown experiment(s): {sorted(bad)}")
    out_dir = os.path.join(RESULTS, "quick") if args.quick else RESULTS
    os.makedirs(out_dir, exist_ok=True)
    command = "python " + " ".join(shlex.quote(a) for a in
                                   [os.path.relpath(sys.argv[0], REPO).replace(os.sep, "/"), *sys.argv[1:]])
    warnings: list[str] = []
    events = load_events(warnings)
    # provenance is taken before the replay: files edited while it runs must not end up in the record
    git_start = _git_state()
    code_start = {os.path.basename(m.__file__): _file_sha256(m.__file__)
                  for m in (gkg, gkg_engine, gkg_cache, dgim_mod, sliding)} \
        | {"run_gkg_experiments.py": _file_sha256(os.path.abspath(__file__))}

    total = count_articles(args.root, args.start, args.end)
    need_main = bool(exps & set(ALL))
    est_main = total / RATE_MAIN if need_main else 0
    est_e5 = sum(total * (1 - f) for f in E5_DROPS) / RATE_E5 if "E5" in exps else 0
    print(f"GKG experiments {sorted(exps)} on {args.start} .. {args.end}: about {total:,} articles")
    print(f"estimated time: main replay {est_main / 60:.0f} min + E5 {est_e5 / 60:.0f} min "
          f"= {(est_main + est_e5) / 60:.0f} min (rates {RATE_MAIN:,.0f}/s and {RATE_E5:,.0f}/s; actual ETA below)")
    print("estimated peak memory: ≤ 9 GB for the full range (engine A 12 + engine B 3 decay windows, 2 detectors) — one pass")

    res = {}
    old_path = os.path.join(out_dir, "results.json")
    if args.only and os.path.exists(old_path):
        with open(old_path, encoding="utf-8") as f:
            res = json.load(f)
    old_meta = res.get("meta")
    seconds = {}
    main_run = MainRun(args, exps, events)
    main_run.run(total)
    seconds["main"] = main_run.seconds
    print(f"main replay done: {main_run.articles:,} articles in {main_run.seconds / 60:.1f} min")
    if "E1" in exps:
        res["E1"] = {"thetas": list(THETAS), "k": K, "rows": summarize_e1(main_run)}
    if "E2" in exps:
        res["E2"] = summarize_e2(main_run, events)
        res["E2_revised"] = summarize_e2_revised(main_run, events)
    if "E3" in exps:
        res["E3"] = main_run.e3
    if "E4" in exps:
        res["E4"] = summarize_e4(main_run)
    if "E5" in exps:
        runs = []
        for frac in E5_DROPS:
            r = e5_run(args, frac, main_run.ref_top20, total)
            seconds[f"E5 {frac:.0%}"] = r["seconds"]
            print(f"E5 {frac:.0%}: overlap mean {r['overlap_mean']}, {r['seconds'] / 60:.1f} min")
            runs.append(r)
        res["E5"] = {"seed": SEED, "runs": runs}
    rss = peak_rss_bytes()
    res["meta"] = {
        "command": command, "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "experiments_run": sorted(exps), "range": {"start": args.start, "end": args.end}, "eval_from": args.eval_from,
        "input": {"stream_sha256": main_run.sha.hexdigest(), "articles": main_run.articles,
                  "duplicate_urls_removed": main_run.stats.duplicate_urls,
                  "stream_sha256_def": "sha256 over canonical_line(a) (same as input_manifest.json)"},
        "git": git_start, "git_end": _git_state(), "python": sys.version.split()[0], "seed": SEED,
        "code": code_start,
        "settings": {"thetas": list(THETAS), "k": K, "block_hours": BLOCK // 3600,
                     "engine": {k: v for k, v in GkgEngineConfig().__dict__.items() if k != "evidence_path"},
                     "e3_dates": list(E3_DATES), "e4_configs": [list(c) for c in E4_CONFIGS], "e4_ranks": E4_RANKS,
                     "e5_drops": list(E5_DROPS)},
        "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "peak_rss_gb": round(rss / 1e9, 2) if rss else None, "warnings": warnings,
    }
    if res["meta"]["git_end"] != git_start:
        warnings.append(f"git 상태가 실행 중에 바뀌었다(시작 {git_start}, 끝 {res['meta']['git_end']}). "
                        "기록한 git·코드 해시는 시작 시점 값이다.")
        res["meta"]["warnings"] = warnings
    prov = res.get("provenance")
    if prov is None:        # results written before provenance existed: all from the run in old meta
        old = old_meta or {}
        prov = {e: {"command": old.get("command"), "created_utc": old.get("created_utc"), "git": old.get("git", {}),
                    "stream_sha256": (old.get("input") or {}).get("stream_sha256"),
                    "articles": (old.get("input") or {}).get("articles"),
                    "note": "git/code recorded at the end of that run"}
                for e in old.get("experiments_run", []) if e in res}
    for e in exps:
        prov[e] = {"command": command, "created_utc": res["meta"]["created_utc"], "git": git_start,
                   "stream_sha256": main_run.sha.hexdigest(), "articles": main_run.articles}
    res["provenance"] = prov
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
        f.write("\n")
    made = figures(res, os.path.join(out_dir, "figures"))
    text = report(res, warnings)
    write_top_section(os.path.join(out_dir, "summary.md"), "gkg_experiments", text)
    print(text)
    print("\nwrote " + ", ".join(os.path.relpath(p, REPO) for p in [old_path, *made]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
