"""Check the actual iOS plist for descriptions required by the linked HealthKit SDK."""

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
    raise SystemExit(f"{args.plist}: missing HealthKit purpose string(s): {', '.join(missing)}")

print(f"{args.plist}: both HealthKit purpose strings are present.")
