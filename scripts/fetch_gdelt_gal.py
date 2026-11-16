"""GDELT GAL (Global Article List) collector, normalizer and replay-contract
builder for the Trend Pulse submission.

Source (documented 2026-11-16 from the official dataset):
  https://data.gdeltproject.org/gdeltv3/gal/YYYYMMDDHHMMSS.gal.json.gz
  one gzipped JSON-lines file per *minute*; in practice the records of each
  15-minute observation slot are published in the minutes right after the slot
  (observed: minute+1 always, minute+2 sometimes, minute+3 absent).
  Each record: {date, url, domain, outletName, outletLogo, outletTwitter,
               title, image, desc, lang, author}.
  * ``date``  : estimated publish time (~30% exact, else first-seen time) ->
                stored separately as ``adate``; NOT the replay clock.
  * observed_at_utc = the batch file's timestamp (the replay clock).
  * ``lang``  : CLD2 code; only ``en`` records are analyzed.

Commands (from the repository root):

  python scripts/fetch_gdelt_gal.py collect --start 2026-11-09 --end 2026-11-15
  python scripts/fetch_gdelt_gal.py normalize
  python scripts/fetch_gdelt_gal.py verify          # replay-contract checks

collect   downloads the raw batches (idempotent: finished files are skipped),
          records sha256 per file and the availability of every expected batch.
normalize turns the raw batches into the fixed replay input
          data/processed/gdelt/articles.jsonl sorted by (observed_at, canonical_url)
          plus manifest.json (window, hashes, counts, gaps, ...).
verify    re-runs normalization into a temp file and compares the SHA-256, then
          loads the input twice and checks that both replays produce the same
          article order and snapshot hashes.

Canonical URL rule (dedup identity, original ``url`` always kept):
  * scheme/host lowercased, default ports dropped, fragment dropped;
  * tracking parameters removed (any ``utm_*``/``itm_*`` and an explicit list:
    fbclid, gclid, ref, ref_src, ref_url, cmpid, cmp, smid, mc_cid, mc_eid,
    ns_campaign, ns_mchannel, _ga, _gl, usqp);
  * everything else (path, article-id query parameters) is preserved as-is.

Collection gaps: an expected batch slot whose files are absent (HTTP 404) is a
*gap*, never "zero articles"; gaps are listed in manifest.json.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "gdelt_gal"
OUT = ROOT / "data" / "processed" / "gdelt"
BASE = "https://data.gdeltproject.org/gdeltv3/gal"
SLOT_MINUTES = (0, 15, 30, 45)
CLUSTER_OFFSETS = (1, 2)          # minutes after the slot that may carry files
RETRIES = 3

TRACKING_PREFIXES = ("utm_", "itm_")
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "ref_url", "cmpid", "cmp",
                   "smid", "mc_cid", "mc_eid", "ns_campaign", "ns_mchannel",
                   "_ga", "_gl", "usqp", "sessionid"}
DEFAULT_PORTS = {"http": 80, "https": 443}

# ------------------------------------------------------------------ country mapping v1
# Publisher country = documented rule, version-recorded:
#   v1 = ccTLD rule + curated overrides (mappings/domain_countries_v1.tsv).
# ccTLD rule: the last label of the registrable domain, except generic-use
# ccTLDs (.co .io .me .tv .fm .am .st) and second-level suffixes
# (co./com./org./net./ac./gov./edu. + ccTLD -> that ccTLD).
GENERIC_CCTLDS = {"co", "io", "me", "tv", "fm", "am", "st", "eu", "edu", "gov", "mil"}
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}
CC_ALIAS = {"UK": "GB"}   # .uk is a ccTLD for the United Kingdom (ISO: GB)


def load_overrides() -> dict[str, str]:
    p = ROOT / "mappings" / "domain_countries_v1.tsv"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                dom, cc = line.split("\t")[:2]
                out[dom.strip().lower()] = cc.strip().upper()
    return out


def registrable_domain(host: str) -> str:
    """host minus a leading www. (standardized media domain)."""
    parts = host.split(".")
    if len(parts) > 2 and parts[0] == "www":
        parts = parts[1:]
    return ".".join(parts)


def country_of(domain_std: str, overrides: dict[str, str]) -> str | None:
    """ISO-3166 alpha-2 publisher country or None (-> 'unknown')."""
    if domain_std in overrides:
        return overrides[domain_std]
    labels = domain_std.split(".")
    last = labels[-1]
    cc = None
    if len(last) == 2 and last.isalpha() and last not in GENERIC_CCTLDS:
        cc = last.upper()
    elif len(labels) >= 2 and labels[-2] in SECOND_LEVEL and len(last) == 2 and last.isalpha() \
            and last not in GENERIC_CCTLDS:
        cc = last.upper()
    return CC_ALIAS.get(cc, cc) if cc else None


# ------------------------------------------------------------------ canonical URL
def canonical_url(url: str) -> str | None:
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    host = u.hostname.lower()
    port = f":{u.port}" if u.port and u.port != DEFAULT_PORTS.get(u.scheme) else ""
    keep = []
    for q in (u.query or "").split("&"):
        if not q:
            continue
        name = urllib.parse.unquote_plus(q.split("=", 1)[0]).lower()
        if name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES):
            continue
        keep.append(q)
    query = "&".join(keep)
    return urllib.parse.urlunsplit((u.scheme.lower(), host + port, u.path or "/", query, ""))


# ------------------------------------------------------------------ collect
def slot_stamps(day: str) -> list[str]:
    """File timestamps (YYYYMMDDHHMMSS) probed for one UTC day."""
    d = day.replace("-", "")
    stamps = []
    for hh in range(24):
        for mm in SLOT_MINUTES:
            for off in CLUSTER_OFFSETS:
                stamps.append(f"{d}{hh:02d}{mm + off:02d}00")
    return stamps


def _download(stamp: str) -> dict:
    dest = RAW / f"{stamp}.gal.json.gz"
    url = f"{BASE}/{stamp}.gal.json.gz"
    if dest.exists() and dest.stat().st_size > 0:
        return _describe(stamp, "ok", dest)
    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "trendpulse-submission/1.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return _describe(stamp, "ok", dest)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"ts": stamp, "status": "absent", "http": 404}
            last_err = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = str(exc)[:120]
        time.sleep(1.5 * attempt)
    return {"ts": stamp, "status": "failed", "error": last_err}


def _describe(stamp: str, status: str, dest: Path) -> dict:
    h = hashlib.sha256()
    n_rec = 0
    with dest.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    with gzip.open(dest, "rt", encoding="utf-8", errors="replace") as fh:
        n_rec = sum(1 for _ in fh)
    return {"ts": stamp, "status": status, "bytes": dest.stat().st_size,
            "sha256": h.hexdigest(), "records": n_rec}


def cmd_collect(start: str, end: str, workers: int = 8) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    days = []
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(start)
    while d.isoformat() <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    stamps = [s for day in days for s in slot_stamps(day)]
    print(f"expected probe set: {len(stamps)} files for {days[0]}..{days[-1]} "
          f"({len(days)} days x {len(SLOT_MINUTES)} slots x {len(CLUSTER_OFFSETS)} cluster minutes)")
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(_download, stamps))
    ok = [r for r in results if r["status"] == "ok"]
    absent = [r for r in results if r["status"] == "absent"]
    failed = [r for r in results if r["status"] == "failed"]
    # a 15-min slot is a *gap* when neither cluster minute exists
    slot_map: dict[str, int] = {}
    for r in results:
        m = int(r["ts"][10:12])
        slot = r["ts"][:10] + f"{m - m % 15:02d}"
        slot_map[slot] = slot_map.get(slot, 0) + (r["status"] == "ok")
    gaps = [{"slot": s, "files": 0} for s, n in sorted(slot_map.items()) if n == 0]
    manifest = {
        "window_days": days,
        "expected_files": len(stamps),
        "ok": len(ok), "absent_404": len(absent), "failed": len(failed),
        "bytes_total": sum(r["bytes"] for r in ok),
        "records_total": sum(r["records"] for r in ok),
        "empty_slots": len(gaps),
        "elapsed_sec": round(time.time() - t0, 1),
        "files": results,
    }
    (RAW / "_manifest_raw.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(f"ok={len(ok)} absent(404)={len(absent)} failed={len(failed)} "
          f"bytes={manifest['bytes_total']:,} records={manifest['records_total']:,} "
          f"empty_slots={len(gaps)} in {manifest['elapsed_sec']}s")
    if failed:
        print("failed:", [f["ts"] for f in failed][:10])
        sys.exit(2)


# ------------------------------------------------------------------ normalize
def cmd_normalize() -> dict:
    from datetime import timedelta
    overrides = load_overrides()
    raw_manifest = json.loads((RAW / "_manifest_raw.json").read_text(encoding="utf-8"))
    files = sorted((f for f in raw_manifest["files"] if f["status"] == "ok"), key=lambda f: f["ts"])
    stats = {"raw_records": 0, "kept": 0, "dup_removed": 0,
             "excluded": {"bad_json": 0, "no_url": 0, "no_title": 0, "not_english": 0}}
    seen: dict[str, dict] = {}
    unmapped: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for f in files:
        obs = (f"{f['ts'][0:4]}-{f['ts'][4:6]}-{f['ts'][6:8]}T{f['ts'][8:10]}:"
               f"{f['ts'][10:12]}:{f['ts'][12:14]}Z")
        with gzip.open(RAW / f"{f['ts']}.gal.json.gz", "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stats["raw_records"] += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    stats["excluded"]["bad_json"] += 1
                    continue
                url = (rec.get("url") or "").strip()
                title = (rec.get("title") or "").strip()
                if not url or not url.lower().startswith(("http://", "https://")):
                    stats["excluded"]["no_url"] += 1
                    continue
                if not title:
                    stats["excluded"]["no_title"] += 1
                    continue
                if (rec.get("lang") or "").strip().lower() != "en":
                    stats["excluded"]["not_english"] += 1
                    continue
                curl = canonical_url(url)
                if curl is None:
                    stats["excluded"]["no_url"] += 1
                    continue
                dom_raw = (rec.get("domain") or "").strip().lower()
                host = urllib.parse.urlsplit(curl).hostname or ""
                dom_std = registrable_domain(host) or dom_raw
                cc = country_of(dom_std, overrides)
                if cc is None:
                    unmapped[dom_std] = unmapped.get(dom_std, 0) + 1
                    cc = "unknown"
                by_country[cc] = by_country.get(cc, 0) + 1
                prev = seen.get(curl)
                if prev is not None:
                    stats["dup_removed"] += 1
                    # keep the earliest observation; on equal obs keep the first seen
                    continue
                seen[curl] = {
                    "obs": obs, "url": url, "curl": curl,
                    "domain": dom_raw, "domain_std": dom_std,
                    "outlet": (rec.get("outletName") or "").strip() or dom_std,
                    "country": cc, "title": title,
                    "desc": (rec.get("desc") or "").strip()[:300],
                    "adate": (rec.get("date") or "").strip() or None,
                }
    OUT.mkdir(parents=True, exist_ok=True)
    articles = sorted(seen.values(), key=lambda a: (a["obs"], a["curl"]))
    for i, a in enumerate(articles):
        a["i"] = i
    path = OUT / "articles.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for a in articles:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    by_day: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for a in articles:
        by_day[a["obs"][:10]] = by_day.get(a["obs"][:10], 0) + 1
        by_domain[a["domain_std"]] = by_domain.get(a["domain_std"], 0) + 1
    # observation gaps: consecutive empty 15-min slots -> intervals
    ok_slots = set()
    for f in files:
        m = int(f["ts"][10:12])
        ok_slots.add(f["ts"][:10] + f"{m - m % 15:02d}")
    all_slots = []
    for day in raw_manifest["window_days"]:
        d = day.replace("-", "")
        for hh in range(24):
            for mm in SLOT_MINUTES:
                all_slots.append(f"{d}{hh:02d}{mm:02d}")
    empty = [s for s in all_slots if s not in ok_slots]
    gap_intervals = []
    prev_slot_dt = None
    for s in empty:                       # run-length encode consecutive slots
        slot_dt = _dt_from_stamp(s)
        end = slot_dt + timedelta(minutes=15)
        if gap_intervals and prev_slot_dt is not None and (slot_dt - prev_slot_dt).total_seconds() == 900:
            gap_intervals[-1]["end"] = end.isoformat() + "Z"
            gap_intervals[-1]["slots"] += 1
        else:
            gap_intervals.append({"start": slot_dt.isoformat() + "Z",
                                  "end": end.isoformat() + "Z", "slots": 1})
        prev_slot_dt = slot_dt
    stats["kept"] = len(articles)
    manifest = {
        "source": "GDELT Global Article List (GAL), https://data.gdeltproject.org/gdeltv3/gal/",
        "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window": {"start_utc": raw_manifest["window_days"][0] + "T00:00:00Z",
                   "end_utc": _day_after(raw_manifest["window_days"][-1]) + "T00:00:00Z",
                   "days": raw_manifest["window_days"]},
        "raw_files": [{"ts": f["ts"], "sha256": f["sha256"], "bytes": f["bytes"],
                       "records": f["records"]} for f in files],
        "raw_absent_404": raw_manifest["absent_404"],
        "raw_failed": raw_manifest["failed"],
        "input_sha256": h.hexdigest(),
        "counts": stats,
        "by_day": by_day,
        "top_domains": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])[:50]),
        "by_country": dict(sorted(by_country.items(), key=lambda kv: -kv[1])),
        "mapping": {"version": "v1", "rule": "ccTLD + mappings/domain_countries_v1.tsv overrides",
                    "overrides": len(overrides)},
        "unmapped_domains": {"count": len(unmapped),
                             "top": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])[:30])},
        "collection_gaps": gap_intervals,
        "sort_key": ["observed_at_utc", "canonical_url"],
        "dedup": "first earliest observation per canonical_url kept",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("input_sha256", "counts", "collection_gaps")},
                     ensure_ascii=False, indent=1))
    print("countries:", manifest["by_country"])
    return manifest


def _dt_from_stamp(stamp: str):
    from datetime import datetime, timezone
    return datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def _day_after(day: str) -> str:
    from datetime import date as _date, timedelta
    return (_date.fromisoformat(day) + timedelta(days=1)).isoformat()


# ------------------------------------------------------------------ verify (replay contract)
def cmd_verify() -> None:
    """(1) normalization is deterministic; (2) two replays give identical order/hashes."""
    global OUT
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        real = OUT / "articles.jsonl"
        tmp = Path(td) / "articles.jsonl"
        old_out = OUT
        try:
            OUT = Path(td)
            cmd_normalize()
        finally:
            OUT = old_out
        h1 = hashlib.sha256(real.read_bytes()).hexdigest()
        h2 = hashlib.sha256(tmp.read_bytes()).hexdigest()
        print("determinism:", "OK" if h1 == h2 else f"MISMATCH {h1} vs {h2}")
    sys.path.insert(0, str(ROOT))
    from trendpulse.gal import load_articles
    from trendpulse.gal_engine import GalEngine
    arts = load_articles()
    hashes = []
    for _ in range(2):
        eng = GalEngine(arts)
        eng.replay_all()
        hashes.append(eng.snapshot_digest())
    print("replay hash:", hashes[0])
    print("replay determinism:", "OK" if hashes[0] == hashes[1] else "MISMATCH")
    if h1 != h2 or hashes[0] != hashes[1]:
        sys.exit(3)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--start", required=True, help="first UTC day YYYY-MM-DD")
    c.add_argument("--end", required=True, help="last UTC day YYYY-MM-DD (inclusive)")
    c.add_argument("--workers", type=int, default=8)
    sub.add_parser("normalize")
    sub.add_parser("verify")
    args = ap.parse_args()
    if args.cmd == "collect":
        cmd_collect(args.start, args.end, args.workers)
    elif args.cmd == "normalize":
        cmd_normalize()
    else:
        cmd_verify()


if __name__ == "__main__":
    main()
