import { stravaFetch } from "./client";

/**
 * Fetch GPX data for a Strava route.
 */
export async function fetchStravaRouteGPX(
  userId: string,
  stravaRouteId: string
): Promise<string | null> {
  const accessToken = (await import("./client")).getStravaAccessToken(userId);
  const token = await accessToken;
  if (!token) return null;

  const res = await fetch(
    `https://www.strava.com/api/v3/routes/${stravaRouteId}/export_gpx`,
    { headers: { Authorization: `Bearer ${token}` } }
  );

  if (!res.ok) {
    console.error("Failed to fetch Strava route GPX:", await res.text());
    return null;
  }

  return res.text();
}

/**
 * List Strava routes for a user.
 */
export async function listStravaRoutes(
  userId: string
): Promise<
  Array<{ id: number; name: string; distance: number; elevation_gain: number }> | null
> {
  return stravaFetch(userId, "/athlete/routes", { per_page: 100 });
}
