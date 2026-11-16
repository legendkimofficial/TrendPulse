"""Build the GKG input manifest and print a summary.

  python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01

Writes experiments/results/gkg/input_manifest.json and updates the
"input manifest" section of experiments/results/gkg/summary.md.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trendpulse.gkg import (DEFAULT_MANIFEST, DEFAULT_ROOT, GkgConfig,  # noqa: E402
                            build_manifest, manifest_summary)

BEGIN = "<!-- input_manifest:begin -->"
END = "<!-- input_manifest:end -->"


def update_summary_md(path: str, m: dict, manifest_path: str) -> None:
    block = "\n".join([
        BEGIN,
        "## Input manifest",
        "",
        f"- command: `{m['command']}`",
        f"- manifest: `{os.path.relpath(manifest_path).replace(os.sep, '/')}`",
        f"- git: {m['code']['git']['commit']} (dirty={m['code']['git']['dirty']}), "
        f"gkg.py sha256 {m['code']['gkg_py_sha256'][:16]}…",
        f"- config: {m['config']}",
        "",
        "```",
        manifest_summary(m),
        "```",
        END,
    ])
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        tail = rest.split(END, 1)[1]
        text = head + block + tail
    else:
        if not text:
            text = "# GKG experiments summary\n\n"
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="first day YYYY-MM-DD (default: first folder)")
    ap.add_argument("--end", help="last day YYYY-MM-DD, inclusive (default: last folder)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="folder holding slim/ and download_log.csv")
    ap.add_argument("--out", default=DEFAULT_MANIFEST)
    ap.add_argument("--include-countries", action="store_true", help="also use country places as keys")
    args = ap.parse_args()

    cfg = GkgConfig(include_countries=args.include_countries)
    command = "python " + " ".join(shlex.quote(a) for a in
                                   [os.path.relpath(sys.argv[0]).replace(os.sep, "/"), *sys.argv[1:]])
    m = build_manifest(args.start, args.end, root=args.root, config=cfg, out_path=args.out, command=command)
    print(manifest_summary(m))
    update_summary_md(os.path.join(os.path.dirname(os.path.abspath(args.out)), "summary.md"), m, args.out)
    print(f"\nwrote {os.path.relpath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
