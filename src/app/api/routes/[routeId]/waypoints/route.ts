import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { waypoints } from "@/lib/db/schema";
import { eq, asc } from "drizzle-orm";
import { nanoid } from "nanoid";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;

    const wps = await db
      .select()
      .from(waypoints)
      .where(eq(waypoints.routeId, routeId))
      .orderBy(asc(waypoints.distance));

    return NextResponse.json(
      wps.map((wp) => ({
        ...wp,
        config: JSON.parse(wp.config),
      }))
    );
  } catch (error) {
    console.error("Error fetching waypoints:", error);
    return NextResponse.json(
      { error: "Failed to fetch waypoints" },
      { status: 500 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;
    const body = await request.json();

    const id = nanoid();

    await db.insert(waypoints).values({
      id,
      routeId,
      type: body.type,
      name: body.name,
      distance: body.distance,
      lat: body.lat,
      lon: body.lon,
      ele: body.ele,
      config: JSON.stringify(body.config || {}),
      orderIndex: body.orderIndex || 0,
    });

    return NextResponse.json({
      id,
      routeId,
      type: body.type,
      name: body.name,
      distance: body.distance,
      lat: body.lat,
      lon: body.lon,
      ele: body.ele,
      config: body.config || {},
    });
  } catch (error) {
    console.error("Error creating waypoint:", error);
    return NextResponse.json(
      { error: "Failed to create waypoint" },
      { status: 500 }
    );
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();

    if (!body.id) {
      return NextResponse.json(
        { error: "Waypoint ID required" },
        { status: 400 }
      );
    }

    await db
      .update(waypoints)
      .set({
        type: body.type,
        name: body.name,
        distance: body.distance,
        lat: body.lat,
        lon: body.lon,
        ele: body.ele,
        config: body.config ? JSON.stringify(body.config) : undefined,
      })
      .where(eq(waypoints.id, body.id));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error updating waypoint:", error);
    return NextResponse.json(
      { error: "Failed to update waypoint" },
      { status: 500 }
    );
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get("id");

    if (!id) {
      return NextResponse.json(
        { error: "Waypoint ID required" },
        { status: 400 }
      );
    }

    await db.delete(waypoints).where(eq(waypoints.id, id));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting waypoint:", error);
    return NextResponse.json(
      { error: "Failed to delete waypoint" },
      { status: 500 }
    );
  }
}
