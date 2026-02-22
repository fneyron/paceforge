import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, segments as segmentsTable } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { nanoid } from "nanoid";
import { processDrawnRoute } from "@/lib/gpx/from-points";

export async function POST(request: NextRequest) {
  try {
    const userId = await getSessionUserId();
    const body = await request.json();

    const { points, sport, name } = body as {
      points: { lat: number; lon: number }[];
      sport: string;
      name: string;
    };

    if (!points || points.length < 2) {
      return NextResponse.json({ error: "Need at least 2 points" }, { status: 400 });
    }

    const result = await processDrawnRoute(points, name || "Drawn Route");
    const routeId = nanoid();

    // Save route
    await db.insert(routes).values({
      id: routeId,
      userId,
      name: name || "Drawn Route",
      sport: (sport || "cycling") as "cycling" | "gravel" | "trail" | "ultra_trail" | "road_running" | "swimming" | "triathlon",
      gpxRaw: result.gpxString,
      geojson: JSON.stringify(result.geojson),
      points: JSON.stringify(result.points),
      totalDistance: result.stats.totalDistance,
      elevationGain: result.stats.elevationGain,
      elevationLoss: result.stats.elevationLoss,
      minElevation: result.stats.minElevation,
      maxElevation: result.stats.maxElevation,
    });

    // Save segments
    for (let i = 0; i < result.segments.length; i++) {
      const seg = result.segments[i];
      await db.insert(segmentsTable).values({
        id: nanoid(),
        routeId,
        type: seg.type,
        orderIndex: i,
        startDistance: seg.startDistance,
        endDistance: seg.endDistance,
        startIndex: seg.startIndex,
        endIndex: seg.endIndex,
        elevationGain: seg.elevationGain,
        elevationLoss: seg.elevationLoss,
        averageGrade: seg.averageGrade,
        maxGrade: seg.maxGrade,
        length: seg.length,
      });
    }

    return NextResponse.json({
      id: routeId,
      name: name || "Drawn Route",
      stats: result.stats,
    });
  } catch (error) {
    console.error("Draw route error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to create route" },
      { status: 500 }
    );
  }
}
