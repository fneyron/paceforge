import { registerRootComponent } from 'expo';

// Before the app: background sync (HealthKit subscriptions, periodic task) is set up
// at the top level, and also runs when the system starts the app without a screen.
import './src/background';
import App from './App';

// registerRootComponent calls AppRegistry.registerComponent('main', () => App);
// It also ensures that whether you load the app in Expo Go or in a native build,
// the environment is set up appropriately
registerRootComponent(App);
