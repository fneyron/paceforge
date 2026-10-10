const { withMainActivity, withAndroidManifest } = require('@expo/config-plugins');
module.exports = config => withMainActivity(withAndroidManifest(config, result => {
  const manifest = result.modResults.manifest;
  manifest.queries = manifest.queries || [{}];
  const queries = manifest.queries[0];
  queries.package = queries.package || [];
  if (!queries.package.some(p => p.$['android:name'] === 'com.google.android.apps.healthdata')) {
    queries.package.push({ $: { 'android:name': 'com.google.android.apps.healthdata' } });
  }
  return result;
}), result => {
  let source = result.modResults.contents;
  const entry = 'HealthConnectPermissionDelegate.setPermissionDelegate(this)';
  if (!source.includes(entry)) {
    source = source.replace('import android.os.Bundle', 'import dev.matinzd.healthconnect.permissions.HealthConnectPermissionDelegate\nimport android.os.Bundle');
    // Must register before the activity reaches STARTED (including process recreation).
    source = source.replace(/super\.onCreate\([^\n]*\)/, match => `${match}\n    ${entry}`);
    if (!source.includes(entry)) throw new Error('Health Connect: MainActivity.onCreate introuvable');
  }
  if (!source.includes('paceforge://privacy')) {
    const translate = `
  private fun healthPrivacyIntent(intent: android.content.Intent?) {
    if (intent?.action == "androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE" ||
        intent?.action == "android.intent.action.VIEW_PERMISSION_USAGE") {
      intent.data = android.net.Uri.parse("paceforge://privacy")
    }
  }
  override fun onNewIntent(intent: android.content.Intent) {
    healthPrivacyIntent(intent)
    super.onNewIntent(intent)
  }
`;
    source = source.replace('    super.onCreate(null)', '    healthPrivacyIntent(intent)\n    super.onCreate(null)');
    source = source.replace('class MainActivity : ReactActivity() {', 'class MainActivity : ReactActivity() {' + translate);
  }
  result.modResults.contents = source;
  return result;
});
