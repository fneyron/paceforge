import type { WeatherCondition } from "@/types/route";

/**
 * Weather physics model for simulation integration.
 */

/**
 * Compute air density from temperature, pressure, and humidity.
 * Uses August-Roche-Magnus formula for saturation vapor pressure.
 *
 * ρ = (P_d / (R_d × T)) + (P_v / (R_v × T))
 *
 * Where:
 *   P_d = dry air partial pressure
 *   P_v = water vapor partial pressure
 *   R_d = 287.058 J/(kg·K) (dry air gas constant)
 *   R_v = 461.495 J/(kg·K) (water vapor gas constant)
 */
export function airDensityFromWeather(
  temperature: number, // °C
  pressure: number, // hPa
  humidity: number // 0-100%
): number {
  const T = temperature + 273.15; // Kelvin
  const P = pressure * 100; // Pa

  // Saturation vapor pressure (August-Roche-Magnus)
  const Es = 610.94 * Math.exp((17.625 * temperature) / (temperature + 243.04));
  const Pv = (humidity / 100) * Es;
  const Pd = P - Pv;

  const Rd = 287.058;
  const Rv = 461.495;

  return Pd / (Rd * T) + Pv / (Rv * T);
}

/**
 * Decompose wind into headwind and crosswind components.
 *
 * @param windSpeed - Wind speed in m/s
 * @param windDirection - Wind direction in degrees (meteorological: direction FROM)
 * @param segmentBearing - Segment travel direction in degrees (0 = north)
 * @returns { headwind, crosswind } in m/s (headwind positive = against travel)
 */
export function decomposeWind(
  windSpeed: number,
  windDirection: number,
  segmentBearing: number
): { headwind: number; crosswind: number } {
  // Wind direction is "from", segment bearing is "to"
  // Relative angle: wind coming FROM windDirection, rider going TO segmentBearing
  const relativeAngle = ((windDirection - segmentBearing + 360) % 360) * (Math.PI / 180);

  return {
    headwind: windSpeed * Math.cos(relativeAngle),
    crosswind: windSpeed * Math.sin(relativeAngle),
  };
}

/**
 * Compute yaw angle from crosswind and rider speed.
 * Yaw affects the effective CdA.
 *
 * yaw = atan2(crosswind, riderSpeed + headwind)
 */
export function computeYawAngle(
  crosswind: number,
  riderSpeed: number,
  headwind: number
): number {
  const apparentHeadwind = riderSpeed + headwind;
  return Math.atan2(Math.abs(crosswind), apparentHeadwind) * (180 / Math.PI);
}

/**
 * Adjust CdA based on yaw angle.
 * CdA increases with yaw due to increased frontal area.
 *
 * CdA(yaw) = CdA_base × (1 + 0.004 × |yaw°|)
 */
export function adjustCdaForYaw(baseCda: number, yawDegrees: number): number {
  return baseCda * (1 + 0.004 * Math.abs(yawDegrees));
}

/**
 * Thermal correction factor for running (Ely et al., 2007).
 * Performance degrades progressively above 15°C.
 */
export function runningThermalFactor(wx: WeatherCondition): number {
  if (wx.temperature <= 15) return 1.0;

  let degradation = (wx.temperature - 15) * 0.003;
  if (wx.humidity > 50) {
    degradation *= 1 + (wx.humidity - 50) / 200;
  }

  return Math.max(0.85, 1 - degradation);
}
