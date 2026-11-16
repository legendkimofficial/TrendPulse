"""Replay the GKG stream through GkgEngine and record Top K, key counts and speed.

  python scripts/gkg_run.py --start 2024-10-01 --end 2024-10-01
  python scripts/gkg_run.py --start 2024-09-15 --end 2024-11-14 --windows 72h --theta 0.5

Writes experiments/results/gkg/engine_<start>_<end>_<windows>_<variants>.json and a
matching section in experiments/results/gkg/summary.md.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trendpulse import gkg, gkg_engine, gkg_evidence  # noqa: E402
from trendpulse.gkg import (DEFAULT_ROOT, REPO_ROOT, StreamStats, _file_sha256, _git_state,  # noqa: E402
                            _rel, canonical_line, iter_articles, peak_rss_bytes, write_summary_section)
from trendpulse.gkg_engine import HALF_LIVES, GkgEngine, GkgEngineConfig, variant_name  # noqa: E402

RESULTS = os.path.join(REPO_ROOT, "experiments", "results", "gkg")


def parse_theta(s: str):
    out = []
    for x in s.split(","):
        x = x.strip().lower()
        out.append(None if x in ("none", "exact") else float(x))
    return tuple(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--windows", default=",".join(n for n, _ in HALF_LIVES), help="e.g. 72h,168h,720h")
    ap.add_argument("--theta", default="none", help="comma list; 'none' = exact (e.g. none,0.5)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--top-m", type=int, default=2000)
    ap.add_argument("--snapshot-every", type=float, default=60.0, help="minutes of stream time")
    ap.add_argument("--evidence", default=gkg_evidence.DEFAULT_PATH, help="sqlite path")
    ap.add_argument("--no-evidence", action="store_true")
    ap.add_argument("--out", help="result JSON (default under experiments/results/gkg/)")
    args = ap.parse_args()

    hl = dict(HALF_LIVES)
    names = [w.strip() for w in args.windows.split(",") if w.strip()]
    unknown = [w for w in names if w not in hl]
    if unknown:
        sys.exit(f"unknown window(s): {unknown}; choose from {list(hl)}")
    thetas = parse_theta(args.theta)
    cfg = GkgEngineConfig(windows=tuple((n, hl[n]) for n in names), thresholds=thetas,
                          top_m=args.top_m, snapshot_every_minutes=args.snapshot_every,
                          evidence_path=None if args.no_evidence else args.evidence)
    tag = f"{args.start}_{args.end}_{'-'.join(names)}_{'-'.join(variant_name(t) for t in thetas)}"
    out = args.out or os.path.join(RESULTS, f"engine_{tag}.json")
    command = "python " + " ".join(shlex.quote(a) for a in
                                   [os.path.relpath(sys.argv[0]).replace(os.sep, "/"), *sys.argv[1:]])

    eng = GkgEngine(cfg)
    st = StreamStats()
    sha = hashlib.sha256()
    t_engine = t_hash = 0.0
    pc = time.perf_counter
    t0 = pc()
    for a in iter_articles(args.start, args.end, root=args.root, stats=st):
        t1 = pc()
        sha.update(canonical_line(a))
        t2 = pc()
        eng.process(a)
        t3 = pc()
        t_hash += t2 - t1
        t_engine += t3 - t2
    t1 = pc()
    eng.finish()
    t_engine += pc() - t1
    wall = pc() - t0
    t_read = wall - t_engine - t_hash
    n = eng.articles

    tops = {f"{w}/{v}": eng.top(w, args.top, v) for (w, v) in eng.windows}
    mem = eng.memory_report()
    if "evidence" in mem:
        mem["evidence"]["path"] = _rel(mem["evidence"]["path"])
    eng.close()
    rss = peak_rss_bytes()
    any_w = next(iter(eng.windows.values()))

    result = {
        "kind": "gkg_engine_run", "version": 1,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": command,
        "code": {"git": _git_state(), **{f"{m.__name__.split('.')[-1]}_py_sha256": _file_sha256(m.__file__)
                                         for m in (gkg, gkg_engine, gkg_evidence)}},
        "python": sys.version.split()[0],
        "seed": None,
        "input": {"root": _rel(args.root), "start": args.start, "end": args.end, "articles": n,
                  "stream_sha256": sha.hexdigest(),
                  "stream_sha256_def": "same as input_manifest.json hashes.stream_sha256 (default GkgConfig)",
                  "duplicate_urls_removed": st.duplicate_urls},
        "config": {**cfg.__dict__, "evidence_path": _rel(cfg.evidence_path) if cfg.evidence_path else None},
        "snapshots": eng.snapshots,
        "last_snapshot_utc": datetime.fromtimestamp(any_w.snap_t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if any_w.snap_t is not None else None,
        "timing": {
            "wall_s": round(wall, 2), "read_s": round(t_read, 2), "engine_s": round(t_engine, 2),
            "hash_s": round(t_hash, 2),
            "articles_per_s_engine": round(n / t_engine) if t_engine else None,
            "articles_per_s_end_to_end": round(n / (wall - t_hash)) if wall > t_hash else None,
            "note": "engine_s = decay updates + snapshots + evidence writes; end-to-end excludes the hash",
        },
        "peak_rss_mb": round(rss / 1e6, 1) if rss else None,
        "memory": mem,
        "top": tops,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    import json
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")

    text = report(result)
    print(text)
    write_summary_section(os.path.join(os.path.dirname(os.path.abspath(out)), "summary.md"),
                          f"engine_{tag}",
                          f"## Engine run {tag}\n\n- command: `{command}`\n- result: `{_rel(out)}`\n"
                          f"- git: {result['code']['git']['commit']} (dirty={result['code']['git']['dirty']})\n\n"
                          f"```\n{text}\n```")
    print(f"\nwrote {_rel(out)}")
    return 0


def report(r: dict) -> str:
    tm, mem = r["timing"], r["memory"]
    lines = [f"input   : {r['input']['start']} .. {r['input']['end']}, {r['input']['articles']:,} articles, "
             f"{r['snapshots']} snapshots, stream sha256 {r['input']['stream_sha256'][:16]}…",
             f"speed   : engine {tm['articles_per_s_engine']:,} articles/s, end-to-end "
             f"{tm['articles_per_s_end_to_end']:,} articles/s (read {tm['read_s']} s, engine {tm['engine_s']} s)",
             f"memory  : peak RSS {r['peak_rss_mb']} MB; decay structures ≈ {mem['decay_total_bytes'] / 1e6:.1f} MB "
             f"(key strings {mem['key_strings']['unique']:,} unique, {mem['key_strings']['bytes'] / 1e6:.1f} MB)"]
    for row in mem["decay_windows"]:
        lines.append(f"  {row['window']:>5}/{row['variant']:<10} keys {row['keys']:,} (peak {row['peak_keys']:,}, "
                     f"deleted {row['deleted_total']:,}), ≈ {row['estimate_bytes'] / 1e6:.1f} MB")
    if "evidence" in mem:
        ev = mem["evidence"]
        lines.append(f"evidence: {ev['tracked_keys']:,} tracked keys, {ev['articles']:,} articles, "
                     f"{ev['postings']:,} postings, file {ev['file_bytes'] / 1e6:.1f} MB ({ev['path']})")
    for name, rows in r["top"].items():
        lines.append(f"--- Top {len(rows)} {name} (score, bias-corrected rate per hour) at {r['last_snapshot_utc']}")
        for i, x in enumerate(rows, 1):
            rate = f"{x['rate_per_hour']:.1f}/h" if x["rate_per_hour"] is not None else "n/a"
            lines.append(f"  {i:>2}. {x['key']:<40} {x['score']:>9.1f}  {rate}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
