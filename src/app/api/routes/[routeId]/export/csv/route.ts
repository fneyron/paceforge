import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, simulations } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { generateSplitsCSV } from "@/lib/export/csv-exporter";

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

  if (!simulationId) {
    return NextResponse.json(
      { error: "simulationId is required for CSV export" },
      { status: 400 }
    );
  }

  const sim = await db
    .select()
    .from(simulations)
    .where(eq(simulations.id, simulationId))
    .limit(1);

  if (sim.length === 0) {
    return NextResponse.json(
      { error: "Simulation not found" },
      { status: 404 }
    );
  }

  const results = JSON.parse(sim[0].results);
  const csv = generateSplitsCSV(results.splits, route[0].name);

  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv",
      "Content-Disposition": `attachment; filename="${route[0].name.replace(/[^a-zA-Z0-9]/g, "_")}_splits.csv"`,
    },
  });
}
