import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { simulations } from "@/lib/db/schema";
import { eq, and } from "drizzle-orm";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ routeId: string; simId: string }> }
) {
  try {
    const { routeId, simId } = await params;

    const sim = await db
      .select()
      .from(simulations)
      .where(and(eq(simulations.id, simId), eq(simulations.routeId, routeId)))
      .get();

    if (!sim) {
      return NextResponse.json({ error: "Simulation not found" }, { status: 404 });
    }

    return NextResponse.json({
      ...sim,
      config: JSON.parse(sim.config),
      fatigueConfig: JSON.parse(sim.fatigueConfig),
      results: JSON.parse(sim.results),
      weatherConfig: sim.weatherConfig ? JSON.parse(sim.weatherConfig) : null,
    });
  } catch (error) {
    console.error("Error fetching simulation:", error);
    return NextResponse.json({ error: "Failed to fetch simulation" }, { status: 500 });
  }
}
