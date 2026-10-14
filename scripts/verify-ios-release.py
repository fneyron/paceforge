"""Check the identity, privacy and background metadata of the IPA about to be submitted."""

import argparse
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

DELIVERY = "com.apple.developer.healthkit.background-delivery"
TASK_IDENTIFIER = "com.expo.modules.backgroundtask.processing"


def embedded_plist(data: bytes, what: str) -> dict:
    """The XML property list inside a CMS-signed provisioning profile or codesign output."""
    start, end = data.find(b"<?xml"), data.rfind(b"</plist>")
    if start < 0 or end < start:
        raise SystemExit(f"{what}: no property list found.")
    return plistlib.loads(data[start:end + len(b"</plist>")])


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
    profile_path = plists[0].replace("Info.plist", "embedded.mobileprovision")
    if profile_path not in paths:
        raise SystemExit("The IPA has no embedded provisioning profile.")
    profile = embedded_plist(archive.read(profile_path), "embedded.mobileprovision")
    signed = None
    if shutil.which("codesign"):
        # The entitlements the app was actually signed with (macOS runner).
        with tempfile.TemporaryDirectory() as folder:
            archive.extractall(folder, [name for name in paths if name.startswith(plists[0].rsplit("/", 1)[0] + "/")])
            app = Path(folder, plists[0]).parent
            result = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(app)], capture_output=True)
            if result.returncode or b"<?xml" not in result.stdout:
                result = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(app)], capture_output=True)
            signed = embedded_plist(result.stdout, "codesign entitlements")

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
# Background sync: weigh-ins and nights wake the app (HealthKit background delivery),
# and a periodic BGProcessingTask catches up.
if profile.get("Entitlements", {}).get(DELIVERY) is not True:
    raise SystemExit(f"The provisioning profile lacks {DELIVERY}: regenerate it once "
                     "(docs/testflight-without-expo-build-quota.md), the release workflow freezes credentials.")
if signed is not None and signed.get(DELIVERY) is not True:
    raise SystemExit(f"The app is not signed with {DELIVERY}.")
if "processing" not in info.get("UIBackgroundModes", []) or TASK_IDENTIFIER not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
    raise SystemExit("The IPA is missing the background task configuration (processing mode and its identifier).")

print(f"IPA metadata verified: {info['CFBundleIdentifier']} {config['version']} ({build}).")
