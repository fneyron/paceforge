import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { stravaTokens, athletes, stravaActivities } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { getStravaAccessToken } from "@/lib/strava/client";
import { eq } from "drizzle-orm";

export async function POST() {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    // Try to revoke the token on Strava's side
    const accessToken = await getStravaAccessToken(userId);
    if (accessToken) {
      await fetch("https://www.strava.com/oauth/deauthorize", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `access_token=${accessToken}`,
      }).catch(() => {
        // Best effort — don't block on Strava API failure
      });
    }

    // Delete Strava tokens
    await db.delete(stravaTokens).where(eq(stravaTokens.userId, userId));

    // Clear Strava-estimated values from athlete
    await db
      .update(athletes)
      .set({
        stravaFtp: null,
        stravaVdot: null,
        stravaCss: null,
        stravaCda: null,
        updatedAt: new Date(),
      })
      .where(eq(athletes.userId, userId));

    // Delete cached Strava activities
    await db.delete(stravaActivities).where(eq(stravaActivities.userId, userId));

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Strava disconnect error:", error);
    return NextResponse.json({ error: "Failed to disconnect" }, { status: 500 });
  }
}
