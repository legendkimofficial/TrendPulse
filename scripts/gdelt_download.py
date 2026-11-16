"""Download GDELT 2.1 GKG (English stream) and keep only the fields Trend Pulse needs.

Pure Python 3.9+ standard library. For each 15-minute GKG file in the date range:
download the zip into memory -> parse the tab-separated rows -> keep a slim record
per web article -> write one small gzip file -> drop the raw zip (nothing big is kept).

Usage
-----
  # 1) measure one day first (prints sizes and a 45-day estimate)
  python gdelt_download.py --start 2024-10-01 --end 2024-10-01 --measure

  # 2) full range (resumable: re-run the same command after an interruption)
  python gdelt_download.py --start 2024-10-01 --end 2024-11-14

  # look at the URLs without downloading
  python gdelt_download.py --start 2024-10-01 --end 2024-10-01 --dry-run

Output (default --out data/gdelt)
------
  slim/YYYY-MM-DD/HHMM.jsonl.gz   one line per article (see SLIM RECORD below)
  slim/YYYY-MM-DD/HHMM.missing    the 15-min file does not exist on the server (404)
  download_log.csv                one row per 15-min file: status, sizes, row counts, seconds

SLIM RECORD (one JSON object per line)
  id       GKGRECORDID
  ts       publication timestamp, YYYYMMDDHHMMSS (UTC)
  domain   V2SOURCECOMMONNAME (news outlet domain, e.g. bbc.co.uk)
  url      V2DOCUMENTIDENTIFIER
  title    <PAGE_TITLE> from V2EXTRASXML, HTML entities decoded ("" if absent)
  persons  V1PERSONS   (list, duplicates removed, order kept)
  orgs     V1ORGANIZATIONS
  places   V1LOCATIONS as [name, type, country] lists, duplicates removed
           type: 1 country, 2 US state, 3 US city, 4 world city, 5 world state
           country: FIPS 10-4 country code (e.g. US, SP, IS)
  themes   V1THEMES (kept as context, not meant for ranking)
  tone     first value of V1.5TONE (average tone), or null

Only web articles are kept (V2SOURCECOLLECTIONIDENTIFIER == 1).
GKG 2.1 column layout (27 columns) follows the GDELT GKG Codebook V2.1.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

BASE_URL = "http://data.gdeltproject.org/gdeltv2/"
N_COLS = 27
COL = {  # 0-based column index, GKG 2.1
    "id": 0, "date": 1, "collection": 2, "domain": 3, "url": 4,
    "themes": 7, "locations": 9, "persons": 11, "orgs": 13, "tone": 15, "extras": 26,
}
TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
LOG_FIELDS = ["file", "status", "zip_bytes", "rows", "kept", "malformed", "not_web",
              "with_title", "slim_bytes", "seconds", "error"]


def slots(start: date, end: date):
    """Every 15-minute file timestamp from start 00:00 to end 23:45 (UTC)."""
    t = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, 23, 45, tzinfo=timezone.utc)
    while t <= stop:
        yield t
        t += timedelta(minutes=15)


def split_list(field: str) -> list[str]:
    """'a;b;;a' -> ['a', 'b'] (empty parts and duplicates removed, order kept)."""
    seen, out = set(), []
    for x in field.split(";"):
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def split_places(field: str) -> list[list]:
    """V1LOCATIONS 'type#name#country#adm1#lat#long#featureid;...' -> [[name, type, country], ...]."""
    seen, out = set(), []
    for block in field.split(";"):
        parts = block.split("#")
        if len(parts) < 3 or not parts[1].strip():
            continue
        name = parts[1].strip()
        if name in seen:
            continue
        seen.add(name)
        try:
            kind = int(parts[0])
        except ValueError:
            kind = 0
        out.append([name, kind, parts[2].strip()])
    return out


def parse_row(cols: list[str]) -> dict:
    extras = cols[COL["extras"]]
    m = TITLE_RE.search(extras)
    tone_field = cols[COL["tone"]]
    try:
        tone = float(tone_field.split(",")[0]) if tone_field else None
    except ValueError:
        tone = None
    return {
        "id": cols[COL["id"]],
        "ts": cols[COL["date"]],
        "domain": cols[COL["domain"]].strip().lower(),
        "url": cols[COL["url"]].strip(),
        "title": html.unescape(m.group(1)).strip() if m else "",
        "persons": split_list(cols[COL["persons"]]),
        "orgs": split_list(cols[COL["orgs"]]),
        "places": split_places(cols[COL["locations"]]),
        "themes": split_list(cols[COL["themes"]]),
        "tone": tone,
    }


def fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    """Return the body, or None on HTTP 404. Retries other errors with backoff."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TrendPulse-course-project"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {retries} tries: {err}")


def process_zip(raw: bytes) -> tuple[list[dict], dict]:
    stats = {"rows": 0, "kept": 0, "malformed": 0, "not_web": 0, "with_title": 0}
    records = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                line = line.rstrip("\r\n")
                if not line:
                    continue
                stats["rows"] += 1
                cols = line.split("\t")
                if len(cols) != N_COLS:
                    stats["malformed"] += 1
                    continue
                if cols[COL["collection"]].strip() != "1":
                    stats["not_web"] += 1
                    continue
                rec = parse_row(cols)
                stats["kept"] += 1
                stats["with_title"] += bool(rec["title"])
                records.append(rec)
    return records, stats


def write_atomic_gz(path: str, records: list[dict]) -> int:
    tmp = path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        for r in records:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return os.path.getsize(path)


def mb(n: float) -> str:
    return f"{n / 1e6:,.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="first day, YYYY-MM-DD (UTC)")
    ap.add_argument("--end", required=True, help="last day, YYYY-MM-DD (UTC), inclusive")
    ap.add_argument("--out", default=os.path.join("data", "gdelt"))
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--measure", action="store_true", help="print a size summary and a 45-day estimate")
    ap.add_argument("--dry-run", action="store_true", help="list URLs only")
    ap.add_argument("--max-files", type=int, default=0, help="stop after this many files (0 = no limit)")
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        sys.exit("--end must not be before --start")
    base = args.base_url.rstrip("/") + "/"
    slim_root = os.path.join(args.out, "slim")
    log_path = os.path.join(args.out, "download_log.csv")
    os.makedirs(slim_root, exist_ok=True)

    new_log = not os.path.exists(log_path)
    logf = open(log_path, "a", newline="", encoding="utf-8")
    log = csv.DictWriter(logf, fieldnames=LOG_FIELDS)
    if new_log:
        log.writeheader()

    tot = {"files": 0, "ok": 0, "missing": 0, "skipped": 0, "error": 0, "zip_bytes": 0,
           "slim_bytes": 0, "rows": 0, "kept": 0, "malformed": 0, "not_web": 0, "with_title": 0}

    for t in slots(start, end):
        stamp = t.strftime("%Y%m%d%H%M%S")
        url = f"{base}{stamp}.gkg.csv.zip"
        if args.dry_run:
            print(url)
            continue
        if args.max_files and tot["files"] >= args.max_files:
            break
        tot["files"] += 1
        day_dir = os.path.join(slim_root, t.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        out_path = os.path.join(day_dir, t.strftime("%H%M") + ".jsonl.gz")
        miss_path = os.path.join(day_dir, t.strftime("%H%M") + ".missing")
        if os.path.exists(out_path) or os.path.exists(miss_path):
            tot["skipped"] += 1          # done in an earlier run (resume)
            continue

        row = {k: "" for k in LOG_FIELDS}
        row["file"] = stamp
        t1 = time.time()
        try:
            raw = fetch(url)
            if raw is None:
                open(miss_path, "w").close()
                row["status"] = "missing"
                tot["missing"] += 1
            else:
                records, st = process_zip(raw)
                size = write_atomic_gz(out_path, records)
                row.update(st, status="ok", zip_bytes=len(raw), slim_bytes=size)
                tot["ok"] += 1
                tot["zip_bytes"] += len(raw)
                tot["slim_bytes"] += size
                for k in ("rows", "kept", "malformed", "not_web", "with_title"):
                    tot[k] += st[k]
        except Exception as e:  # keep going; the file is retried on the next run
            row.update(status="error", error=str(e)[:200])
            tot["error"] += 1
        row["seconds"] = round(time.time() - t1, 2)
        log.writerow(row)
        logf.flush()
        done = tot["ok"] + tot["missing"] + tot["error"]
        print(f"\r{stamp} {row['status']:<8} done {done} | kept {tot['kept']:,} articles"
              f" | zip {mb(tot['zip_bytes'])} -> slim {mb(tot['slim_bytes'])}", end="", flush=True)

    logf.close()
    if args.dry_run:
        return 0
    print()
    report = summarize(args.out, start, end, args.measure)
    print(report)
    with open(os.path.join(args.out, "measure_report.txt" if args.measure else "last_run_report.txt"),
              "w", encoding="utf-8") as f:
        f.write(report + "\n")
    return 1 if tot["error"] else 0


def summarize(out_dir: str, start: date, end: date, measure: bool) -> str:
    """Totals for the whole range from the log and the saved files, so a resumed run
    reports the same numbers as an uninterrupted one."""
    wanted = {t.strftime("%Y%m%d%H%M%S") for t in slots(start, end)}
    last: dict[str, dict] = {}
    with open(os.path.join(out_dir, "download_log.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["file"] in wanted:
                last[r["file"]] = r          # the latest attempt wins
    status = {"ok": 0, "missing": 0, "error": 0}
    num = {k: 0.0 for k in ("zip_bytes", "slim_bytes", "rows", "kept", "malformed", "not_web",
                            "with_title", "seconds")}
    for r in last.values():
        status[r["status"]] = status.get(r["status"], 0) + 1
        if r["status"] == "ok":
            for k in num:
                num[k] += float(r[k] or 0)
    pending = len(wanted) - status["ok"] - status["missing"]
    kept = max(num["kept"], 1)
    n_p = n_o = n_t = n_l = 0
    distinct = {"persons": set(), "orgs": set(), "places": set(), "themes": set(), "domains": set()}
    place_types = {}
    sample = None
    for day in sorted({w[:8] for w in wanted}):
        d = os.path.join(out_dir, "slim", f"{day[:4]}-{day[4:6]}-{day[6:]}")
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl.gz"):
                continue
            with gzip.open(os.path.join(d, name), "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    places = r.get("places", [])
                    n_p += len(r["persons"]); n_o += len(r["orgs"]); n_t += len(r["themes"]); n_l += len(places)
                    if measure:
                        distinct["persons"].update(r["persons"]); distinct["orgs"].update(r["orgs"])
                        for name, kind, _ in places:
                            distinct["places"].add(name)
                            place_types[kind] = place_types.get(kind, 0) + 1
                        distinct["themes"].update(r["themes"]); distinct["domains"].add(r["domain"])
                    if sample is None and r["title"]:
                        sample = r
    lines = [
        f"range              : {start} .. {end} (UTC), {len(wanted)} files expected",
        f"15-min files       : ok {status['ok']}, missing on server {status['missing']}, "
        f"error {status['error']}, not done yet {pending}",
        f"downloaded (zip)   : {mb(num['zip_bytes'])}   saved (slim gz): {mb(num['slim_bytes'])}",
        f"rows               : {int(num['rows']):,} (malformed {int(num['malformed']):,}, "
        f"not web {int(num['not_web']):,})",
        f"articles kept      : {int(num['kept']):,}   with title: {num['with_title'] / kept:.1%}",
        f"per article        : persons {n_p / kept:.2f}, orgs {n_o / kept:.2f}, places {n_l / kept:.2f}, "
        f"themes {n_t / kept:.2f}",
        f"time spent         : {num['seconds'] / 60:.1f} min (download + processing)",
    ]
    if measure:
        lines.insert(5, "distinct           : " + ", ".join(f"{k} {len(v):,}" for k, v in distinct.items()))
        names = {1: "country", 2: "US state", 3: "US city", 4: "world city", 5: "world state"}
        tot_l = max(sum(place_types.values()), 1)
        lines.insert(7, "place mentions     : " + ", ".join(
            f"{names.get(k, 'other')} {v / tot_l:.0%}" for k, v in sorted(place_types.items())))
        done = status["ok"] + status["missing"]
        if done:
            scale = 45 * 96 / done        # 96 files per day
            lines += [
                "--- estimate for 45 days (30-day warm-up + 15-day evaluation) ---",
                f"download           : {num['zip_bytes'] * scale / 1e9:,.1f} GB (zips are not kept)",
                f"disk (slim)        : {num['slim_bytes'] * scale / 1e9:,.2f} GB",
                f"time               : {num['seconds'] * scale / 3600:,.1f} h at this speed",
                f"articles           : {num['kept'] * scale:,.0f}",
            ]
        if pending:
            lines.append(f"WARNING: {pending} files not done yet; re-run the same command to finish.")
        if sample:
            lines += ["--- one record (check that the fields look right) ---",
                      json.dumps({**sample, "themes": sample["themes"][:8], "places": sample.get("places", [])[:8]},
                                 ensure_ascii=False, indent=1)]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
