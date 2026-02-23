import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { athletes } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { eq } from "drizzle-orm";
import { runningPredictions, cyclingPredictions, swimmingPredictions } from "@/lib/predictions";
import { multiModelRunningPredictions, multiModelCyclingPredictions } from "@/lib/predictions/multi-model";
import type { MultiModelPrediction } from "@/types/route";

export async function GET() {
  try {
    const userId = await getSessionUserId();
    if (!userId) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    const [athlete] = await db
      .select()
      .from(athletes)
      .where(eq(athletes.userId, userId))
      .limit(1);

    if (!athlete) {
      return NextResponse.json({ error: "No athlete profile" }, { status: 404 });
    }

    const result: {
      running: ReturnType<typeof runningPredictions>;
      cycling: ReturnType<typeof cyclingPredictions>;
      swimming: ReturnType<typeof swimmingPredictions>;
      multiModelRunning: MultiModelPrediction[];
      multiModelCycling: MultiModelPrediction[];
    } = {
      running: [],
      cycling: [],
      swimming: [],
      multiModelRunning: [],
      multiModelCycling: [],
    };

    const effectiveVdot = athlete.vdot ?? athlete.stravaVdot;
    if (effectiveVdot && effectiveVdot > 0) {
      result.running = runningPredictions(effectiveVdot);

      // Multi-model running predictions
      const refRace = athlete.referenceRaceDistance && athlete.referenceRaceTime
        ? { distanceM: athlete.referenceRaceDistance, timeS: athlete.referenceRaceTime }
        : undefined;
      result.multiModelRunning = multiModelRunningPredictions(effectiveVdot, refRace);
    }

    const effectiveFtp = athlete.ftp ?? athlete.stravaFtp;
    if (effectiveFtp && effectiveFtp > 0) {
      const cyclingInput = {
        ftp: effectiveFtp,
        weight: athlete.weight || 75,
        bikeWeight: athlete.bikeWeight || 8,
        cda: athlete.cda ?? athlete.stravaCda ?? 0.32,
        crr: athlete.crr || 0.005,
        efficiency: athlete.efficiency || 0.25,
      };
      result.cycling = cyclingPredictions(cyclingInput);

      // Multi-model cycling predictions
      result.multiModelCycling = multiModelCyclingPredictions(
        cyclingInput,
        athlete.cp ?? undefined,
        athlete.wPrime ?? undefined
      );
    }

    const effectiveCss = athlete.css ?? athlete.stravaCss;
    if (effectiveCss && effectiveCss > 0) {
      result.swimming = swimmingPredictions(effectiveCss);
    }

    return NextResponse.json(result);
  } catch (error) {
    console.error("Predictions error:", error);
    return NextResponse.json({ error: "Failed to compute predictions" }, { status: 500 });
  }
}
