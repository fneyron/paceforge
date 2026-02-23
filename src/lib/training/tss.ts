/**
 * Training Stress Score (TSS) calculations.
 * TSS quantifies the training load of a workout.
 */

/**
 * Cycling TSS from normalized power.
 * TSS = (duration_s × NP × IF) / (FTP × 3600) × 100
 * where IF = NP / FTP
 */
export function computeCyclingTSS(
  durationS: number,
  normalizedPower: number,
  ftp: number
): number {
  if (ftp <= 0 || durationS <= 0) return 0;
  const intensityFactor = normalizedPower / ftp;
  return (durationS * normalizedPower * intensityFactor) / (ftp * 3600) * 100;
}

/**
 * Running TSS (rTSS) from pace.
 * rTSS = (duration_s / 3600) × IF² × 100
 * where IF = threshold_pace / avg_pace (both in sec/km)
 * Note: threshold_pace < avg_pace for IF > 1
 */
export function computeRunningTSS(
  durationS: number,
  avgPaceSecPerKm: number,
  thresholdPaceSecPerKm: number
): number {
  if (thresholdPaceSecPerKm <= 0 || durationS <= 0 || avgPaceSecPerKm <= 0) return 0;
  const intensityFactor = thresholdPaceSecPerKm / avgPaceSecPerKm;
  return (durationS / 3600) * intensityFactor * intensityFactor * 100;
}

/**
 * Heart rate-based TSS (hrTSS).
 * hrTSS = (duration_s / 3600) × IF² × 100
 * where IF = avg_HR / LTHR
 */
export function computeHRBasedTSS(
  durationS: number,
  avgHeartRate: number,
  lactateThresholdHR: number
): number {
  if (lactateThresholdHR <= 0 || durationS <= 0 || avgHeartRate <= 0) return 0;
  const intensityFactor = avgHeartRate / lactateThresholdHR;
  return (durationS / 3600) * intensityFactor * intensityFactor * 100;
}

/**
 * Swimming TSS (sTSS).
 * sTSS = (duration_s / 3600) × IF³ × 100
 * where IF = CSS / avg_pace (both sec/100m)
 */
export function computeSwimmingTSS(
  durationS: number,
  avgPaceSec100m: number,
  css: number
): number {
  if (css <= 0 || durationS <= 0 || avgPaceSec100m <= 0) return 0;
  const intensityFactor = css / avgPaceSec100m;
  return (durationS / 3600) * Math.pow(intensityFactor, 3) * 100;
}

/**
 * Auto-detect sport and compute TSS from activity data.
 */
export function computeTSS(activity: {
  sport: string;
  movingTime: number; // seconds
  normalizedPower?: number | null;
  averagePower?: number | null;
  averageSpeed?: number | null; // m/s
  averageHeartRate?: number | null;
  distance?: number; // meters
}, athleteMetrics: {
  ftp?: number | null;
  vdot?: number | null;
  css?: number | null;
  fcMax?: number | null;
  lactateThreshold?: number | null;
}): { tss: number; intensityFactor: number } | null {
  const duration = activity.movingTime;

  // Cycling: prefer normalized power, fallback to average power
  if (activity.sport === "Ride" || activity.sport === "VirtualRide") {
    const power = activity.normalizedPower || activity.averagePower;
    if (power && athleteMetrics.ftp && athleteMetrics.ftp > 0) {
      const tss = computeCyclingTSS(duration, power, athleteMetrics.ftp);
      return { tss, intensityFactor: power / athleteMetrics.ftp };
    }
  }

  // Running: use pace
  if (activity.sport === "Run" || activity.sport === "TrailRun") {
    if (activity.averageSpeed && activity.averageSpeed > 0 && athleteMetrics.vdot) {
      const avgPaceSecKm = 1000 / activity.averageSpeed;
      // Threshold pace from VDOT (approx: VDOT 50 ≈ 4:30/km threshold)
      // Using Daniels: threshold speed ≈ 88% of vVO2max
      const vdot = athleteMetrics.vdot;
      const vo2 = vdot;
      const vVO2maxMPerMin = (-0.182258 + Math.sqrt(0.182258 ** 2 + 4 * 0.000104 * (vo2 + 4.6))) / (2 * 0.000104);
      const thresholdSpeedMPerS = (vVO2maxMPerMin * 0.88) / 60;
      const thresholdPaceSecKm = 1000 / thresholdSpeedMPerS;
      const tss = computeRunningTSS(duration, avgPaceSecKm, thresholdPaceSecKm);
      return { tss, intensityFactor: thresholdPaceSecKm / avgPaceSecKm };
    }
  }

  // Swimming: use pace
  if (activity.sport === "Swim") {
    if (activity.averageSpeed && activity.averageSpeed > 0 && athleteMetrics.css) {
      const avgPace100m = 100 / activity.averageSpeed;
      const tss = computeSwimmingTSS(duration, avgPace100m, athleteMetrics.css);
      return { tss, intensityFactor: athleteMetrics.css / avgPace100m };
    }
  }

  // Fallback: HR-based
  if (activity.averageHeartRate && athleteMetrics.fcMax) {
    const lthr = athleteMetrics.lactateThreshold || athleteMetrics.fcMax * 0.85;
    const tss = computeHRBasedTSS(duration, activity.averageHeartRate, lthr);
    return { tss, intensityFactor: activity.averageHeartRate / lthr };
  }

  return null;
}
