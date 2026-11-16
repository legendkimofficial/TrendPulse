"""GDELT GKG 2.1 slim stream reader (gdelt-gkg branch).

Reads ``data/gdelt/slim/YYYY-MM-DD/HHMM.jsonl.gz`` (made by scripts/gdelt_download.py)
day folder by day folder, file by file, line by line, and yields one ``GkgArticle`` at a
time. Nothing is collected into a list: the only state that grows with the stream is a
compact 64-bit hash table of the URLs already seen (8 bytes per slot).

Keys used for ranking
---------------------
  person          "P:<name>"
  organization    "O:<name>"
  place           "L:<name>"   only place types 2,3,4,5 (US state, US city, world city,
                               world region). Type 1 (country) goes to ``countries``,
                               and also to keys only when ``include_countries=True``.
  <name> = whitespace collapsed + lower case. Duplicates inside one article are removed.

Outlet self-name removal
------------------------
  The outlet name is the registered-domain label of ``domain`` (energycentral.com ->
  "energycentral", bbc.co.uk -> "bbc", news.yahoo.com -> "yahoo"). An organization is
  dropped when its first one or more words, with every non-alphanumeric character removed
  and joined, equal the outlet name, i.e. the match must end at a word boundary:
  "energy central" and "energy central power industry network" on energycentral.com are
  dropped; "national treasury" on nation.africa and "abcam" on abc.net.au are kept.
  Known limitation: outlets named after a place still drop that place's organizations
  ("syracuse university" on syracuse.com).

Country-name organizations
--------------------------
  GKG often tags a country as an organization too ("united states"). An organization whose
  normalized name equals a country name in gkg_country_names.txt (a fixed list of the 250
  type-1 names seen in the data) is dropped (``drop_country_orgs=True``). This check runs
  before the self-name check.

Duplicate URLs
--------------
  Only the first article with a given URL is kept. URLs are stored as the first 8 bytes
  of their SHA-1 (collision chance for 10 M URLs is about 3e-6).
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from array import array
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(REPO_ROOT, "data", "gdelt")
DEFAULT_MANIFEST = os.path.join(REPO_ROOT, "experiments", "results", "gkg", "input_manifest.json")

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^(\d{4})\.jsonl\.gz$")
MISSING_RE = re.compile(r"^(\d{4})\.missing$")
NON_ALNUM = re.compile(r"[^0-9a-z]+")
SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "ac", "edu", "or", "ne", "go",
                          "gob", "nic", "mil", "info", "biz"})
SLOTS_PER_DAY = 96
KEY_KINDS = {"P": "persons", "O": "orgs", "L": "places"}
COUNTRY_NAMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gkg_country_names.txt")


def _load_country_names(path: str = COUNTRY_NAMES_PATH) -> frozenset[str]:
    with open(path, encoding="utf-8") as f:
        return frozenset(line.strip() for line in f if line.strip() and not line.startswith("#"))


# Fixed list (not grown from the stream) so the rule does not depend on what was read.
COUNTRY_NAMES = _load_country_names()


# --------------------------------------------------------------------------- data types
@dataclass(slots=True)
class GkgArticle:
    idx: int                    # 0-based position in the (deduplicated) stream
    ts: int                     # UTC epoch seconds
    domain: str
    url: str
    title: str
    keys: tuple[str, ...]       # ranking keys, e.g. ("P:joe biden", "O:nato", "L:kyiv, kyyiv, ukraine")
    countries: tuple[str, ...]  # normalized names of type-1 places, e.g. ("ukraine",)
    tone: float | None


@dataclass(frozen=True)
class GkgConfig:
    include_countries: bool = False
    place_types: tuple[int, ...] = (2, 3, 4, 5)
    drop_self_orgs: bool = True
    drop_country_orgs: bool = True
    dedup_urls: bool = True


@dataclass
class StreamStats:
    """Counters filled while ``iter_articles`` runs (pass one in to read them afterwards)."""
    files_read: int = 0
    lines: int = 0
    bad_lines: int = 0
    articles: int = 0            # yielded
    duplicate_urls: int = 0
    empty_urls: int = 0          # kept, never treated as duplicates
    self_orgs_dropped: int = 0
    country_orgs_dropped: int = 0  # organization names equal to a country name
    ts_out_of_order: int = 0     # article ts smaller than the previous one
    per_day: dict = field(default_factory=dict)        # day -> articles yielded
    files_per_day: dict = field(default_factory=dict)  # day -> [HHMM, ...] read
    raw_sha256: object = None    # hashlib object; when set, every file's name+bytes go into it
    url_table_bytes: int = 0     # size of the URL hash table at the end


class U64Set:
    """Open-addressing set of 64-bit ints in one ``array('Q')`` (8 bytes per slot).

    Linear probing, grows at 3/4 load. 0 marks an empty slot, so 0 is stored as 1
    (a negligible extra collision)."""

    __slots__ = ("_t", "_mask", "_n")

    def __init__(self, capacity: int = 1 << 12) -> None:
        cap = 1
        while cap < capacity:
            cap <<= 1
        self._t = array("Q", bytes(8 * cap))
        self._mask = cap - 1
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def add(self, h: int) -> bool:
        """Insert; return True if ``h`` was not present."""
        h = h or 1
        t, m = self._t, self._mask
        i = h & m
        while True:
            v = t[i]
            if v == 0:
                t[i] = h
                self._n += 1
                if self._n * 4 > len(t) * 3:
                    self._grow()
                return True
            if v == h:
                return False
            i = (i + 1) & m

    def __contains__(self, h: int) -> bool:
        h = h or 1
        t, m = self._t, self._mask
        i = h & m
        while True:
            v = t[i]
            if v == 0:
                return False
            if v == h:
                return True
            i = (i + 1) & m

    def _grow(self) -> None:
        old = self._t
        cap = len(old) * 2
        t = array("Q", bytes(8 * cap))
        m = cap - 1
        for h in old:
            if h:
                i = h & m
                while t[i]:
                    i = (i + 1) & m
                t[i] = h
        self._t, self._mask = t, m

    def nbytes(self) -> int:
        return self._t.itemsize * len(self._t)


def h64(text: str) -> int:
    """First 8 bytes of SHA-1, as an unsigned int."""
    return int.from_bytes(hashlib.sha1(text.encode("utf-8")).digest()[:8], "big")


# --------------------------------------------------------------------------- key rules
def norm_name(name: str) -> str:
    return " ".join(name.split()).lower()


_outlet_cache: dict[str, str] = {}


def outlet_name(domain: str) -> str:
    """energycentral.com -> 'energycentral', www.bbc.co.uk -> 'bbc', news.yahoo.com -> 'yahoo'."""
    got = _outlet_cache.get(domain)
    if got is not None:
        return got
    d = domain.strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    labels = [x for x in d.split(".") if x]
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        name = labels[-3]
    elif len(labels) >= 2:
        name = labels[-2]
    else:
        name = labels[0] if labels else ""
    name = NON_ALNUM.sub("", name)
    _outlet_cache[domain] = name
    return name


def is_self_org(org_norm: str, outlet: str) -> bool:
    """True if the first word(s) of the (normalized) organization name spell the outlet name.

    Words are compared with non-alphanumerics removed, so the match always ends at a word
    boundary: ("energy central power", "energycentral") -> True, ("national treasury",
    "nation") -> False."""
    if not outlet:
        return False
    acc = ""
    for word in org_norm.split():
        acc += NON_ALNUM.sub("", word)
        if acc == outlet:
            return True
        if len(acc) >= len(outlet) or not outlet.startswith(acc):
            return False
    return False


def make_keys(rec: dict, cfg: GkgConfig) -> tuple[tuple[str, ...], tuple[str, ...], int, int]:
    """Return (keys, countries, self-name orgs dropped, country-name orgs dropped).

    Organization rules, in this order: a name equal to a country name (COUNTRY_NAMES) is
    dropped and counted as a country-name drop; otherwise a name matching the outlet is
    dropped and counted as a self-name drop. Each distinct name counts once per article."""
    keys: list[str] = []
    seen: set[str] = set()

    def add(k: str) -> None:
        if k not in seen:
            seen.add(k)
            keys.append(k)

    for p in rec.get("persons") or ():
        n = norm_name(p)
        if n:
            add("P:" + n)
    dropped = country_dropped = 0
    outlet = outlet_name(rec.get("domain") or "") if cfg.drop_self_orgs else ""
    dropped_names: set[str] = set()
    for o in rec.get("orgs") or ():
        n = norm_name(o)
        if not n:
            continue
        if cfg.drop_country_orgs and n in COUNTRY_NAMES:
            if n not in dropped_names:
                dropped_names.add(n)
                country_dropped += 1
            continue
        if outlet and is_self_org(n, outlet):
            if n not in dropped_names:
                dropped_names.add(n)
                dropped += 1
            continue
        add("O:" + n)
    countries: list[str] = []
    cseen: set[str] = set()
    for place in rec.get("places") or ():
        try:
            name, kind = place[0], int(place[1])
        except (IndexError, TypeError, ValueError):
            continue
        n = norm_name(name or "")
        if not n:
            continue
        if kind == 1:
            if n not in cseen:
                cseen.add(n)
                countries.append(n)
            if cfg.include_countries:
                add("L:" + n)
        elif kind in cfg.place_types:
            add("L:" + n)
    return tuple(keys), tuple(countries), dropped, country_dropped


_ts_cache: dict[str, int] = {}


def ts_to_epoch(ts: str) -> int:
    """'YYYYMMDDHHMMSS' (UTC) -> epoch seconds. Cached: a 15-min file shares one ts."""
    got = _ts_cache.get(ts)
    if got is None:
        if len(_ts_cache) > 4096:
            _ts_cache.clear()
        got = calendar.timegm((int(ts[0:4]), int(ts[4:6]), int(ts[6:8]),
                               int(ts[8:10]), int(ts[10:12]), int(ts[12:14]), 0, 0, 0))
        _ts_cache[ts] = got
    return got


# --------------------------------------------------------------------------- stream
def _day_str(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    s = str(d)
    date.fromisoformat(s)  # validates
    return s


def list_days(root: str = DEFAULT_ROOT, start=None, end=None) -> list[str]:
    slim = os.path.join(root, "slim")
    if not os.path.isdir(slim):
        return []
    s, e = _day_str(start), _day_str(end)
    return [d for d in sorted(os.listdir(slim))
            if DAY_RE.match(d) and os.path.isdir(os.path.join(slim, d))
            and (s is None or d >= s) and (e is None or d <= e)]


def iter_articles(start=None, end=None, *, root: str = DEFAULT_ROOT,
                  config: GkgConfig | None = None,
                  stats: StreamStats | None = None) -> Iterator[GkgArticle]:
    """Yield articles in time order: day folders by name, HHMM files by name, lines in order.

    ``start``/``end`` are inclusive days ('YYYY-MM-DD' or date); None means open-ended.
    ``.missing`` files and anything that is not ``HHMM.jsonl.gz`` are skipped here
    (``build_manifest`` reports them).
    """
    cfg = config or GkgConfig()
    st = stats if stats is not None else StreamStats()
    seen_urls = U64Set(1 << 16) if cfg.dedup_urls else None
    idx = 0
    last_ts = -1
    for day in list_days(root, start, end):
        ddir = os.path.join(root, "slim", day)
        names = sorted(n for n in os.listdir(ddir) if FILE_RE.match(n))
        st.per_day.setdefault(day, 0)
        st.files_per_day.setdefault(day, [])
        for name in names:
            path = os.path.join(ddir, name)
            with open(path, "rb") as fh:
                raw = fh.read()
            if st.raw_sha256 is not None:
                st.raw_sha256.update(f"{day}/{name}\n{len(raw)}\n".encode())
                st.raw_sha256.update(raw)
            st.files_read += 1
            st.files_per_day[day].append(name[:4])
            text = gzip.decompress(raw).decode("utf-8")
            del raw
            for line in text.splitlines():
                if not line.strip():
                    continue
                st.lines += 1
                try:
                    rec = json.loads(line)
                    ts = ts_to_epoch(rec["ts"])
                except (ValueError, KeyError, TypeError):
                    st.bad_lines += 1
                    continue
                url = (rec.get("url") or "").strip()
                if seen_urls is not None:
                    if not url:
                        st.empty_urls += 1
                    elif not seen_urls.add(h64(url)):
                        st.duplicate_urls += 1
                        continue
                keys, countries, dropped, cdropped = make_keys(rec, cfg)
                st.self_orgs_dropped += dropped
                st.country_orgs_dropped += cdropped
                if ts < last_ts:
                    st.ts_out_of_order += 1
                last_ts = ts
                tone = rec.get("tone")
                art = GkgArticle(idx=idx, ts=ts, domain=rec.get("domain") or "", url=url,
                                 title=rec.get("title") or "", keys=keys, countries=countries,
                                 tone=float(tone) if tone is not None else None)
                idx += 1
                st.articles += 1
                st.per_day[day] += 1
                if seen_urls is not None:
                    st.url_table_bytes = seen_urls.nbytes()
                yield art
            del text


def canonical_line(a: GkgArticle) -> bytes:
    """The bytes that go into the stream SHA-256 (one line per article)."""
    return (json.dumps([a.idx, a.ts, a.domain, a.url, a.title, list(a.keys), list(a.countries), a.tone],
                       ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- manifest
def _read_log(root: str) -> dict[str, str]:
    """download_log.csv -> {YYYYMMDDHHMMSS: latest status}."""
    import csv
    path = os.path.join(root, "download_log.csv")
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("file"):
                out[r["file"]] = (r.get("status") or "").strip()
    return out


def _all_slots() -> list[str]:
    return [f"{h:02d}{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def _git_state() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"commit": run("rev-parse", "HEAD") or None,
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def peak_rss_bytes() -> int | None:
    """Peak resident set size of this process (Windows: PeakWorkingSetSize).

    trendpulse.memory.process_rss returns None on 64-bit Windows / Python 3.14 here
    (no argtypes, so the process handle is passed as a 32-bit int); that shared module
    is not modified, so this step measures the peak itself."""
    import ctypes
    if sys.platform == "win32":
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize",
                                               "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                               "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                               "PagefileUsage", "PeakPagefileUsage")]
        try:
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            fn = k32.K32GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            fn.restype = wintypes.BOOL
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            return int(pmc.PeakWorkingSetSize) if fn(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb) else None
        except (OSError, AttributeError):
            return None
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru if sys.platform == "darwin" else ru * 1024
    except (ImportError, OSError):
        return None


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _rel(path: str) -> str:
    p = os.path.abspath(path)
    try:
        r = os.path.relpath(p, REPO_ROOT)
    except ValueError:
        return p.replace("\\", "/")
    return p.replace("\\", "/") if r.startswith("..") else r.replace("\\", "/")


def build_manifest(start, end, *, root: str = DEFAULT_ROOT, config: GkgConfig | None = None,
                   out_path: str | None = DEFAULT_MANIFEST, command: str | None = None) -> dict:
    """Read the stream once and write the input manifest JSON (returned as a dict).

    ``out_path=None`` skips writing."""
    cfg = config or GkgConfig()
    s, e = _day_str(start), _day_str(end)
    if s is None or e is None:
        days_avail = list_days(root)
        if not days_avail:
            raise FileNotFoundError(f"no day folders under {os.path.join(root, 'slim')}")
        s = s or days_avail[0]
        e = e or days_avail[-1]
    if e < s:
        raise ValueError("end must not be before start")

    t0 = time.time()
    st = StreamStats(raw_sha256=hashlib.sha256())
    stream_hash = hashlib.sha256()
    distinct = {kind: U64Set(1 << 16) for kind in KEY_KINDS.values()}
    distinct_countries: set[str] = set()
    mentions = {kind: 0 for kind in KEY_KINDS.values()}
    domains: set[str] = set()
    n_with_title = n_no_keys = 0
    first_ts = last_ts = None

    for a in iter_articles(s, e, root=root, config=cfg, stats=st):
        stream_hash.update(canonical_line(a))
        for k in a.keys:
            kind = KEY_KINDS[k[0]]
            mentions[kind] += 1
            distinct[kind].add(h64(k))
        distinct_countries.update(a.countries)
        domains.add(a.domain)
        n_with_title += bool(a.title)
        n_no_keys += not a.keys
        if first_ts is None:
            first_ts = a.ts
        last_ts = a.ts
    peak_rss = peak_rss_bytes()

    # slot accounting for every day of the requested range
    log = _read_log(root)
    per_day = []
    tot_slots = {"expected": 0, "read": 0, "missing_on_server": 0, "not_downloaded": 0}
    log_mismatch = []
    d = date.fromisoformat(s)
    while d <= date.fromisoformat(e):
        day = d.isoformat()
        ddir = os.path.join(root, "slim", day)
        present = set(os.listdir(ddir)) if os.path.isdir(ddir) else set()
        files = {m.group(1) for n in present if (m := FILE_RE.match(n))}
        missing_marks = {m.group(1) for n in present if (m := MISSING_RE.match(n))}
        missing, absent, log_errors = [], [], []
        for slot in _all_slots():
            stamp = day.replace("-", "") + slot + "00"
            lstat = log.get(stamp)
            if slot in files:
                if lstat not in (None, "ok"):
                    log_mismatch.append(f"{day} {slot}: file present, log says {lstat}")
                continue
            if slot in missing_marks or lstat == "missing":
                missing.append(slot)
                if slot not in missing_marks:
                    log_mismatch.append(f"{day} {slot}: log says missing, no .missing file")
            else:
                absent.append(slot)
                if lstat == "error":
                    log_errors.append(slot)
                elif lstat == "ok":
                    log_mismatch.append(f"{day} {slot}: log says ok, file absent")
        read = len(st.files_per_day.get(day, []))
        per_day.append({"day": day, "articles": st.per_day.get(day, 0), "files_read": read,
                        "missing_on_server": missing, "not_downloaded": absent,
                        "log_error_slots": log_errors})
        tot_slots["expected"] += SLOTS_PER_DAY
        tot_slots["read"] += read
        tot_slots["missing_on_server"] += len(missing)
        tot_slots["not_downloaded"] += len(absent)
        d += timedelta(days=1)

    def iso(ts):
        return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = {
        "kind": "gkg_input_manifest",
        "version": 1,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": command,
        "code": {"git": _git_state(), "gkg_py_sha256": _file_sha256(os.path.abspath(__file__)),
                 "country_names_sha256": _file_sha256(COUNTRY_NAMES_PATH),
                 "country_names": len(COUNTRY_NAMES)},
        "python": sys.version.split()[0],
        "seed": None,  # no randomness in this step
        "root": _rel(root),
        "range": {"start": s, "end": e, "days": len(per_day)},
        "config": asdict(cfg),
        "hashes": {
            "stream_sha256": stream_hash.hexdigest(),
            "stream_sha256_def": "sha256 over canonical_line(a) for every yielded article: "
                                 "JSON [idx,ts,domain,url,title,keys,countries,tone] + '\\n', UTF-8",
            "raw_files_sha256": st.raw_sha256.hexdigest(),
            "raw_files_sha256_def": "sha256 over '<day>/<HHMM>.jsonl.gz\\n<bytes>\\n' + file bytes, in read order",
        },
        "totals": {
            "articles": st.articles,
            "lines": st.lines,
            "bad_lines": st.bad_lines,
            "duplicate_urls_removed": st.duplicate_urls,
            "empty_urls": st.empty_urls,
            "self_orgs_dropped": st.self_orgs_dropped,
            "country_orgs_dropped": st.country_orgs_dropped,
            "ts_out_of_order": st.ts_out_of_order,
            "with_title": n_with_title,
            "without_keys": n_no_keys,
            "domains": len(domains),
            "first_ts": iso(first_ts),
            "last_ts": iso(last_ts),
        },
        "slots": tot_slots,
        "keys": {
            "distinct": {**{k: len(v) for k, v in distinct.items()}, "countries": len(distinct_countries)},
            "mentions": mentions,
            "per_article": {k: round(v / st.articles, 3) if st.articles else 0.0 for k, v in mentions.items()},
            "distinct_def": "distinct keys counted by 64-bit SHA-1 prefix",
        },
        "log_mismatch": log_mismatch,
        "per_day": per_day,
        "run": {"seconds": round(time.time() - t0, 1),
                "peak_rss_mb": round(peak_rss / 1e6, 1) if peak_rss else None,
                "url_table_mb": round(st.url_table_bytes / 1e6, 1)},
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        tmp = out_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, out_path)
    return manifest


def manifest_summary(m: dict) -> str:
    """Short human-readable summary of a manifest (printed and put into summary.md)."""
    t, sl, k = m["totals"], m["slots"], m["keys"]
    lines = [
        f"range        : {m['range']['start']} .. {m['range']['end']} ({m['range']['days']} days, UTC)",
        f"15-min slots : expected {sl['expected']}, read {sl['read']}, missing on server "
        f"{sl['missing_on_server']}, not downloaded {sl['not_downloaded']}",
        f"articles     : {t['articles']:,} kept of {t['lines']:,} lines "
        f"(duplicate URL {t['duplicate_urls_removed']:,}, bad line {t['bad_lines']:,}, "
        f"empty URL {t['empty_urls']:,})",
        f"with title   : {t['with_title'] / max(t['articles'], 1):.1%}   no keys: "
        f"{t['without_keys'] / max(t['articles'], 1):.1%}   domains: {t['domains']:,}",
        f"self-name    : {t['self_orgs_dropped']:,} organization keys dropped",
        f"country org  : {t.get('country_orgs_dropped', 0):,} organization keys equal to a country name dropped",
        "distinct     : " + ", ".join(f"{a} {b:,}" for a, b in k["distinct"].items()),
        "per article  : " + ", ".join(f"{a} {b:.2f}" for a, b in k["per_article"].items()),
        f"time order   : {t['first_ts']} .. {t['last_ts']}, out of order {t['ts_out_of_order']}",
        f"stream sha256: {m['hashes']['stream_sha256']}",
        f"raw sha256   : {m['hashes']['raw_files_sha256']}",
        f"run          : {m['run']['seconds']} s, peak RSS {m['run']['peak_rss_mb']} MB "
        f"(URL hash table {m['run']['url_table_mb']} MB)",
    ]
    if m["log_mismatch"]:
        lines.append(f"WARNING      : {len(m['log_mismatch'])} slot(s) disagree with download_log.csv")
    return "\n".join(lines)


def write_summary_section(path: str, tag: str, block: str) -> None:
    """Replace (or append) the text between <!-- tag:begin --> and <!-- tag:end --> in summary.md."""
    begin, end = f"<!-- {tag}:begin -->", f"<!-- {tag}:end -->"
    block = f"{begin}\n{block.strip()}\n{end}"
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        text = head + block + rest.split(end, 1)[1]
    else:
        text = (text or "# GKG experiments summary\n").rstrip("\n") + "\n\n" + block + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
