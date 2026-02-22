import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, segments } from "@/lib/db/schema";
import { processGPX } from "@/lib/gpx";
import { getSessionUserId } from "@/lib/auth/session";
import { nanoid } from "nanoid";
import { desc, eq } from "drizzle-orm";

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const file = formData.get("gpx") as File | null;
    const sport = (formData.get("sport") as string) || "cycling";

    if (!file) {
      return NextResponse.json({ error: "No GPX file provided" }, { status: 400 });
    }

    const gpxString = await file.text();
    const userId = await getSessionUserId();

    // Process GPX through pipeline
    const result = await processGPX(gpxString);

    const routeId = nanoid();

    // Store route
    await db.insert(routes).values({
      id: routeId,
      userId,
      name: result.name,
      sport: sport as "cycling" | "trail" | "ultra_trail",
      gpxRaw: gpxString,
      geojson: JSON.stringify(result.geojson),
      points: JSON.stringify(result.points),
      totalDistance: result.stats.totalDistance,
      elevationGain: result.stats.elevationGain,
      elevationLoss: result.stats.elevationLoss,
      minElevation: result.stats.minElevation,
      maxElevation: result.stats.maxElevation,
    });

    // Store segments
    for (let i = 0; i < result.segments.length; i++) {
      const seg = result.segments[i];
      await db.insert(segments).values({
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
      name: result.name,
      stats: result.stats,
      segmentCount: result.segments.length,
    });
  } catch (error) {
    console.error("GPX processing error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to process GPX" },
      { status: 500 }
    );
  }
}

export async function GET() {
  try {
    const userId = await getSessionUserId();

    let query = db
      .select({
        id: routes.id,
        name: routes.name,
        sport: routes.sport,
        totalDistance: routes.totalDistance,
        elevationGain: routes.elevationGain,
        elevationLoss: routes.elevationLoss,
        createdAt: routes.createdAt,
        points: routes.points,
      })
      .from(routes);

    // Filter by user if authenticated
    const rawRoutes = userId
      ? await query.where(eq(routes.userId, userId)).orderBy(desc(routes.createdAt))
      : await query.orderBy(desc(routes.createdAt));

    // Downsample elevation to ~30 points for sparklines
    const allRoutes = rawRoutes.map(({ points: pointsJson, ...rest }) => {
      let elevationSample: number[] | undefined;
      try {
        const pts = JSON.parse(pointsJson as string) as Array<{ ele: number }>;
        if (pts.length > 0) {
          const step = Math.max(1, Math.floor(pts.length / 30));
          elevationSample = [];
          for (let i = 0; i < pts.length; i += step) {
            elevationSample.push(Math.round(pts[i].ele));
          }
          if (elevationSample.length > 0 && (pts.length - 1) % step !== 0) {
            elevationSample.push(Math.round(pts[pts.length - 1].ele));
          }
        }
      } catch {
        // skip if points can't be parsed
      }
      return { ...rest, elevationSample };
    });

    return NextResponse.json(allRoutes);
  } catch (error) {
    console.error("Error fetching routes:", error);
    return NextResponse.json({ error: "Failed to fetch routes" }, { status: 500 });
  }
}
