import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { bluetoothTransport } from './bluetooth';
import { HeartRateController, initialHeartState } from './heart-rate-controller';
import { liveReading } from './heart-rate';

export function useHeartRate() {
  const [state, setState] = useState(initialHeartState);
  const [now, setNow] = useState(() => Date.now());
  const controller = useRef<HeartRateController | null>(null);
  useEffect(() => {
    const instance = new HeartRateController(bluetoothTransport(), setState);
    controller.current = instance;
    const appState = AppState.addEventListener('change', next => {
      if (next === 'background' && instance.state.phase !== 'idle') {
        instance.stop('Lecture interrompue lorsque l’application a été quittée. Relance la recherche pour reconnecter ton capteur.');
      }
    });
    return () => { controller.current = null; appState.remove(); instance.dispose(); };
  }, []);
  useEffect(() => {
    if (state.phase !== 'connected') return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);
  // New notifications should render immediately even between freshness ticks.
  const reading = liveReading(state.reading, Math.max(now, state.reading?.at || 0));
  return { state, reading, controller };
}
