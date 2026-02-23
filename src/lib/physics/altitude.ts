/**
 * Altitude performance model (Peronnet-Thibault 1991).
 * VO2max decreases ~6.5% per 1000m above 1500m altitude.
 */
export function altitudePerformanceFactor(altitudeM: number): number {
  if (altitudeM <= 1500) return 1.0;
  return Math.max(0.5, 1.0 - 0.065 * ((altitudeM - 1500) / 1000));
}
