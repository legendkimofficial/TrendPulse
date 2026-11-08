"""Download the HuffPost News Category Dataset (v3, 209,527 articles, CC BY 4.0).

Original: Rishabh Misra, Kaggle "News Category Dataset"
          https://www.kaggle.com/datasets/rmisra/news-category-dataset
Mirror used here (no Kaggle login needed): Hugging Face heegyu/news-category-dataset

    python scripts/download_data.py
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

URL = "https://huggingface.co/datasets/heegyu/news-category-dataset/resolve/main/data.json"
DEST = Path(__file__).resolve().parent.parent / "data" / "raw" / "News_Category_Dataset_v3.json"
EXPECTED_LINES = 209_527


def main() -> int:
    if DEST.exists():
        print(f"already present: {DEST}")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".part")
    print(f"downloading {URL}\n -> {DEST}")

    def progress(blocks, bsize, total):
        if total > 0:
            done = min(100, blocks * bsize * 100 // total)
            print(f"\r  {done:3d}%", end="", flush=True)

    urllib.request.urlretrieve(URL, tmp, progress)
    print()
    with tmp.open(encoding="utf-8") as fh:
        lines = sum(1 for _ in fh)
    if lines != EXPECTED_LINES:
        print(f"warning: expected {EXPECTED_LINES} lines, got {lines}")
    tmp.replace(DEST)
    print(f"ok: {lines:,} articles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
