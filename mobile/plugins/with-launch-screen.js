// Launch screen in the app's colour, colour only: the bare template shows a white
// storyboard on iOS and its sample logo as the Android window background.
const fs = require('fs');
const path = require('path');
const { AndroidConfig, withAndroidColors, withAndroidStyles, withDangerousMod } = require('@expo/config-plugins');

const COLOR = '#0B5F6E'; // the icon background (app.json android.adaptiveIcon.backgroundColor)
const [red, green, blue] = [1, 3, 5].map(i => parseInt(COLOR.slice(i, i + 2), 16) / 255);
const storyboard = `<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.CocoaTouch.Storyboard.XIB" version="3.0" toolsVersion="22155" targetRuntime="iOS.CocoaTouch" propertyAccessControl="none" useAutolayout="YES" launchScreen="YES" useTraitCollections="YES" useSafeAreas="YES" colorMatched="YES" initialViewController="EXPO-VIEWCONTROLLER-1">
    <device id="retina6_12" orientation="portrait" appearance="light"/>
    <dependencies>
        <deployment identifier="iOS"/>
        <plugIn identifier="com.apple.InterfaceBuilder.IBCocoaTouchPlugin" version="22131"/>
        <capability name="Safe area layout guides" minToolsVersion="9.0"/>
        <capability name="documents saved in the Xcode 8 format" minToolsVersion="8.0"/>
    </dependencies>
    <scenes>
        <scene sceneID="EXPO-SCENE-1">
            <objects>
                <viewController storyboardIdentifier="SplashScreenViewController" id="EXPO-VIEWCONTROLLER-1" sceneMemberID="viewController">
                    <view key="view" userInteractionEnabled="NO" contentMode="scaleToFill" insetsLayoutMarginsFromSafeArea="NO" id="EXPO-ContainerView" userLabel="ContainerView">
                        <rect key="frame" x="0.0" y="0.0" width="393" height="852"/>
                        <autoresizingMask key="autoresizingMask" flexibleMaxX="YES" flexibleMaxY="YES"/>
                        <viewLayoutGuide key="safeArea" id="Rmq-lb-GrQ"/>
                        <color key="backgroundColor" red="${red}" green="${green}" blue="${blue}" alpha="1" colorSpace="custom" customColorSpace="sRGB"/>
                    </view>
                </viewController>
                <placeholder placeholderIdentifier="IBFirstResponder" id="EXPO-PLACEHOLDER-1" userLabel="First Responder" sceneMemberID="firstResponder"/>
            </objects>
            <point key="canvasLocation" x="0.0" y="0.0"/>
        </scene>
    </scenes>
</document>
`;

module.exports = config => {
  config = withDangerousMod(config, ['ios', result => {
    const { platformProjectRoot, projectName } = result.modRequest;
    // Named by UILaunchStoryboardName in the generated Info.plist.
    fs.writeFileSync(path.join(platformProjectRoot, projectName, 'SplashScreen.storyboard'), storyboard);
    return result;
  }]);
  config = withAndroidColors(config, result => {
    result.modResults = AndroidConfig.Colors.assignColorValue(result.modResults, { name: 'splashscreen_background', value: COLOR });
    return result;
  });
  // Also what Android 12+ shows behind the icon (windowSplashScreenBackground follows a colour windowBackground).
  return withAndroidStyles(config, result => {
    result.modResults = AndroidConfig.Styles.assignStylesValue(result.modResults, { add: true, parent: { name: 'Theme.App.SplashScreen' },
      name: 'android:windowBackground', value: '@color/splashscreen_background' });
    return result;
  });
};
