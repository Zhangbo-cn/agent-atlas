#!/usr/bin/env python3
"""Build the publishable artifact folder from the local site.

The Artifact host wraps the main page in its own ``<!doctype html>`` skeleton,
so ``dist/index.html`` is the body-level fragment. Every other page is a normal
standalone HTML document and is copied as-is.

Usage: python3 scripts/build_artifact.py
"""

from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def build_index_fragment() -> str:
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    head_match = re.search(r"<head>(.*?)</head>", source, re.S | re.I)
    body_match = re.search(r"<body>(.*?)</body>", source, re.S | re.I)
    if not head_match or not body_match:
        raise SystemExit("index.html must contain <head> and <body>")

    head = head_match.group(1)
    # The host supplies charset and viewport; keep everything else (title,
    # description, font/CSS links, theme bootstrap).
    head = re.sub(r"<meta\s+charset=[^>]*>\s*", "", head, flags=re.I)
    head = re.sub(r"<meta\s+name=\"viewport\"[^>]*>\s*", "", head, flags=re.I)

    return head.strip() + "\n\n" + body_match.group(1).strip() + "\n"


def main() -> None:
    DIST.mkdir(exist_ok=True)
    (DIST / "assets").mkdir(exist_ok=True)

    (DIST / "index.html").write_text(build_index_fragment(), encoding="utf-8")
    for name in ("pi.html",):
        shutil.copy2(ROOT / name, DIST / name)
    for name in ("site.css", "site.js"):
        shutil.copy2(ROOT / "assets" / name, DIST / "assets" / name)

    print(f"Built {DIST}")
    for path in sorted(DIST.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(DIST)}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
