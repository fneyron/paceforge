import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { fetchWeatherForRoute } from "@/lib/weather/client";
import type { RoutePoint } from "@/types/route";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;

    const route = await db
      .select()
      .from(routes)
      .where(eq(routes.id, routeId))
      .get();

    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    const points: RoutePoint[] = JSON.parse(route.points);

    // Use route's race date or default to tomorrow
    const raceDate =
      route.raceDate || new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    const raceStartTime = route.raceStartTime || "08:00";

    const conditions = await fetchWeatherForRoute(
      points,
      raceDate,
      raceStartTime
    );

    return NextResponse.json({ conditions });
  } catch (error) {
    console.error("Weather fetch error:", error);
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to fetch weather",
      },
      { status: 500 }
    );
  }
}
