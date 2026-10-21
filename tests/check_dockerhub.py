#!/usr/bin/env python3
"""Checks the Docker Hub overview (docs/dockerhub.md, which the release workflow publishes as the image's
page): it fits Docker Hub's 25,000-byte limit, uses only Markdown Docker Hub renders (no <picture>, HTML
images or Mermaid), and every link and image is an absolute https URL, images as raw files, because Docker
Hub can't resolve paths in the repository. Each image must exist in the checkout. Standard library only."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/HoneyBearTech/ubuntu-server-auto-setup/main/"
LIMIT = 25000


def problems(text: str) -> list[str]:
    found = []
    if len(text.encode()) > LIMIT:
        found.append(f"{len(text.encode())} bytes, more than Docker Hub's {LIMIT}")
    for pattern, what in ((r"<picture|<source|<img", "HTML images"), (r"```mermaid", "a Mermaid diagram")):
        if re.search(pattern, text, re.I):
            found.append(f"{what}, which Docker Hub doesn't render")
    for image, url in re.findall(r"(!?)\[[^\]]*\]\(([^)\s]+)", text):
        if image:
            if not url.startswith(RAW):
                found.append(f"image {url} isn't a raw URL under {RAW}")
            elif not (ROOT / url[len(RAW):]).is_file():
                found.append(f"image {url} isn't in the checkout")
        elif not url.startswith(("https://", "http://localhost")):
            found.append(f"link {url} isn't an absolute https URL")
    return found


if __name__ == "__main__":
    path = ROOT / "docs" / "dockerhub.md"
    found = problems(path.read_text())
    for problem in found:
        print(f"docs/dockerhub.md: {problem}", file=sys.stderr)
    if found:
        sys.exit(1)
    print(f"docs/dockerhub.md: {len(path.read_bytes())} bytes, every link and image absolute")
