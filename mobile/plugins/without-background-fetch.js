// expo-task-manager's plugin declares the legacy "fetch" background mode, which nothing
// uses: the catch-up task is a BGProcessingTask ("processing", expo-background-task) and
// HealthKit wakes the app through its own entitlement. A declared mode without a use is
// an App Review question (2.5.4). The plugin runs after expo-task-manager's.
const { withInfoPlist } = require('@expo/config-plugins');

module.exports = config => withInfoPlist(config, result => {
  const modes = result.modResults.UIBackgroundModes;
  if (Array.isArray(modes)) result.modResults.UIBackgroundModes = modes.filter(mode => mode !== 'fetch');
  return result;
});
