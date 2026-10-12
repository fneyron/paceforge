"""Check the actual iOS plist for HealthKit purpose descriptions."""

import argparse
import plistlib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("plist", type=Path, help="Generated or compiled app Info.plist")
args = parser.parse_args()

with args.plist.open("rb") as source:
    info = plistlib.load(source)

required = ("NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription")
missing = [
    key for key in required
    if not isinstance(info.get(key), str) or not info[key].strip()
]
if missing:
    raise SystemExit(f"{args.plist}: missing purpose string(s): {', '.join(missing)}")

print(f"{args.plist}: HealthKit purpose strings are present.")
