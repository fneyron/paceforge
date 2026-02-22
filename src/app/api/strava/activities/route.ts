import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import { stravaActivities } from "@/lib/db/schema/strava-activities";
import { eq, desc } from "drizzle-orm";

export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = request.nextUrl;
  const sport = searchParams.get("sport");
  const limit = parseInt(searchParams.get("limit") || "50", 10);

  let query = db
    .select()
    .from(stravaActivities)
    .where(eq(stravaActivities.userId, session.user.id))
    .orderBy(desc(stravaActivities.startDate))
    .limit(limit);

  if (sport) {
    query = db
      .select()
      .from(stravaActivities)
      .where(eq(stravaActivities.userId, session.user.id))
      .orderBy(desc(stravaActivities.startDate))
      .limit(limit);
  }

  const activities = await query;

  return NextResponse.json(
    sport
      ? activities.filter((a) => a.sport.toLowerCase().includes(sport.toLowerCase()))
      : activities
  );
}
