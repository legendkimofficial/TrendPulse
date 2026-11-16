"""Fixed replay input for the GDELT GAL submission (see scripts/fetch_gdelt_gal.py).

articles.jsonl is sorted by (observed_at_utc, canonical_url) and deduplicated
by canonical_url (earliest observation kept). The loader is a pure function of
that file: two loads give the identical article order, so two replays over the
same file are comparable position by position.

Keyword extraction rule (fixed for the submission): the repository tokenizer
``trendpulse.text.tokenize`` — lowercase words of length >= 3 that are not
stopwords and contain no digits; one article contributes at most once per
keyword (document frequency) — minus a small GAL-specific stopword extension
(``GAL_STOPWORDS`` below: URL fragments like "com"/"www" and generic headline
words that the HuffPost-tuned list does not cover). Multi-word phrases are NOT
used here. The pickle cache stores the raw tokenizer output, so the extension
can change without re-tokenizing the stream.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .text import tokenize

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_PATH = ROOT / "data" / "processed" / "gdelt" / "articles.jsonl"
CACHE_PATH = ROOT / "data" / "processed" / "gdelt" / "articles.pkl"

# GAL-specific additions (documented part of the extraction rule, v1):
# bare domain labels and generic headline filler that survive the base list.
GAL_STOPWORDS = frozenset({
    "com", "www", "http", "https", "daily", "update", "updates", "latest",
    "breaking", "full", "video", "watch", "photos", "photo", "gallery",
})


def parse_iso(ts: str) -> int:
    """ISO-8601 Z timestamp -> epoch seconds (UTC)."""
    return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


@dataclass(slots=True)
class GalArticle:
    i: int                  # replay order (the stream clock, 0-based)
    obs: int                # batch observation time, epoch seconds (replay clock)
    obs_iso: str
    url: str                # original URL from GAL (preserved)
    curl: str               # canonical URL (dedup identity)
    domain: str             # raw domain field
    domain_std: str         # standardized registrable domain (www. dropped)
    outlet: str             # outletName (or standardized domain)
    country: str            # publisher country, ISO alpha-2 or 'unknown'
    title: str
    desc: str
    adate: str | None       # GAL 'date' field (estimated publish time), may be None
    tokens: tuple[str, ...]  # distinct keywords (extraction rule fixed above)


def _filter_tokens(toks: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t for t in toks if t not in GAL_STOPWORDS)


def load_articles(path: Path = ARTICLES_PATH, use_cache: bool = True) -> list[GalArticle]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run: python scripts/fetch_gdelt_gal.py collect --start "
            f"2026-11-09 --end 2026-11-15  &&  python scripts/fetch_gdelt_gal.py normalize")
    if use_cache and CACHE_PATH.exists() and CACHE_PATH.stat().st_mtime >= path.stat().st_mtime:
        with CACHE_PATH.open("rb") as fh:
            arts = pickle.load(fh)
    else:
        arts = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                arts.append(GalArticle(
                    i=r["i"], obs=parse_iso(r["obs"]), obs_iso=r["obs"], url=r["url"],
                    curl=r["curl"], domain=r["domain"], domain_std=r["domain_std"],
                    outlet=r["outlet"], country=r["country"], title=r["title"],
                    desc=r["desc"], adate=r.get("adate"), tokens=tuple(tokenize(r["title"])),
                ))
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("wb") as fh:
            pickle.dump(arts, fh, protocol=pickle.HIGHEST_PROTOCOL)
    # extraction-rule extension applied at load time (idempotent), so the raw
    # tokenizer output in the cache stays reusable if the list changes
    for a in arts:
        if GAL_STOPWORDS.intersection(a.tokens):
            a.tokens = _filter_tokens(a.tokens)
    return arts
