import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { lactateTests, athletes } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { nanoid } from "nanoid";
import { eq, desc } from "drizzle-orm";

export async function GET() {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json([], { status: 200 });
    }

    // Get athlete ID
    const [athlete] = await db
      .select({ id: athletes.id })
      .from(athletes)
      .where(eq(athletes.userId, userId))
      .limit(1);

    if (!athlete) {
      return NextResponse.json([]);
    }

    const tests = await db
      .select()
      .from(lactateTests)
      .where(eq(lactateTests.athleteId, athlete.id))
      .orderBy(desc(lactateTests.testDate));

    return NextResponse.json(
      tests.map((t) => ({
        ...t,
        steps: JSON.parse(t.steps),
      }))
    );
  } catch (error) {
    console.error("Error fetching lactate tests:", error);
    return NextResponse.json({ error: "Failed to fetch tests" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    const body = await request.json();
    const { protocol, stepDuration, startValue, increment, steps, lt1, lt2 } = body;

    // Get athlete
    const [athlete] = await db
      .select({ id: athletes.id })
      .from(athletes)
      .where(eq(athletes.userId, userId))
      .limit(1);

    if (!athlete) {
      return NextResponse.json({ error: "No athlete profile" }, { status: 404 });
    }

    const id = nanoid();

    await db.insert(lactateTests).values({
      id,
      athleteId: athlete.id,
      testDate: new Date().toISOString().split("T")[0],
      protocol,
      stepDuration,
      startValue,
      increment,
      steps: JSON.stringify(steps),
      lt1Speed: lt1?.speed ?? null,
      lt1Lactate: lt1?.lactate ?? null,
      lt1HR: lt1?.hr ?? null,
      lt2Speed: lt2?.speed ?? null,
      lt2Lactate: lt2?.lactate ?? null,
      lt2HR: lt2?.hr ?? null,
    });

    return NextResponse.json({ id });
  } catch (error) {
    console.error("Error creating lactate test:", error);
    return NextResponse.json({ error: "Failed to create test" }, { status: 500 });
  }
}
