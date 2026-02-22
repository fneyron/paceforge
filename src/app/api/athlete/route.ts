import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { athletes } from "@/lib/db/schema";
import { eq } from "drizzle-orm";

const DEFAULT_ATHLETE_ID = "default";

export async function GET() {
  try {
    let athlete = await db
      .select()
      .from(athletes)
      .where(eq(athletes.id, DEFAULT_ATHLETE_ID))
      .get();

    if (!athlete) {
      // Create default athlete
      await db.insert(athletes).values({
        id: DEFAULT_ATHLETE_ID,
        name: "Athlete",
      });
      athlete = await db
        .select()
        .from(athletes)
        .where(eq(athletes.id, DEFAULT_ATHLETE_ID))
        .get();
    }

    // Return athlete with effective values (manual > strava > null)
    // Don't invent defaults — only use actual data for predictions
    const effective = {
      ...athlete,
      ftp: athlete!.ftp ?? athlete!.stravaFtp ?? null,
      vdot: athlete!.vdot ?? athlete!.stravaVdot ?? null,
      css: athlete!.css ?? athlete!.stravaCss ?? null,
      cda: athlete!.cda ?? athlete!.stravaCda ?? null,
    };

    return NextResponse.json(effective);
  } catch (error) {
    console.error("Error fetching athlete:", error);
    return NextResponse.json(
      { error: "Failed to fetch athlete" },
      { status: 500 }
    );
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();

    // Ensure default athlete exists
    const existing = await db
      .select()
      .from(athletes)
      .where(eq(athletes.id, DEFAULT_ATHLETE_ID))
      .get();

    if (!existing) {
      await db.insert(athletes).values({
        id: DEFAULT_ATHLETE_ID,
        name: "Athlete",
      });
    }

    await db
      .update(athletes)
      .set({
        name: body.name,
        weight: body.weight,
        ftp: body.ftp,
        bikeWeight: body.bikeWeight,
        cda: body.cda,
        crr: body.crr,
        efficiency: body.efficiency,
        vma: body.vma,
        vo2max: body.vo2max,
        fcMax: body.fcMax,
        lactateThreshold: body.lactateThreshold,
        updatedAt: new Date(),
      })
      .where(eq(athletes.id, DEFAULT_ATHLETE_ID));

    const updated = await db
      .select()
      .from(athletes)
      .where(eq(athletes.id, DEFAULT_ATHLETE_ID))
      .get();

    return NextResponse.json(updated);
  } catch (error) {
    console.error("Error updating athlete:", error);
    return NextResponse.json(
      { error: "Failed to update athlete" },
      { status: 500 }
    );
  }
}
