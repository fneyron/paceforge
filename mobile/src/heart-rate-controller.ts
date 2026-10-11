import { decodeHeartRate, HeartReading, HEART_RATE_MEASUREMENT, HEART_RATE_SERVICE, isBluetoothUuid } from './heart-rate';

export type Sensor = { id: string; name: string };
export type HeartState = {
  phase: 'idle' | 'preparing' | 'scanning' | 'connecting' | 'connected';
  sensors: Sensor[];
  sensor: Sensor | null;
  reading: HeartReading | null;
  message: string;
  error: string;
};
export type HeartEvents = {
  discovered: (sensor: Sensor) => void;
  scanStopped: () => void;
  disconnected: (id: string) => void;
  poweredOff: () => void;
  value: (id: string, service: string, characteristic: string, bytes: number[]) => void;
};
export type HeartTransport = {
  prepare: () => Promise<void>;
  scan: () => Promise<void>;
  stopScan: () => Promise<void>;
  connect: (id: string) => Promise<void>;
  subscribe: (id: string) => Promise<void>;
  disconnect: (id: string) => Promise<void>;
  listen: (events: HeartEvents) => () => void;
};

export const initialHeartState: HeartState = {
  phase: 'idle', sensors: [], sensor: null, reading: null,
  message: 'Porte ton brassard et garde-le près du téléphone.', error: '',
};

/** Owns only the connection selected by the user. No automatic pairing or writes. */
export class HeartRateController {
  state: HeartState = initialHeartState;
  private generation = 0;
  private disposed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private unlisten: () => void;

  constructor(private transport: HeartTransport, private changed: (state: HeartState) => void,
    private timeoutMs = 20000) {
    this.unlisten = transport.listen({
      discovered: sensor => {
        if (this.state.phase !== 'scanning') return;
        this.update({ sensors: [...this.state.sensors.filter(s => s.id !== sensor.id), sensor].slice(-30) });
      },
      scanStopped: () => {
        if (this.state.phase === 'scanning') this.update({ phase: 'idle', message: this.state.sensors.length
          ? 'Choisis ton capteur pour afficher le pouls.'
          : 'Aucun capteur cardiaque trouvé. Porte le brassard, rapproche-le et vérifie ses connexions.' });
      },
      disconnected: id => {
        if (id === this.state.sensor?.id) this.stop('Capteur déconnecté. Relance la recherche pour le retrouver.');
      },
      poweredOff: () => this.stop('Bluetooth indisponible. Active-le puis relance la recherche.'),
      value: (id, service, characteristic, bytes) => {
        if (this.state.phase !== 'connected' || id !== this.state.sensor?.id
          || !isBluetoothUuid(service, HEART_RATE_SERVICE)
          || !isBluetoothUuid(characteristic, HEART_RATE_MEASUREMENT)) return;
        const bpm = decodeHeartRate(bytes);
        this.update({ reading: bpm === null ? null : { bpm, at: Date.now() },
          message: bpm === null ? 'Mesure indisponible. Vérifie le contact du brassard avec la peau.' : 'Pouls reçu en direct.' });
      },
    });
  }

  private update(patch: Partial<HeartState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.changed(this.state);
  }
  private current(generation: number) { return !this.disposed && this.generation === generation; }
  private cancelTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }

  async scan() {
    if (this.disposed || this.state.phase !== 'idle') return;
    const generation = ++this.generation;
    this.update({ phase: 'preparing', sensors: [], sensor: null, reading: null, error: '', message: 'Préparation du Bluetooth…' });
    try {
      await this.transport.prepare();
      if (!this.current(generation)) return;
      this.update({ phase: 'scanning', message: 'Recherche des capteurs cardiaques à proximité…' });
      await this.transport.scan();
      if (!this.current(generation)) await this.transport.stopScan();
    } catch (e) {
      if (this.current(generation)) {
        this.stop('La recherche n’a pas abouti.');
        this.update({ error: e instanceof Error ? e.message : 'Impossible de rechercher les capteurs.' });
      }
    }
  }

  async connect(sensor: Sensor) {
    if (this.disposed || !['idle', 'scanning'].includes(this.state.phase)) return;
    const generation = ++this.generation;
    this.update({ phase: 'connecting', sensor, reading: null, error: '', message: `Connexion à ${sensor.name}…` });
    this.timer = setTimeout(() => {
      if (this.current(generation)) this.stop('Connexion trop longue. Vérifie que le capteur est disponible, puis réessaie.');
    }, this.timeoutMs);
    try {
      await this.transport.stopScan();
      if (!this.current(generation)) return;
      await this.transport.connect(sensor.id);
      if (!this.current(generation)) { this.releaseOld(sensor.id); return; }
      await this.transport.subscribe(sensor.id);
      if (!this.current(generation)) { this.releaseOld(sensor.id); return; }
      this.cancelTimer();
      this.update({ phase: 'connected', message: 'Connecté. En attente de la première mesure…' });
    } catch {
      if (this.current(generation)) this.stop('Connexion impossible. Rapproche le capteur et libère une connexion Bluetooth si nécessaire.');
    }
  }

  private releaseOld(id: string) {
    // A new user-selected connection to the same device owns its current link.
    if (this.disposed || this.state.sensor?.id !== id) void this.transport.disconnect(id).catch(() => {});
  }

  stop(message = 'Lecture arrêtée. Tu peux reconnecter un capteur.') {
    const id = this.state.sensor?.id;
    ++this.generation;
    this.cancelTimer();
    this.update({ phase: 'idle', sensor: null, reading: null, message });
    void this.transport.stopScan().catch(() => {});
    if (id) void this.transport.disconnect(id).catch(() => {});
  }

  dispose() {
    this.stop();
    this.disposed = true;
    this.unlisten();
  }
}
