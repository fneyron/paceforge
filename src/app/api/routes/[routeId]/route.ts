import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, segments as segmentsTable } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { eq, asc } from "drizzle-orm";

export async function GET(
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

    // Check ownership if authenticated
    if (userId && route.userId && route.userId !== userId) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    const segments = await db
      .select()
      .from(segmentsTable)
      .where(eq(segmentsTable.routeId, routeId))
      .orderBy(asc(segmentsTable.orderIndex));

    return NextResponse.json({
      ...route,
      geojson: JSON.parse(route.geojson),
      points: JSON.parse(route.points),
      segments,
    });
  } catch (error) {
    console.error("Error fetching route:", error);
    return NextResponse.json({ error: "Failed to fetch route" }, { status: 500 });
  }
}

export async function PATCH(
  request: NextRequest,
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

    const body = await request.json();
    const allowedFields = ["name", "sport", "raceDate", "raceStartTime"] as const;
    const updates: Record<string, unknown> = { updatedAt: new Date() };

    for (const field of allowedFields) {
      if (field in body) {
        updates[field] = body[field];
      }
    }

    await db.update(routes).set(updates).where(eq(routes.id, routeId));

    return NextResponse.json({ success: true, ...updates });
  } catch (error) {
    console.error("Error updating route:", error);
    return NextResponse.json({ error: "Failed to update route" }, { status: 500 });
  }
}

export async function DELETE(
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

    // Check ownership if authenticated
    if (userId && route.userId && route.userId !== userId) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    await db.delete(routes).where(eq(routes.id, routeId));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting route:", error);
    return NextResponse.json({ error: "Failed to delete route" }, { status: 500 });
  }
}
