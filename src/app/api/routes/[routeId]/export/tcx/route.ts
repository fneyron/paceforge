import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, simulations } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { generateTCX } from "@/lib/export/tcx-encoder";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  const { routeId } = await params;
  const simulationId = request.nextUrl.searchParams.get("simulationId");

  const route = await db
    .select()
    .from(routes)
    .where(eq(routes.id, routeId))
    .limit(1);

  if (route.length === 0) {
    return NextResponse.json({ error: "Route not found" }, { status: 404 });
  }

  const points = JSON.parse(route[0].points);
  let splits;

  if (simulationId) {
    const sim = await db
      .select()
      .from(simulations)
      .where(eq(simulations.id, simulationId))
      .limit(1);

    if (sim.length > 0) {
      const results = JSON.parse(sim[0].results);
      splits = results.splits;
    }
  }

  const tcx = generateTCX(route[0].name, points, splits, route[0].sport);

  return new NextResponse(tcx, {
    headers: {
      "Content-Type": "application/vnd.garmin.tcx+xml",
      "Content-Disposition": `attachment; filename="${route[0].name.replace(/[^a-zA-Z0-9]/g, "_")}.tcx"`,
    },
  });
}
