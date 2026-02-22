import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { shares, routes, simulations } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { nanoid } from "nanoid";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { routeId, simulationId } = body;

    if (!routeId) {
      return NextResponse.json(
        { error: "Route ID required" },
        { status: 400 }
      );
    }

    // Verify route exists
    const route = await db
      .select()
      .from(routes)
      .where(eq(routes.id, routeId))
      .get();
    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    const token = nanoid(12);
    const id = nanoid();

    await db.insert(shares).values({
      id,
      token,
      routeId,
      simulationId: simulationId || null,
    });

    return NextResponse.json({ token, url: `/share/${token}` });
  } catch (error) {
    console.error("Error creating share:", error);
    return NextResponse.json(
      { error: "Failed to create share link" },
      { status: 500 }
    );
  }
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const token = searchParams.get("token");

    if (!token) {
      return NextResponse.json(
        { error: "Token required" },
        { status: 400 }
      );
    }

    const share = await db
      .select()
      .from(shares)
      .where(eq(shares.token, token))
      .get();

    if (!share) {
      return NextResponse.json(
        { error: "Share not found" },
        { status: 404 }
      );
    }

    // Check expiry
    if (share.expiresAt && share.expiresAt < new Date()) {
      return NextResponse.json({ error: "Share link expired" }, { status: 410 });
    }

    // Fetch route
    const route = await db
      .select()
      .from(routes)
      .where(eq(routes.id, share.routeId))
      .get();

    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    // Fetch simulation if linked
    let simulation = null;
    if (share.simulationId) {
      const sim = await db
        .select()
        .from(simulations)
        .where(eq(simulations.id, share.simulationId))
        .get();
      if (sim) {
        simulation = {
          ...sim,
          config: JSON.parse(sim.config),
          fatigueConfig: JSON.parse(sim.fatigueConfig),
          results: JSON.parse(sim.results),
        };
      }
    }

    return NextResponse.json({
      route: {
        ...route,
        geojson: JSON.parse(route.geojson),
        points: JSON.parse(route.points),
      },
      simulation,
    });
  } catch (error) {
    console.error("Error fetching share:", error);
    return NextResponse.json(
      { error: "Failed to fetch shared data" },
      { status: 500 }
    );
  }
}
