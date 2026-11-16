"""Evidence store for GKG replays (sqlite3, on disk).

Only keys that have been in the Top N (default 200) of any window at some snapshot are
"tracked"; for them the store keeps postings (key -> article idx) and the article rows
(idx, ts, domain, url, title) those postings point to. Untracked articles are not stored.

Limitation: the replay is one pass, so postings start at the snapshot where the key was
first tracked. Articles that pushed a key into the Top N before that snapshot (at most
one snapshot interval, but possibly the whole early build-up of a slow riser) have no
postings. ``first_tracked_ts`` in the keys table records where coverage starts.

The store is reset on open (a replay always restarts at idx 0).
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "gdelt", "processed", "evidence.sqlite")

SCHEMA = """
CREATE TABLE articles (idx INTEGER PRIMARY KEY, ts INTEGER NOT NULL, domain TEXT,
                       url TEXT, title TEXT);
CREATE TABLE keys (key_id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE,
                   first_tracked_ts INTEGER NOT NULL, first_window TEXT NOT NULL);
CREATE TABLE postings (key_id INTEGER NOT NULL, idx INTEGER NOT NULL,
                       PRIMARY KEY (key_id, idx)) WITHOUT ROWID;
"""


class EvidenceStore:
    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        for suffix in ("", "-journal", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=OFF")      # rebuildable cache: speed over safety
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.executescript(SCHEMA)
        self.tracked: dict[str, int] = {}
        self._articles: list[tuple] = []
        self._postings: list[tuple[int, int]] = []
        self.n_articles = 0
        self.n_postings = 0

    def track(self, keys, ts: float, window: str) -> int:
        """Start tracking keys (no-op for keys already tracked). Returns how many are new."""
        new = []
        for k in keys:
            if k not in self.tracked:
                self.tracked[k] = len(self.tracked) + 1
                new.append((self.tracked[k], k, int(ts), window))
        if new:
            self.db.executemany("INSERT INTO keys VALUES (?,?,?,?)", new)
        return len(new)

    def add_article(self, a) -> None:
        tr = self.tracked
        ids = [tr[k] for k in a.keys if k in tr]
        if not ids:
            return
        self._articles.append((a.idx, a.ts, a.domain, a.url, a.title))
        self._postings.extend((i, a.idx) for i in ids)

    def flush(self) -> None:
        if self._articles:
            self.db.executemany("INSERT INTO articles VALUES (?,?,?,?,?)", self._articles)
            self.db.executemany("INSERT INTO postings VALUES (?,?)", self._postings)
            self.n_articles += len(self._articles)
            self.n_postings += len(self._postings)
            self._articles, self._postings = [], []
        self.db.commit()

    def articles_for(self, key: str, limit: int = 20) -> list[dict]:
        """Most recent stored articles for a tracked key."""
        kid = self.tracked.get(key)
        if kid is None:
            return []
        rows = self.db.execute(
            "SELECT a.idx, a.ts, a.domain, a.url, a.title FROM postings p JOIN articles a "
            "ON a.idx = p.idx WHERE p.key_id = ? ORDER BY a.idx DESC LIMIT ?", (kid, limit)).fetchall()
        return [dict(zip(("idx", "ts", "domain", "url", "title"), r)) for r in rows]

    def report(self) -> dict:
        self.flush()
        return {"path": self.path.replace("\\", "/"), "tracked_keys": len(self.tracked),
                "articles": self.n_articles, "postings": self.n_postings,
                "file_bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0}

    def close(self) -> None:
        self.flush()
        self.db.close()
