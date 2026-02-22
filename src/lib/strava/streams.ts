import { stravaFetch } from "./client";

export interface ActivityStreams {
  time?: number[];
  distance?: number[];
  altitude?: number[];
  heartrate?: number[];
  watts?: number[];
  cadence?: number[];
  velocity_smooth?: number[];
  latlng?: [number, number][];
  grade_smooth?: number[];
}

/**
 * Fetch detailed streams for a specific Strava activity.
 */
export async function fetchActivityStreams(
  userId: string,
  activityId: string
): Promise<ActivityStreams | null> {
  const streamTypes = [
    "time",
    "distance",
    "altitude",
    "heartrate",
    "watts",
    "cadence",
    "velocity_smooth",
    "latlng",
    "grade_smooth",
  ];

  const data = await stravaFetch<
    Array<{ type: string; data: number[] | [number, number][] }>
  >(userId, `/activities/${activityId}/streams`, {
    keys: streamTypes.join(","),
    key_by_type: "true",
  });

  if (!data || !Array.isArray(data)) return null;

  const streams: ActivityStreams = {};
  for (const stream of data) {
    (streams as Record<string, unknown>)[stream.type] = stream.data;
  }

  return streams;
}
