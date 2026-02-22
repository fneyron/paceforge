import { db } from "@/lib/db";
import { stravaActivities } from "@/lib/db/schema/strava-activities";
import { athletes } from "@/lib/db/schema";
import { eq, and } from "drizzle-orm";
import { stravaFetch } from "./client";
import { nanoid } from "nanoid";

interface StravaActivity {
  id: number;
  name: string;
  sport_type: string;
  type: string;
  start_date: string;
  distance: number;
  moving_time: number;
  elapsed_time: number;
  total_elevation_gain: number;
  average_watts?: number;
  weighted_average_watts?: number;
  max_watts?: number;
  average_heartrate?: number;
  max_heartrate?: number;
  average_speed: number;
  max_speed: number;
  average_cadence?: number;
}

/**
 * Sync activities from Strava for a user.
 * Fetches incrementally from the last sync timestamp.
 */
export async function syncActivities(userId: string): Promise<number> {
  // Get last sync timestamp from athlete
  const athleteRows = await db
    .select()
    .from(athletes)
    .where(eq(athletes.userId, userId))
    .limit(1);

  const lastSync = athleteRows[0]?.lastStravaSync;
  const afterTimestamp = lastSync
    ? Math.floor(lastSync.getTime() / 1000)
    : undefined;

  let page = 1;
  let totalImported = 0;
  const perPage = 100;

  while (true) {
    const params: Record<string, string | number> = {
      page,
      per_page: perPage,
    };
    if (afterTimestamp) {
      params.after = afterTimestamp;
    }

    const activities = await stravaFetch<StravaActivity[]>(
      userId,
      "/athlete/activities",
      params
    );

    if (!activities || activities.length === 0) break;

    for (const act of activities) {
      const stravaId = String(act.id);

      // Check if already exists
      const existing = await db
        .select({ id: stravaActivities.id })
        .from(stravaActivities)
        .where(
          and(
            eq(stravaActivities.userId, userId),
            eq(stravaActivities.stravaActivityId, stravaId)
          )
        )
        .limit(1);

      if (existing.length > 0) continue;

      await db.insert(stravaActivities).values({
        id: nanoid(),
        userId,
        stravaActivityId: stravaId,
        sport: act.sport_type || act.type,
        name: act.name,
        startDate: new Date(act.start_date),
        distance: act.distance,
        movingTime: act.moving_time,
        elapsedTime: act.elapsed_time,
        elevationGain: act.total_elevation_gain,
        averagePower: act.average_watts ?? null,
        normalizedPower: act.weighted_average_watts ?? null,
        maxPower: act.max_watts ?? null,
        averageHeartRate: act.average_heartrate ?? null,
        maxHeartRate: act.max_heartrate ?? null,
        averageSpeed: act.average_speed,
        maxSpeed: act.max_speed,
        averageCadence: act.average_cadence ?? null,
        rawData: JSON.stringify(act),
      });
      totalImported++;
    }

    if (activities.length < perPage) break;
    page++;
  }

  // Update last sync timestamp
  if (athleteRows.length > 0) {
    await db
      .update(athletes)
      .set({ lastStravaSync: new Date() })
      .where(eq(athletes.userId, userId));
  }

  return totalImported;
}
