import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, segments as segmentsTable, waypoints as waypointsTable } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { eq, asc } from "drizzle-orm";
import { nanoid } from "nanoid";

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;
    const userId = await getSessionUserId();

    const route = await db.select().from(routes).where(eq(routes.id, routeId)).get();

    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    if (userId && route.userId && route.userId !== userId) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    const newRouteId = nanoid();

    // Copy route
    await db.insert(routes).values({
      id: newRouteId,
      userId: route.userId,
      name: `${route.name} (copy)`,
      sport: route.sport,
      gpxRaw: route.gpxRaw,
      geojson: route.geojson,
      points: route.points,
      raceDate: route.raceDate,
      raceStartTime: route.raceStartTime,
      triathlonLegs: route.triathlonLegs,
      totalDistance: route.totalDistance,
      elevationGain: route.elevationGain,
      elevationLoss: route.elevationLoss,
      minElevation: route.minElevation,
      maxElevation: route.maxElevation,
    });

    // Copy segments
    const segments = await db
      .select()
      .from(segmentsTable)
      .where(eq(segmentsTable.routeId, routeId))
      .orderBy(asc(segmentsTable.orderIndex));

    for (const seg of segments) {
      await db.insert(segmentsTable).values({
        id: nanoid(),
        routeId: newRouteId,
        type: seg.type,
        orderIndex: seg.orderIndex,
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

    // Copy waypoints
    const waypoints = await db
      .select()
      .from(waypointsTable)
      .where(eq(waypointsTable.routeId, routeId))
      .orderBy(asc(waypointsTable.orderIndex));

    for (const wp of waypoints) {
      await db.insert(waypointsTable).values({
        id: nanoid(),
        routeId: newRouteId,
        type: wp.type,
        name: wp.name,
        distance: wp.distance,
        lat: wp.lat,
        lon: wp.lon,
        ele: wp.ele,
        config: wp.config,
        orderIndex: wp.orderIndex,
      });
    }

    return NextResponse.json({
      id: newRouteId,
      name: `${route.name} (copy)`,
    });
  } catch (error) {
    console.error("Error duplicating route:", error);
    return NextResponse.json({ error: "Failed to duplicate route" }, { status: 500 });
  }
}
