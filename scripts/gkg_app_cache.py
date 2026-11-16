"""Build (or reuse) the dashboard cache and record its build statistics.

  python scripts/gkg_app_cache.py --start 2024-09-15 --end 2024-10-14
  python scripts/gkg_app_cache.py                 (every downloaded day)

Writes experiments/results/gkg/app_cache_<start>_<end>.json and a section in summary.md.
The dashboard (python app_gkg.py) reuses the same cache when started with the same range.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trendpulse.gkg import DEFAULT_ROOT, REPO_ROOT, _git_state, write_summary_section  # noqa: E402
from trendpulse.gkg_cache import CacheConfig, ensure_cache  # noqa: E402

RESULTS = os.path.join(REPO_ROOT, "experiments", "results", "gkg")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--theta", type=float, default=None)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    meta = ensure_cache(CacheConfig(start=args.start, end=args.end, theta=args.theta),
                        root=args.root, rebuild=args.rebuild)
    command = "python " + " ".join(shlex.quote(a) for a in
                                   [os.path.relpath(sys.argv[0]).replace(os.sep, "/"), *sys.argv[1:]])
    rec = {"kind": "gkg_app_cache", "command": command, "git": _git_state(), **meta}
    rec["memory"].get("evidence", {}).pop("path", None)
    tag = f"{meta['range']['start']}_{meta['range']['end']}" + ("" if args.theta is None else f"_theta{args.theta:g}")
    out = os.path.join(RESULTS, f"app_cache_{tag}.json")
    os.makedirs(RESULTS, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    mem = meta["memory"]
    windows = ", ".join(f"{r['window']} {r['keys']:,} keys" for r in mem["decay_windows"])
    act = mem.get("activity_tracker", {})
    ev = mem.get("evidence", {})
    lines = [
        f"range      : {meta['range']['start']} .. {meta['range']['end']}, {meta['articles']:,} articles, "
        f"{meta['snapshots']} cached 6-hour snapshots, {meta['new_events']} new-issue entry events",
        f"build      : {meta['seconds']} s ({meta['articles_per_s']:,} articles/s), peak RSS {meta['peak_rss_mb']} MB"
        + ("" if meta.get("rebuilt") else " (numbers from the earlier build; cache reused)"),
        f"decay      : ≈ {mem['decay_total_bytes'] / 1e6:.1f} MB ({windows})",
        f"tracker    : {act.get('tracked_keys', 0):,} keys, ≈ {act.get('estimate_bytes', 0) / 1e6:.1f} MB",
        f"evidence   : {ev.get('tracked_keys', 0):,} keys, {ev.get('articles', 0):,} articles, "
        f"{ev.get('postings', 0):,} postings (cap {ev.get('per_day_cap')}/day, "
        f"{ev.get('truncated_postings', 0):,} truncated), {ev.get('file_bytes', 0) / 1e6:.1f} MB",
        f"dgim       : {mem['dgim']['keys']:,} keys, {mem['dgim']['buckets']:,} buckets",
    ]
    text = "\n".join(lines)
    print(text)
    write_summary_section(os.path.join(RESULTS, "summary.md"), f"app_cache_{tag}",
                          f"## Dashboard cache {tag}\n\n- command: `{command}`\n"
                          f"- result: `experiments/results/gkg/app_cache_{tag}.json`\n"
                          f"- git: {rec['git']['commit']} (dirty={rec['git']['dirty']}), fingerprint "
                          f"{meta['fingerprint'][:16]}…\n\n```\n{text}\n```")
    print(f"\nwrote {os.path.relpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
