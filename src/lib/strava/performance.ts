import { db } from "@/lib/db";
import { stravaActivities } from "@/lib/db/schema/strava-activities";
import { eq, and, desc, or, gte } from "drizzle-orm";
import { fetchActivityStreams } from "./streams";
import { stravaFetch } from "./client";

interface StravaAthlete {
  id: number;
  ftp: number | null;
  weight: number | null;
}

/**
 * Estimate FTP from Strava.
 * 1. First tries to get FTP directly from the Strava athlete profile
 * 2. Falls back to estimating from activities (95% of best 20-min power)
 */
export async function estimateFTP(userId: string): Promise<number | null> {
  // Try to get FTP directly from Strava athlete profile
  const athlete = await stravaFetch<StravaAthlete>(userId, "/athlete");
  if (athlete?.ftp && athlete.ftp > 0) {
    return Math.round(athlete.ftp);
  }

  // Fallback: estimate from activities
  const rides = await db
    .select()
    .from(stravaActivities)
    .where(
      and(
        eq(stravaActivities.userId, userId),
        or(
          eq(stravaActivities.sport, "Ride"),
          eq(stravaActivities.sport, "GravelRide"),
          eq(stravaActivities.sport, "VirtualRide")
        )
      )
    )
    .orderBy(desc(stravaActivities.startDate))
    .limit(50);

  let bestAvg20 = 0;

  for (const ride of rides) {
    if (!ride.averagePower || ride.movingTime < 1200) continue;

    // Try to get power stream for more accurate 20min best
    const streams = await fetchActivityStreams(
      userId,
      ride.stravaActivityId
    );

    if (streams?.watts && streams.watts.length > 0) {
      const windowSize = 1200; // 20 minutes in seconds (1 sample/sec)
      if (streams.watts.length >= windowSize) {
        let sum = 0;
        for (let i = 0; i < windowSize; i++) sum += streams.watts[i];
        let best = sum;
        for (let i = windowSize; i < streams.watts.length; i++) {
          sum += streams.watts[i] - streams.watts[i - windowSize];
          if (sum > best) best = sum;
        }
        const avg = best / windowSize;
        if (avg > bestAvg20) bestAvg20 = avg;
      }
    } else if (ride.normalizedPower && ride.normalizedPower > bestAvg20) {
      // Fallback: use normalized power as rough estimate
      bestAvg20 = ride.normalizedPower;
    }
  }

  return bestAvg20 > 0 ? Math.round(bestAvg20 * 0.95) : null;
}

/**
 * Estimate VDOT from Strava running activities.
 * Uses Jack Daniels formula — ROAD RUNS ONLY (no trail).
 * Takes median of top 5 VDOT values for realistic estimate.
 * Filters out runs with excessive elevation gain.
 */
export async function estimateVDOT(userId: string): Promise<number | null> {
  // Only last 12 months
  const twelveMonthsAgo = new Date();
  twelveMonthsAgo.setMonth(twelveMonthsAgo.getMonth() - 12);

  const runs = await db
    .select()
    .from(stravaActivities)
    .where(
      and(
        eq(stravaActivities.userId, userId),
        // Road runs only — TrailRun excluded (VDOT not valid with elevation)
        or(
          eq(stravaActivities.sport, "Run"),
          eq(stravaActivities.sport, "VirtualRun")
        ),
        gte(stravaActivities.startDate, twelveMonthsAgo)
      )
    )
    .orderBy(desc(stravaActivities.startDate))
    .limit(100);

  const vdotValues: number[] = [];

  for (const run of runs) {
    const distKm = run.distance / 1000;
    const timeMin = run.movingTime / 60;

    // Only consider runs of 5k+ for VDOT estimation
    if (distKm < 4.5) continue;

    // Filter out runs with excessive elevation (>15m per km = too hilly for VDOT)
    if (run.elevationGain && run.elevationGain / distKm > 15) continue;

    // Daniels VDOT approximation — velocity in m/min
    const v = run.distance / timeMin;

    // VO2 from velocity: VO2 = -4.6 + 0.182258v + 0.000104v²
    const vo2 = -4.6 + 0.182258 * v + 0.000104 * v * v;

    // Percent VO2max from time: %VO2max = 0.8 + 0.1894393·e^(-0.012778·t) + 0.2989558·e^(-0.1932605·t)
    const pctVO2max =
      0.8 +
      0.1894393 * Math.exp(-0.012778 * timeMin) +
      0.2989558 * Math.exp(-0.1932605 * timeMin);

    const vdot = vo2 / pctVO2max;

    if (vdot > 20 && vdot < 85) {
      vdotValues.push(vdot);
    }
  }

  if (vdotValues.length === 0) return null;

  // Sort descending and take median of top 5 for a realistic but fair estimate
  vdotValues.sort((a, b) => b - a);
  const top = vdotValues.slice(0, Math.min(5, vdotValues.length));
  const median = top[Math.floor(top.length / 2)];

  return Math.round(median * 10) / 10;
}

/**
 * Estimate CSS (Critical Swim Speed) from Strava swim activities.
 * CSS = best pace per 100m with 6% safety margin.
 * Uses median of top 3 best paces for realistic estimate.
 */
export async function estimateCSS(userId: string): Promise<number | null> {
  const swims = await db
    .select()
    .from(stravaActivities)
    .where(
      and(
        eq(stravaActivities.userId, userId),
        eq(stravaActivities.sport, "Swim")
      )
    )
    .orderBy(desc(stravaActivities.startDate))
    .limit(50);

  const paces: number[] = [];

  for (const swim of swims) {
    if (swim.distance < 200) continue;
    const pace100m = (swim.movingTime / swim.distance) * 100; // sec/100m
    paces.push(pace100m);
  }

  if (paces.length === 0) return null;

  // Sort ascending (fastest first) and take median of top 3
  paces.sort((a, b) => a - b);
  const top = paces.slice(0, Math.min(3, paces.length));
  const medianPace = top[Math.floor(top.length / 2)];

  // CSS is roughly 6% slower than best pace
  return Math.round((medianPace * 1.06) * 10) / 10;
}

/**
 * Estimate CdA from Strava cycling activities on flat segments.
 * CdA = (P/v - m·g·Crr) / (0.5·ρ·v²) on flat segments (|grade| < 1%)
 */
export async function estimateCdA(
  userId: string,
  weight: number = 75,
  bikeWeight: number = 8,
  crr: number = 0.005
): Promise<number | null> {
  const rides = await db
    .select()
    .from(stravaActivities)
    .where(
      and(
        eq(stravaActivities.userId, userId),
        or(
          eq(stravaActivities.sport, "Ride"),
          eq(stravaActivities.sport, "GravelRide"),
          eq(stravaActivities.sport, "VirtualRide")
        )
      )
    )
    .orderBy(desc(stravaActivities.startDate))
    .limit(20);

  const cdaValues: number[] = [];
  const totalMass = weight + bikeWeight;
  const g = 9.81;
  const rho = 1.225; // air density at sea level

  for (const ride of rides) {
    if (!ride.averagePower || ride.averagePower < 100) continue;

    const streams = await fetchActivityStreams(
      userId,
      ride.stravaActivityId
    );

    if (!streams?.watts || !streams?.velocity_smooth || !streams?.grade_smooth)
      continue;

    // Find flat segments
    for (let i = 0; i < streams.watts.length; i++) {
      const grade = streams.grade_smooth[i] ?? 0;
      const power = streams.watts[i];
      const speed = streams.velocity_smooth[i];

      if (
        Math.abs(grade) < 0.01 &&
        power > 80 &&
        speed > 5 && // > 18 km/h
        speed < 20 // < 72 km/h
      ) {
        const cda =
          (power / speed - totalMass * g * crr) / (0.5 * rho * speed * speed);
        if (cda > 0.15 && cda < 0.6) {
          cdaValues.push(cda);
        }
      }
    }
  }

  if (cdaValues.length < 10) return null;

  // Median CdA
  cdaValues.sort((a, b) => a - b);
  const mid = Math.floor(cdaValues.length / 2);
  const median =
    cdaValues.length % 2 === 0
      ? (cdaValues[mid - 1] + cdaValues[mid]) / 2
      : cdaValues[mid];

  return Math.round(median * 1000) / 1000;
}
