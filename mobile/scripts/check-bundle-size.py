"""Fail when an exported JavaScript bundle (npx expo export) is over its size budget."""

import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("dist", type=Path, help="the export folder, with metadata.json")
parser.add_argument("--limit", type=int, default=2_200_000, help="bytes per platform (default 2.2 MB)")
args = parser.parse_args()

with (args.dist / "metadata.json").open(encoding="utf-8") as source:
    platforms = json.load(source)["fileMetadata"]
errors = []
for platform, files in sorted(platforms.items()):
    bundle = args.dist / files["bundle"]
    size = bundle.stat().st_size
    print(f"{platform}: {size / 1e6:.2f} MB, budget {args.limit / 1e6:.1f} MB ({bundle.name})")
    if size > args.limit:
        errors.append(f"{platform}: the bundle is {size - args.limit:,} bytes over its budget.")
if errors:
    sys.exit("\n".join(errors))
