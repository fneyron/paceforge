import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { stravaActivities, athletes } from "@/lib/db/schema";
import { eq, desc } from "drizzle-orm";
import { computePMC } from "@/lib/training/pmc";
import { computeTSS } from "@/lib/training/tss";
import { getSessionUserId } from "@/lib/auth/session";

export async function GET() {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    // Get athlete metrics for TSS computation
    const [athlete] = await db
      .select()
      .from(athletes)
      .where(eq(athletes.userId, userId))
      .limit(1);

    // Get all activities
    const activities = await db
      .select()
      .from(stravaActivities)
      .where(eq(stravaActivities.userId, userId))
      .orderBy(desc(stravaActivities.startDate));

    // Compute TSS for each activity
    const activitiesWithTSS: { date: string; tss: number }[] = [];
    for (const activity of activities) {
      // Use stored TSS if available
      if (activity.tss && activity.tss > 0) {
        activitiesWithTSS.push({
          date: new Date(activity.startDate).toISOString(),
          tss: activity.tss,
        });
        continue;
      }

      // Otherwise compute from activity data and athlete metrics
      const result = computeTSS(
        {
          sport: activity.sport,
          movingTime: activity.movingTime,
          normalizedPower: activity.normalizedPower,
          averagePower: activity.averagePower,
          averageSpeed: activity.averageSpeed,
          averageHeartRate: activity.averageHeartRate,
          distance: activity.distance,
        },
        {
          ftp: athlete?.ftp ?? athlete?.stravaFtp,
          vdot: athlete?.vdot ?? athlete?.stravaVdot,
          css: athlete?.css ?? athlete?.stravaCss,
          fcMax: athlete?.fcMax,
          lactateThreshold: athlete?.lactateThreshold,
        }
      );

      if (result) {
        activitiesWithTSS.push({
          date: new Date(activity.startDate).toISOString(),
          tss: result.tss,
        });
      }
    }

    const pmcData = computePMC(activitiesWithTSS);
    return NextResponse.json(pmcData);
  } catch (error) {
    console.error("PMC error:", error);
    return NextResponse.json(
      { error: "Failed to compute PMC" },
      { status: 500 }
    );
  }
}
