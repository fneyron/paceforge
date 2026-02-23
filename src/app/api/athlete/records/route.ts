import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { stravaActivities, personalRecords } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import {
  extractPRsFromActivities,
  bestPRs,
} from "@/lib/training/personal-records";
import { getSessionUserId } from "@/lib/auth/session";
import { nanoid } from "nanoid";

export async function GET() {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json(
        { error: "Not authenticated" },
        { status: 401 }
      );
    }

    // Try stored records first
    const storedRecords = await db
      .select()
      .from(personalRecords)
      .where(eq(personalRecords.userId, userId));

    if (storedRecords.length > 0) {
      return NextResponse.json(
        storedRecords.map((r) => ({
          sport: r.sport,
          category: r.category,
          value: r.value,
          activityId: r.activityId,
          date: r.date,
        }))
      );
    }

    // Compute from activities
    const activities = await db
      .select()
      .from(stravaActivities)
      .where(eq(stravaActivities.userId, userId));

    const allPRs = extractPRsFromActivities(
      activities.map((a) => ({
        id: a.id,
        sport: a.sport,
        distance: a.distance,
        movingTime: a.movingTime,
        averagePower: a.averagePower,
        normalizedPower: a.normalizedPower,
        startDate: new Date(a.startDate),
      }))
    );

    const best = bestPRs(allPRs);

    // Store for future lookups
    for (const pr of best) {
      await db
        .insert(personalRecords)
        .values({
          id: nanoid(),
          userId,
          sport: pr.sport,
          category: pr.category,
          value: pr.value,
          activityId: pr.activityId,
          date: pr.date,
        })
        .onConflictDoNothing();
    }

    return NextResponse.json(best);
  } catch (error) {
    console.error("Records error:", error);
    return NextResponse.json(
      { error: "Failed to compute records" },
      { status: 500 }
    );
  }
}
