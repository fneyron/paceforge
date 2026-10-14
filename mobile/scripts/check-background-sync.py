"""Check the generated native projects for background health sync.

iOS: the HealthKit background-delivery entitlement, the "processing" background
mode and the BGTaskScheduler identifier of expo-background-task. Android: the
Health Connect permission to read in the background.
"""

import argparse
import plistlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TASK_IDENTIFIER = "com.expo.modules.backgroundtask.processing"
DELIVERY = "com.apple.developer.healthkit.background-delivery"
ANDROID_PERMISSION = "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"
ANDROID_NAME = "{http://schemas.android.com/apk/res/android}name"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--entitlements", type=Path, help="Generated ios/<App>/<App>.entitlements")
parser.add_argument("--info-plist", type=Path, help="Generated or compiled Info.plist")
parser.add_argument("--android-manifest", type=Path, help="Generated android/app/src/main/AndroidManifest.xml")
args = parser.parse_args()
if not (args.entitlements or args.info_plist or args.android_manifest):
    parser.error("nothing to check")

errors = []
if args.entitlements:
    with args.entitlements.open("rb") as source:
        entitlements = plistlib.load(source)
    if entitlements.get("com.apple.developer.healthkit") is not True:
        errors.append(f"{args.entitlements}: HealthKit entitlement missing")
    if entitlements.get(DELIVERY) is not True:
        errors.append(f"{args.entitlements}: {DELIVERY} missing (HealthKit plugin \"background\": true)")
if args.info_plist:
    with args.info_plist.open("rb") as source:
        info = plistlib.load(source)
    if "processing" not in info.get("UIBackgroundModes", []):
        errors.append(f"{args.info_plist}: UIBackgroundModes lacks \"processing\" (expo-background-task plugin)")
    if TASK_IDENTIFIER not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
        errors.append(f"{args.info_plist}: BGTaskSchedulerPermittedIdentifiers lacks {TASK_IDENTIFIER}")
if args.android_manifest:
    permissions = {element.get(ANDROID_NAME) for element in ET.parse(args.android_manifest).getroot().iter("uses-permission")}
    if ANDROID_PERMISSION not in permissions:
        errors.append(f"{args.android_manifest}: {ANDROID_PERMISSION} not declared")

if errors:
    sys.exit("\n".join(errors))
print("Background health sync configuration is present: "
      + ", ".join(str(p) for p in (args.entitlements, args.info_plist, args.android_manifest) if p) + ".")
