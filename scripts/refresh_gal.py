"""Periodic GAL refresh for the submission collector (stretch goal).

    python scripts/refresh_gal.py --days 7          # refresh to the last 7 complete UTC days
    python scripts/refresh_gal.py --check           # idempotence check (see below)

Reuses the submission collector (scripts/fetch_gdelt_gal.py): the raw directory
is append-only, downloads are idempotent (a finished batch is never
re-downloaded), and normalization always rebuilds the whole fixed input from
the raw files, so the same URL can never be counted twice no matter how often a
batch is reprocessed or the app restarts. Progress and the last-success
timestamp live in data/processed/gdelt/refresh_state.json and are updated only
after a fully successful normalize (atomic replace).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_gdelt_gal as fg  # noqa: E402

STATE = fg.OUT / "refresh_state.json"


def last_complete_days(n: int) -> tuple[str, str]:
    today = (_date.fromordinal(datetime.now(timezone.utc).date().toordinal()) - timedelta(days=1))
    end = today.isoformat()
    start = (today - timedelta(days=n - 1)).isoformat()
    return start, end


def refresh(days: int) -> dict:
    start, end = last_complete_days(days)
    print(f"[refresh] window {start} .. {end} (last {days} complete UTC days)")
    state = {"window": [start, end], "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    fg.cmd_collect(start, end, workers=8)          # idempotent; exits 2 on download failure
    manifest = fg.cmd_normalize()                  # full deterministic rebuild
    # atomic state update only after full success
    state.update({"last_success_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "input_sha256": manifest["input_sha256"],
                  "articles": manifest["counts"]["kept"],
                  "raw_batches": len(manifest["raw_files"]),
                  "gaps": manifest["collection_gaps"]})
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)
    print(f"[refresh] OK last_success={state['last_success_ts']} "
          f"sha256={state['input_sha256'][:12]}… articles={state['articles']:,}")
    return state


def check() -> None:
    """Idempotence proof: re-run collect+normalize on the same window; the input
    hash must not change (same batches reprocessed, no URL double counting)."""
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    start, end = st.get("window", last_complete_days(7))
    h0 = json.loads((fg.OUT / "manifest.json").read_text(encoding="utf-8"))["input_sha256"]
    fg.cmd_collect(start, end, workers=8)
    m = fg.cmd_normalize()
    same = m["input_sha256"] == h0
    print(f"[check] before={h0[:12]}… after={m['input_sha256'][:12]}… "
          f"-> {'OK (unchanged)' if same else 'MISMATCH'}")
    sys.exit(0 if same else 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        check()
    refresh(args.days)


if __name__ == "__main__":
    main()
