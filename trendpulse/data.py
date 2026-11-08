"""Load the HuffPost News Category Dataset as an ordered article stream.

The dataset only has a calendar date per article (no time of day). The raw file
is sorted newest-first, so we reverse it to get a deterministic oldest-first
stream; articles from the same day keep the reversed file order. The replay is
therefore a *simulation* of a live feed, not real-time collection.

Cleaning rules (v2): articles with an empty headline are dropped, and when the
same link appears more than once only the first occurrence in stream order is
kept. Identical headlines with *different* links are kept (they can be separate
articles, e.g. recurring column titles); their count is reported only.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

from .text import tokenize

ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "raw" / "News_Category_Dataset_v3.json"
CACHE_PATH = ROOT / "data" / "processed" / "stream_v2.pkl"


@dataclass(slots=True)
class Article:
    idx: int            # position in the stream (0-based); the stream clock
    date: str           # YYYY-MM-DD
    headline: str
    description: str
    category: str
    link: str
    tokens: tuple[str, ...]


def _build(raw_path: Path) -> tuple[list[Article], dict]:
    rows = []
    with raw_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.reverse()  # newest-first file -> oldest-first stream
    # Stable sort guards against any out-of-order lines in the source file.
    rows.sort(key=lambda r: r["date"])
    report = {"raw_rows": len(rows), "dropped_empty_headline": 0, "dropped_duplicate_link": 0}
    seen_links: set[str] = set()
    date_headline: set[tuple[str, str]] = set()
    same_date_headline = 0
    articles: list[Article] = []
    for r in rows:
        headline = (r.get("headline") or "").strip()
        link = (r.get("link") or "").strip()
        if not headline:
            report["dropped_empty_headline"] += 1
            continue
        if link and link in seen_links:
            report["dropped_duplicate_link"] += 1
            continue
        seen_links.add(link)
        key = (r["date"], headline)
        if key in date_headline:
            same_date_headline += 1
        date_headline.add(key)
        articles.append(Article(
            idx=len(articles),
            date=r["date"],
            headline=headline,
            description=(r.get("short_description") or "").strip(),
            category=r.get("category", ""),
            link=link,
            tokens=tuple(tokenize(headline)),
        ))
    report["kept"] = len(articles)
    report["kept_same_date_and_headline_extra"] = same_date_headline
    report["kept_without_tokens"] = sum(1 for a in articles if not a.tokens)
    return articles, report


def load_stream(raw_path: Path = RAW_PATH, cache_path: Path = CACHE_PATH,
                use_cache: bool = True) -> list[Article]:
    """Return all articles in stream order, using a pickle cache when fresh."""
    return _load(raw_path, cache_path, use_cache)[0]


def quality_report(raw_path: Path = RAW_PATH, cache_path: Path = CACHE_PATH) -> dict:
    """Counts of what the cleaning rules removed / kept (see module docstring)."""
    return _load(raw_path, cache_path, True)[1]


def _load(raw_path: Path, cache_path: Path, use_cache: bool) -> tuple[list[Article], dict]:
    if not raw_path.exists():
        raise FileNotFoundError(
            f"Dataset not found at {raw_path}. Run: python scripts/download_data.py")
    # The cache is stale if the raw data, the tokenizer or this loader changed after it was written.
    sources = [raw_path, Path(__file__).with_name("text.py"), Path(__file__)]
    newest_input = max(p.stat().st_mtime for p in sources)
    if use_cache and cache_path.exists() and cache_path.stat().st_mtime >= newest_input:
        with cache_path.open("rb") as fh:
            return pickle.load(fh)
    built = _build(raw_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump(built, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return built


def first_index_on_or_after(articles: list[Article], date: str) -> int:
    """Binary search for the first article whose date >= ``date``."""
    lo, hi = 0, len(articles)
    while lo < hi:
        mid = (lo + hi) // 2
        if articles[mid].date < date:
            lo = mid + 1
        else:
            hi = mid
    return lo
