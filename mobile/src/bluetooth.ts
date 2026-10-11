import { PermissionsAndroid, Platform } from 'react-native';
import BleManager, { BleState, Peripheral } from 'react-native-ble-manager';
import { HEART_RATE_MEASUREMENT, HEART_RATE_SERVICE, isBluetoothUuid } from './heart-rate';
import { HeartEvents, HeartTransport } from './heart-rate-controller';

let started: Promise<void> | null = null;

async function prepare() {
  if (Platform.OS === 'android') {
    const requested = Number(Platform.Version) >= 31
      ? [PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN, PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
    const result = await PermissionsAndroid.requestMultiple(requested);
    if (requested.some(p => result[p] !== PermissionsAndroid.RESULTS.GRANTED)) {
      throw new Error('Autorise l’accès aux appareils à proximité dans les réglages du téléphone. Sur Android 11 et antérieur, la recherche Bluetooth nécessite la localisation.');
    }
  }
  started ??= BleManager.start({ showAlert: false }).catch(e => { started = null; throw e; });
  await started;
  const state = await new Promise<BleState>((resolve, reject) => {
    const finish = (value: BleState) => {
      if ([BleState.Unknown, BleState.Resetting, BleState.TurningOn].includes(value)) return;
      clearTimeout(timer); listener.remove(); resolve(value);
    };
    const listener = BleManager.onDidUpdateState(({ state }) => finish(state));
    const timer = setTimeout(() => { listener.remove(); reject(new Error('Le Bluetooth ne répond pas. Vérifie les réglages puis réessaie.')); }, 10000);
    BleManager.checkState().then(finish).catch(() => { clearTimeout(timer); listener.remove(); reject(new Error('Impossible de vérifier le Bluetooth.')); });
  });
  if (state === BleState.Unauthorized) throw new Error('Autorise PaceForge à utiliser le Bluetooth dans les réglages du téléphone.');
  if (state === BleState.Unsupported) throw new Error('Ce téléphone ne prend pas en charge cette connexion Bluetooth.');
  if (state !== BleState.On) throw new Error('Active le Bluetooth du téléphone puis relance la recherche.');
}

export function bluetoothTransport(): HeartTransport {
  let events: HeartEvents | null = null;
  const discover = (peripheral: Peripheral) => events?.discovered({
    id: peripheral.id,
    name: (peripheral.name || peripheral.advertising?.localName || 'Capteur cardiaque').trim().slice(0, 80),
  });
  return {
    prepare,
    async scan() {
      await BleManager.scan({ serviceUUIDs: [HEART_RATE_SERVICE], seconds: 15, allowDuplicates: false });
      // iOS may already have this monitor connected to another app (e.g. Zwift).
      // Android ignores the service filter in getConnectedPeripherals: don't list unrelated devices.
      if (Platform.OS === 'ios') {
        void BleManager.getConnectedPeripherals([HEART_RATE_SERVICE]).then(items => items.forEach(discover)).catch(() => {});
      }
    },
    stopScan: () => BleManager.stopScan(),
    connect: id => BleManager.connect(id),
    async subscribe(id) {
      const device = await BleManager.retrieveServices(id, [HEART_RATE_SERVICE]);
      const measurement = device.characteristics?.find(c => isBluetoothUuid(c.service, HEART_RATE_SERVICE)
        && isBluetoothUuid(c.characteristic, HEART_RATE_MEASUREMENT) && c.properties.Notify);
      if (!measurement) throw new Error('Ce capteur ne diffuse pas de fréquence cardiaque compatible.');
      await BleManager.startNotification(id, measurement.service, measurement.characteristic);
    },
    disconnect: id => BleManager.disconnect(id),
    listen(callbacks) {
      events = callbacks;
      const subscriptions = [
        BleManager.onDiscoverPeripheral(discover),
        BleManager.onStopScan(() => events?.scanStopped()),
        BleManager.onDisconnectPeripheral(({ peripheral }) => events?.disconnected(peripheral)),
        BleManager.onDidUpdateState(({ state }) => {
          if ([BleState.Off, BleState.TurningOff, BleState.Unsupported].includes(state)) events?.poweredOff();
        }),
        BleManager.onDidUpdateValueForCharacteristic(e => events?.value(e.peripheral, e.service, e.characteristic, e.value)),
      ];
      return () => { events = null; subscriptions.forEach(s => s.remove()); };
    },
  };
}
