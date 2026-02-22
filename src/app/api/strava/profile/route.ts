import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import { users, athletes } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { estimateFTP, estimateVDOT, estimateCSS, estimateCdA } from "@/lib/strava/performance";
import { stravaFetch } from "@/lib/strava/client";

interface StravaAthleteProfile {
  id: number;
  firstname: string;
  lastname: string;
  profile: string;
  profile_medium: string;
  weight: number | null;
  ftp: number | null;
}

export async function GET() {
  try {
    const session = await auth();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = session.user.id;

    // Fetch user info
    const user = await db
      .select()
      .from(users)
      .where(eq(users.id, userId))
      .get();

    if (!user) {
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }

    // Fetch athlete profile directly from Strava API (for FTP, weight, name, avatar)
    const stravaProfile = await stravaFetch<StravaAthleteProfile>(userId, "/athlete");

    // Update user display name and avatar from Strava profile
    if (stravaProfile) {
      const displayName = `${stravaProfile.firstname} ${stravaProfile.lastname}`.trim() || user.displayName;
      const avatarUrl = stravaProfile.profile_medium || stravaProfile.profile || user.avatarUrl;

      if (displayName !== user.displayName || avatarUrl !== user.avatarUrl) {
        await db
          .update(users)
          .set({ displayName, avatarUrl, updatedAt: new Date() })
          .where(eq(users.id, userId));
      }
    }

    // Estimate performance metrics from Strava data (each can fail independently)
    const [ftp, vdot, css, cda] = await Promise.all([
      estimateFTP(userId).catch((e) => { console.error("estimateFTP error:", e); return null; }),
      estimateVDOT(userId).catch((e) => { console.error("estimateVDOT error:", e); return null; }),
      estimateCSS(userId).catch((e) => { console.error("estimateCSS error:", e); return null; }),
      estimateCdA(userId).catch((e) => { console.error("estimateCdA error:", e); return null; }),
    ]);

    // Persist Strava estimates to athlete table for pre-filling simulation configs
    const athleteId = "default";
    const existing = await db
      .select()
      .from(athletes)
      .where(eq(athletes.id, athleteId))
      .get();

    if (!existing) {
      await db.insert(athletes).values({ id: athleteId, name: "Athlete" });
    }

    // Build update payload — include Strava profile data (name, weight) if athlete has no manual values
    const updatePayload: Record<string, unknown> = {
      stravaFtp: ftp,
      stravaVdot: vdot,
      stravaCss: css,
      stravaCda: cda,
      lastStravaSync: new Date(),
      updatedAt: new Date(),
    };

    // If athlete name is still default, set it from Strava
    if (stravaProfile && existing?.name === "Athlete") {
      updatePayload.name = `${stravaProfile.firstname} ${stravaProfile.lastname}`.trim();
    }

    // If weight not set manually, use Strava weight
    if (stravaProfile?.weight && (!existing?.weight || existing.weight === 75)) {
      updatePayload.weight = stravaProfile.weight;
    }

    await db
      .update(athletes)
      .set(updatePayload)
      .where(eq(athletes.id, athleteId));

    return NextResponse.json({
      user: {
        id: user.id,
        displayName: stravaProfile
          ? `${stravaProfile.firstname} ${stravaProfile.lastname}`.trim()
          : user.displayName,
        avatarUrl: stravaProfile?.profile_medium || user.avatarUrl,
        stravaId: user.stravaId,
      },
      estimates: {
        ftp,
        vdot,
        css,
        cda,
      },
    });
  } catch (error) {
    console.error("Error fetching Strava profile:", error);
    return NextResponse.json({ error: "Failed to fetch profile" }, { status: 500 });
  }
}
