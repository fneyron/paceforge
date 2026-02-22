import { db } from "@/lib/db";
import { stravaTokens } from "@/lib/db/schema";
import { eq } from "drizzle-orm";

const STRAVA_API_BASE = "https://www.strava.com/api/v3";
const TOKEN_REFRESH_URL = "https://www.strava.com/oauth/token";
const REFRESH_BUFFER_SECONDS = 300; // 5 minutes

/**
 * Get a valid Strava access token for a user, refreshing if needed.
 */
export async function getStravaAccessToken(
  userId: string
): Promise<string | null> {
  const tokens = await db
    .select()
    .from(stravaTokens)
    .where(eq(stravaTokens.userId, userId))
    .limit(1);

  if (tokens.length === 0) return null;

  const token = tokens[0];
  const now = Math.floor(Date.now() / 1000);

  // Token still valid
  if (token.expiresAt > now + REFRESH_BUFFER_SECONDS) {
    return token.accessToken;
  }

  // Refresh token
  const res = await fetch(TOKEN_REFRESH_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: process.env.STRAVA_CLIENT_ID,
      client_secret: process.env.STRAVA_CLIENT_SECRET,
      grant_type: "refresh_token",
      refresh_token: token.refreshToken,
    }),
  });

  if (!res.ok) {
    console.error("Strava token refresh failed:", await res.text());
    return null;
  }

  const data = await res.json();

  await db
    .update(stravaTokens)
    .set({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      expiresAt: data.expires_at,
    })
    .where(eq(stravaTokens.userId, userId));

  return data.access_token as string;
}

/**
 * Make an authenticated request to the Strava API.
 */
export async function stravaFetch<T = unknown>(
  userId: string,
  endpoint: string,
  params?: Record<string, string | number>
): Promise<T | null> {
  const accessToken = await getStravaAccessToken(userId);
  if (!accessToken) return null;

  const url = new URL(`${STRAVA_API_BASE}${endpoint}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }
  }

  const res = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  if (!res.ok) {
    console.error(`Strava API error ${res.status}:`, await res.text());
    return null;
  }

  return res.json() as Promise<T>;
}
