import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, segments as segmentsTable, simulations } from "@/lib/db/schema";
import { eq, asc } from "drizzle-orm";
import { nanoid } from "nanoid";
import { simulate } from "@/lib/physics/simulate";
import type { SportType, CyclingConfig, TrailConfig, FatigueConfig } from "@/types/route";
import type { PacingStrategy } from "@/types/pacing";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;
    const body = await request.json();

    const { sport, config, fatigueConfig, pacingStrategy, name } = body as {
      sport: SportType;
      config: CyclingConfig | TrailConfig;
      fatigueConfig?: FatigueConfig;
      pacingStrategy?: PacingStrategy;
      name?: string;
    };

    // Fetch route and segments
    const route = await db.select().from(routes).where(eq(routes.id, routeId)).get();
    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    const segments = await db
      .select()
      .from(segmentsTable)
      .where(eq(segmentsTable.routeId, routeId))
      .orderBy(asc(segmentsTable.orderIndex));

    const points = JSON.parse(route.points);

    // Run simulation
    const results = simulate({
      sport,
      segments,
      points,
      config,
      fatigueConfig,
      pacingStrategy,
    });

    // Save to DB
    const simId = nanoid();
    const simName = name || `${sport} - ${new Date().toLocaleDateString()}`;
    const configWithPacing = pacingStrategy
      ? { ...config, pacingStrategy }
      : config;

    await db.insert(simulations).values({
      id: simId,
      routeId,
      name: simName,
      sport,
      config: JSON.stringify(configWithPacing),
      fatigueConfig: JSON.stringify(fatigueConfig || {}),
      results: JSON.stringify(results),
      totalTime: results.totalTime,
    });

    return NextResponse.json({
      id: simId,
      name: simName,
      ...results,
    });
  } catch (error) {
    console.error("Simulation error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Simulation failed" },
      { status: 500 }
    );
  }
}
