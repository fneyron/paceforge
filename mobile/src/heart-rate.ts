// Bluetooth SIG Heart Rate Service (0x180D), Heart Rate Measurement (0x2A37).
export const HEART_RATE_SERVICE = '180d';
export const HEART_RATE_MEASUREMENT = '2a37';
export const LIVE_TIMEOUT_MS = 15000;

export function isBluetoothUuid(value: string, short: string): boolean {
  const uuid = value.toLowerCase();
  return uuid === short || uuid === `0000${short}` || uuid === `0000${short}-0000-1000-8000-00805f9b34fb`;
}

/** A spot measurement, never a daily average or an overnight recovery input. */
export function decodeHeartRate(bytes: readonly number[]): number | null {
  if (bytes.length < 2 || bytes.some(b => !Number.isInteger(b) || b < 0 || b > 255)) return null;
  const flags = bytes[0];
  if (flags & 0xe0) return null; // Reserved flags: do not guess another protocol.
  let offset = flags & 1 ? 3 : 2;
  if (bytes.length < offset) return null;
  const bpm = flags & 1 ? bytes[1] + 256 * bytes[2] : bytes[1];
  if (flags & 8) offset += 2; // Energy Expended, uint16.
  if (bytes.length < offset) return null;
  if (flags & 16) {
    if (bytes.length < offset + 2 || (bytes.length - offset) % 2) return null;
  } else if (bytes.length !== offset) return null;
  if ((flags & 4) && !(flags & 2)) return null; // Contact supported, but sensor off skin.
  return bpm >= 25 && bpm <= 250 ? bpm : null;
}

export type HeartReading = { bpm: number; at: number };
export function liveReading(reading: HeartReading | null, now: number): HeartReading | null {
  return reading && now >= reading.at && now - reading.at < LIVE_TIMEOUT_MS ? reading : null;
}
