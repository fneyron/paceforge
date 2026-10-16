"""Check the identity, privacy and background metadata of the iPhone app.

Given an .ipa, the archive about to be submitted (release workflow): its Info.plist,
privacy manifest, provisioning profile and signed entitlements. Given --prebuilt and
the generated ios/<App> folder, the same Info.plist and privacy manifest checks before
any build (CI, after `expo prebuild`); the profile, signature and platform are not
known yet.
"""

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
# Required-reason APIs used by the Expo / React Native code compiled into the app
# (app.json ios.privacyManifests): category -> the one reason PaceForge may claim.
REQUIRED_REASON_APIS = {
    "NSPrivacyAccessedAPICategoryFileTimestamp": "C617.1",
    "NSPrivacyAccessedAPICategoryUserDefaults": "CA92.1",
    "NSPrivacyAccessedAPICategorySystemBootTime": "35F9.1",
    "NSPrivacyAccessedAPICategoryDiskSpace": "E174.1",
}
# Defaults written by Expo plugins when a purpose string is not configured.
ENGLISH_DEFAULTS = ("Allow $(PRODUCT_NAME)", "Expo Dev Launcher")
HEALTH = "NSPrivacyCollectedDataTypeHealth"
APP_FUNCTIONALITY = "NSPrivacyCollectedDataTypePurposeAppFunctionality"


def embedded_plist(data: bytes, what: str) -> dict:
    """The XML property list inside a CMS-signed provisioning profile or codesign output."""
    start, end = data.find(b"<?xml"), data.rfind(b"</plist>")
    if start < 0 or end < start:
        raise SystemExit(f"{what}: no property list found.")
    return plistlib.loads(data[start:end + len(b"</plist>")])


def plugin_option(config: dict, plugin: str, key: str):
    """An option given to a config plugin in app.json, or None."""
    for entry in config.get("plugins", []):
        if isinstance(entry, list) and entry and entry[0] == plugin and len(entry) > 1:
            return entry[1].get(key)
    return None


def check_info(info: dict, config: dict, built: bool) -> list[str]:
    """Info.plist against app.json. `built`: a compiled archive, not the generated project."""
    errors = []
    if built:
        if info.get("CFBundleIdentifier") != config["ios"]["bundleIdentifier"]:
            errors.append("The IPA bundle identifier does not match app.json.")
        if info.get("DTPlatformName") != "iphoneos" or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
            errors.append("TestFlight requires a device archive, not a simulator build.")
        build = info.get("CFBundleVersion", "")
        if not isinstance(build, str) or not re.fullmatch(r"[1-9]\d*(?:\.\d+){0,2}", build):
            errors.append("The IPA has no valid iOS build number.")
    if info.get("CFBundleShortVersionString") != config["version"]:
        errors.append(f"The app version {info.get('CFBundleShortVersionString')!r} does not match app.json ({config['version']}).")
    for key in ("NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription"):
        if not isinstance(info.get(key), str) or not info[key].strip():
            errors.append(f"Missing {key}.")
    # Only the French purpose strings configured in app.json: no English plugin defaults.
    configured = {
        "NSFaceIDUsageDescription": plugin_option(config, "expo-secure-store", "faceIDPermission"),
        "NSLocalNetworkUsageDescription": config["ios"].get("infoPlist", {}).get("NSLocalNetworkUsageDescription"),
    }
    for key, expected in configured.items():
        value = info.get(key)
        if value is None:
            continue  # stripped from a release build: fine
        if any(english in value for english in ENGLISH_DEFAULTS):
            errors.append(f"{key} is the English development default.")
        elif value != expected:
            errors.append(f"{key} is not the text configured in app.json.")
    if info.get("NSFaceIDUsageDescription") is None:
        errors.append("Missing NSFaceIDUsageDescription (expo-secure-store).")
    if info.get("CFBundleLocalizations") != ["fr"]:
        errors.append("CFBundleLocalizations must be exactly [\"fr\"] (the store lists the app as French).")
    if info.get("CFBundleDevelopmentRegion") != "fr":
        errors.append("CFBundleDevelopmentRegion must be fr.")
    if info.get("UIUserInterfaceStyle") != "Automatic":
        errors.append("UIUserInterfaceStyle must be Automatic (the WebView reports the system scheme).")
    if info.get("ITSAppUsesNonExemptEncryption") is not False:
        errors.append("Missing the configured encryption declaration.")
    # Background sync: weigh-ins and nights wake the app (HealthKit background delivery),
    # and a periodic BGProcessingTask catches up. Nothing uses the legacy fetch mode.
    modes = info.get("UIBackgroundModes", [])
    if "fetch" in modes:
        errors.append("UIBackgroundModes declares the unused \"fetch\" mode (plugins/without-background-fetch).")
    if "processing" not in modes or TASK_IDENTIFIER not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
        errors.append("Missing the background task configuration (processing mode and its identifier).")
    return errors


def check_privacy(privacy: dict, info: dict) -> list[str]:
    """PrivacyInfo.xcprivacy: required-reason APIs, and data types coherent with HealthKit use."""
    errors = []
    if privacy.get("NSPrivacyTracking") is not False or privacy.get("NSPrivacyTrackingDomains", []):
        errors.append("The privacy manifest must declare no tracking and no tracking domain.")
    reasons = {entry.get("NSPrivacyAccessedAPIType"): entry.get("NSPrivacyAccessedAPITypeReasons", [])
               for entry in privacy.get("NSPrivacyAccessedAPITypes", [])}
    for category, reason in REQUIRED_REASON_APIS.items():
        if reasons.get(category) != [reason]:
            errors.append(f"{category} must declare exactly the reason {reason}.")
    for category in reasons.keys() - REQUIRED_REASON_APIS.keys():
        errors.append(f"{category} is declared but has no known use in PaceForge.")
    collected = {entry.get("NSPrivacyCollectedDataType"): entry for entry in privacy.get("NSPrivacyCollectedDataTypes", [])}
    if "NSHealthShareUsageDescription" in info:
        health = collected.get(HEALTH)
        if not health:
            errors.append(f"The app reads HealthKit but does not declare {HEALTH}.")
        elif (health.get("NSPrivacyCollectedDataTypeLinked") is not True or health.get("NSPrivacyCollectedDataTypeTracking") is not False
              or health.get("NSPrivacyCollectedDataTypePurposes") != [APP_FUNCTIONALITY]):
            errors.append(f"{HEALTH} must be linked to the account, never for tracking, for app functionality only.")
    for name, entry in collected.items():
        if entry.get("NSPrivacyCollectedDataTypeTracking") is not False:
            errors.append(f"{name} must not be collected for tracking.")
    return errors


def checked_keys() -> str:
    return ("Info.plist: CFBundleShortVersionString, NSHealthShareUsageDescription, NSHealthUpdateUsageDescription, "
            "NSFaceIDUsageDescription, NSLocalNetworkUsageDescription, CFBundleLocalizations, CFBundleDevelopmentRegion, "
            "UIUserInterfaceStyle, ITSAppUsesNonExemptEncryption, UIBackgroundModes, BGTaskSchedulerPermittedIdentifiers; "
            "PrivacyInfo.xcprivacy: NSPrivacyTracking, NSPrivacyTrackingDomains, NSPrivacyAccessedAPITypes, NSPrivacyCollectedDataTypes")


def verify_prebuilt(folder: Path, config: dict) -> None:
    info = plistlib.loads((folder / "Info.plist").read_bytes())
    privacy_path = folder / "PrivacyInfo.xcprivacy"
    if not privacy_path.is_file():
        raise SystemExit(f"{privacy_path}: missing (app.json ios.privacyManifests).")
    privacy = plistlib.loads(privacy_path.read_bytes())
    entitlements_path = next(folder.glob("*.entitlements"), None)
    if entitlements_path is None:
        raise SystemExit(f"{folder}: no .entitlements file (HealthKit plugin with \"background\": true).")
    entitlements = plistlib.loads(entitlements_path.read_bytes())
    errors = check_info(info, config, built=False) + check_privacy(privacy, info)
    if entitlements.get("com.apple.developer.healthkit") is not True or entitlements.get(DELIVERY) is not True:
        errors.append(f"The entitlements lack HealthKit or {DELIVERY}.")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Generated project verified ({folder}, version {config['version']}); profile, signature and platform are checked on the IPA.")
    print(checked_keys() + "; entitlements: com.apple.developer.healthkit, " + DELIVERY + ".")


def verify_archive(archive: Path, config: dict) -> None:
    with ZipFile(archive) as zipped:
        paths = zipped.namelist()
        plists = [path for path in paths if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", path)]
        if len(plists) != 1:
            raise SystemExit("Expected exactly one iPhone application in the IPA.")
        app_dir = plists[0].rsplit("/", 1)[0]
        info = plistlib.loads(zipped.read(plists[0]))
        privacy_path = f"{app_dir}/PrivacyInfo.xcprivacy"
        if privacy_path not in paths:
            raise SystemExit("The IPA has no PrivacyInfo.xcprivacy (app.json ios.privacyManifests).")
        privacy = plistlib.loads(zipped.read(privacy_path))
        profile_path = f"{app_dir}/embedded.mobileprovision"
        if profile_path not in paths:
            raise SystemExit("The IPA has no embedded provisioning profile.")
        profile = embedded_plist(zipped.read(profile_path), "embedded.mobileprovision")
        signed = None
        if shutil.which("codesign"):
            # The entitlements the app was actually signed with (macOS runner).
            with tempfile.TemporaryDirectory() as folder:
                zipped.extractall(folder, [name for name in paths if name.startswith(app_dir + "/")])
                app = Path(folder, plists[0]).parent
                result = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(app)], capture_output=True)
                if result.returncode or b"<?xml" not in result.stdout:
                    result = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(app)], capture_output=True)
                signed = embedded_plist(result.stdout, "codesign entitlements")
    errors = check_info(info, config, built=True) + check_privacy(privacy, info)
    if profile.get("Entitlements", {}).get(DELIVERY) is not True:
        errors.append(f"The provisioning profile lacks {DELIVERY}: regenerate it once "
                      "(docs/testflight-without-expo-build-quota.md), the release workflow freezes credentials.")
    if signed is not None and signed.get(DELIVERY) is not True:
        errors.append(f"The app is not signed with {DELIVERY}.")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"IPA metadata verified: {info['CFBundleIdentifier']} {config['version']} ({info['CFBundleVersion']}).")
    print(checked_keys() + "; embedded.mobileprovision and codesign: " + DELIVERY + ".")


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("source", type=Path, help="the .ipa, or with --prebuilt the generated ios/<App> folder")
parser.add_argument("app_config", type=Path, help="mobile/app.json")
parser.add_argument("--prebuilt", action="store_true", help="check the generated project instead of an archive")
args = parser.parse_args()

with args.app_config.open(encoding="utf-8") as source:
    app_config = json.load(source)["expo"]
if args.prebuilt:
    verify_prebuilt(args.source, app_config)
else:
    verify_archive(args.source, app_config)
