/**
 * CdA estimation from power and speed data.
 * Uses least-squares regression on flat segments.
 */

interface DataPoint {
  power: number; // watts
  speed: number; // m/s
  grade: number; // fraction
  altitude: number; // meters
}

const G = 9.81;

/**
 * Air density at altitude.
 */
function airDensity(altitude: number): number {
  return 1.225 * Math.exp(-altitude / 8500);
}

/**
 * Estimate CdA from power/speed data points on flat terrain.
 *
 * On flat (grade ≈ 0):
 *   P × η = m·g·Crr·v + 0.5·ρ·CdA·v³
 *
 *   Rearranging: (P×η/v - m·g·Crr) = 0.5·ρ·CdA·v²
 *
 *   So: CdA = 2·(P×η/v - m·g·Crr) / (ρ·v²)
 *
 * We use median of computed CdA values for robustness.
 */
export function estimateCdaFromData(
  dataPoints: DataPoint[],
  totalMass: number,
  crr: number = 0.005,
  efficiency: number = 0.25
): { cda: number; confidence: number; sampleCount: number } | null {
  // Filter to flat segments with valid data
  const flat = dataPoints.filter(
    (d) =>
      Math.abs(d.grade) < 0.01 &&
      d.power > 80 &&
      d.speed > 5 && // > 18 km/h
      d.speed < 20 // < 72 km/h
  );

  if (flat.length < 10) return null;

  const cdaValues: number[] = [];

  for (const d of flat) {
    const rho = airDensity(d.altitude);
    const cda =
      (2 * (d.power * efficiency / d.speed - totalMass * G * crr)) /
      (rho * d.speed * d.speed);

    if (cda > 0.15 && cda < 0.6) {
      cdaValues.push(cda);
    }
  }

  if (cdaValues.length < 5) return null;

  // Median
  cdaValues.sort((a, b) => a - b);
  const mid = Math.floor(cdaValues.length / 2);
  const median =
    cdaValues.length % 2 === 0
      ? (cdaValues[mid - 1] + cdaValues[mid]) / 2
      : cdaValues[mid];

  // Confidence based on sample count and variance
  const mean = cdaValues.reduce((s, v) => s + v, 0) / cdaValues.length;
  const variance =
    cdaValues.reduce((s, v) => s + (v - mean) ** 2, 0) / cdaValues.length;
  const cv = Math.sqrt(variance) / mean; // coefficient of variation
  const confidence = Math.max(0, Math.min(1, 1 - cv * 2));

  return {
    cda: Math.round(median * 1000) / 1000,
    confidence: Math.round(confidence * 100) / 100,
    sampleCount: cdaValues.length,
  };
}
