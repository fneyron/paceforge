import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { simulations } from "@/lib/db/schema";
import { eq, desc } from "drizzle-orm";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;

    const sims = await db
      .select({
        id: simulations.id,
        name: simulations.name,
        sport: simulations.sport,
        totalTime: simulations.totalTime,
        createdAt: simulations.createdAt,
      })
      .from(simulations)
      .where(eq(simulations.routeId, routeId))
      .orderBy(desc(simulations.createdAt));

    return NextResponse.json(sims);
  } catch (error) {
    console.error("Error fetching simulations:", error);
    return NextResponse.json({ error: "Failed to fetch simulations" }, { status: 500 });
  }
}
