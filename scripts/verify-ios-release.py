"""Check the identity and privacy metadata of the IPA about to be submitted."""

import argparse
import json
import plistlib
import re
from zipfile import ZipFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive")
parser.add_argument("app_config")
args = parser.parse_args()

with open(args.app_config, encoding="utf-8") as source:
    config = json.load(source)["expo"]
with ZipFile(args.archive) as archive:
    paths = archive.namelist()
    plists = [path for path in paths if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", path)]
    if len(plists) != 1:
        raise SystemExit("Expected exactly one iPhone application in the IPA.")
    info = plistlib.loads(archive.read(plists[0]))
    if plists[0].replace("Info.plist", "embedded.mobileprovision") not in paths:
        raise SystemExit("The IPA has no embedded provisioning profile.")

if info.get("CFBundleIdentifier") != config["ios"]["bundleIdentifier"]:
    raise SystemExit("The IPA bundle identifier does not match app.json.")
if info.get("CFBundleShortVersionString") != config["version"]:
    raise SystemExit("The IPA app version does not match app.json.")
if info.get("DTPlatformName") != "iphoneos" or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
    raise SystemExit("TestFlight requires a device archive, not a simulator build.")
build = info.get("CFBundleVersion", "")
if not isinstance(build, str) or not re.fullmatch(r"[1-9]\d*(?:\.\d+){0,2}", build):
    raise SystemExit("The IPA has no valid iOS build number.")
for key in ("NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription"):
    if not isinstance(info.get(key), str) or not info[key].strip():
        raise SystemExit(f"The IPA is missing {key}.")
if info.get("ITSAppUsesNonExemptEncryption") is not False:
    raise SystemExit("The IPA is missing the configured encryption declaration.")

print(f"IPA metadata verified: {info['CFBundleIdentifier']} {config['version']} ({build}).")
