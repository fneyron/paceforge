import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import { routes, segments as segmentsTable, stravaActivities } from "@/lib/db/schema";
import { nanoid } from "nanoid";
import { eq, and } from "drizzle-orm";
import { fetchActivityStreams } from "@/lib/strava/streams";
import { streamsToRoutePoints, streamsToGPX, mapStravaSport } from "@/lib/strava/convert";
import { fetchElevation, smoothElevation, detectSegments, analyzeRoute } from "@/lib/gpx";

export async function POST(request: NextRequest) {
  try {
    const session = await auth();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await request.json();
    const { activityId, sport: sportOverride, name: nameOverride } = body as {
      activityId: string;
      sport?: string;
      name?: string;
    };

    if (!activityId) {
      return NextResponse.json({ error: "activityId is required" }, { status: 400 });
    }

    // Get activity metadata for name and sport
    const activity = await db
      .select()
      .from(stravaActivities)
      .where(
        and(
          eq(stravaActivities.stravaActivityId, activityId),
          eq(stravaActivities.userId, session.user.id)
        )
      )
      .get();

    const activityName = nameOverride || activity?.name || "Strava Import";
    const activitySport = sportOverride || (activity ? mapStravaSport(activity.sport) : "cycling");

    // Fetch streams
    const streams = await fetchActivityStreams(session.user.id, activityId);
    if (!streams || !streams.latlng?.length) {
      return NextResponse.json({ error: "Activity has no GPS data" }, { status: 400 });
    }

    // Convert to RoutePoints
    let routePoints = streamsToRoutePoints(streams);

    // Generate GPX
    const gpxString = streamsToGPX(streams, activityName);

    // Pipeline: elevation -> smooth -> segments -> stats
    routePoints = await fetchElevation(routePoints);
    routePoints = smoothElevation(routePoints);
    const routeSegments = detectSegments(routePoints);
    const stats = analyzeRoute(routePoints);

    // Build GeoJSON
    const geojson = {
      type: "FeatureCollection" as const,
      features: [
        {
          type: "Feature" as const,
          properties: {},
          geometry: {
            type: "LineString" as const,
            coordinates: routePoints.map((p) => [p.lon, p.lat, p.ele]),
          },
        },
      ],
    };

    // Save to DB
    const routeId = nanoid();
    await db.insert(routes).values({
      id: routeId,
      userId: session.user.id,
      name: activityName,
      sport: activitySport as "cycling" | "gravel" | "trail" | "ultra_trail" | "road_running" | "swimming" | "triathlon",
      gpxRaw: gpxString,
      geojson: JSON.stringify(geojson),
      points: JSON.stringify(routePoints),
      totalDistance: stats.totalDistance,
      elevationGain: stats.elevationGain,
      elevationLoss: stats.elevationLoss,
      minElevation: stats.minElevation,
      maxElevation: stats.maxElevation,
    });

    for (let i = 0; i < routeSegments.length; i++) {
      const seg = routeSegments[i];
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
      name: activityName,
      stats,
    });
  } catch (error) {
    console.error("Strava import error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Import failed" },
      { status: 500 }
    );
  }
}
