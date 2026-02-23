/**
 * Heat stress model based on WBGT (Wet Bulb Globe Temperature).
 * Approximation from Liljegren et al. (2008).
 */

/**
 * Compute WBGT from temperature and humidity.
 * Uses simplified Bernard approximation for outdoor conditions.
 * @param tempC - Air temperature in °C
 * @param humidityPct - Relative humidity 0-100%
 * @param solarW - Optional solar radiation in W/m² (default 500 for sunny)
 * @returns WBGT in °C
 */
export function computeWBGT(
  tempC: number,
  humidityPct: number,
  solarW: number = 500
): number {
  // Vapor pressure (hPa) via August-Roche-Magnus
  const e = (humidityPct / 100) * 6.105 * Math.exp((17.27 * tempC) / (237.7 + tempC));

  // Simplified WBGT approximation (Bernard 1999)
  // WBGT_outdoor ≈ 0.567*Ta + 0.393*e + 3.94 + solarContribution
  const solarContrib = 0.0045 * solarW; // ~2.25°C contribution at 500 W/m²
  return 0.567 * tempC + 0.393 * e + 3.94 + solarContrib;
}

/**
 * Performance degradation factor based on WBGT.
 * Based on Ely et al. (2007) extended model.
 * @param wbgt - WBGT in °C
 * @param acclimatization - Heat acclimatization level 0-1 (0=none, 1=fully acclimatized)
 * @returns Factor 0-1 to multiply performance by
 */
export function wbgtPerformanceFactor(
  wbgt: number,
  acclimatization: number = 0
): number {
  const acclim = Math.max(0, Math.min(1, acclimatization));

  if (wbgt <= 18) return 1.0;

  // 18-28°C: ~1% degradation per degree
  if (wbgt <= 28) {
    const degradation = 0.01 * (wbgt - 18) * (1 - 0.3 * acclim);
    return Math.max(0.5, 1.0 - degradation);
  }

  // >28°C: accelerated degradation (~2% per degree)
  const baseDeg = 0.01 * 10 * (1 - 0.3 * acclim); // 18-28 component
  const severeDeg = 0.02 * (wbgt - 28) * (1 - 0.3 * acclim);
  return Math.max(0.5, 1.0 - baseDeg - severeDeg);
}

/**
 * Convenience: compute performance factor directly from weather.
 */
export function heatPerformanceFactor(
  tempC: number,
  humidityPct: number,
  acclimatization: number = 0,
  solarW?: number
): number {
  const wbgt = computeWBGT(tempC, humidityPct, solarW);
  return wbgtPerformanceFactor(wbgt, acclimatization);
}
