"""Build stations.json from the www.klymot.com station index.

Reads the sibling checkout's ``docs/data/index.json`` (or a URL override) and
writes the id/name/lat/lon subset this repo samples forecasts at.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIBLING = REPO_ROOT.parent / "www.klymot.com" / "docs" / "data" / "index.json"
DEFAULT_URL = "https://www.klymot.com/data/index.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=None,
        help="path or URL of the main-site index.json "
        "(default: sibling checkout, falling back to www.klymot.com)",
    )
    args = parser.parse_args()

    if args.source:
        source = args.source
    elif DEFAULT_SIBLING.exists():
        source = str(DEFAULT_SIBLING)
    else:
        source = DEFAULT_URL

    if source.startswith("http"):
        with urllib.request.urlopen(source) as f:
            index = json.load(f)
    else:
        index = json.loads(Path(source).read_text())

    stations = [
        {"id": l["id"], "name": l["name"], "lat": l["lat"], "lon": l["lng"]}
        for l in index["locations"]
    ]
    stations.sort(key=lambda s: s["id"])
    out = REPO_ROOT / "stations.json"
    out.write_text(
        json.dumps({"source": source, "stations": stations}, indent=1) + "\n"
    )
    print(f"wrote {len(stations)} stations to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
