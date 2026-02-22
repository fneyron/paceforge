import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { routes, waypoints, nutritionProducts, nutritionPlans } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { nanoid } from "nanoid";
import {
  generateNutritionPlan,
  computeNutritionTotals,
} from "@/lib/nutrition/planner";
import type { NutritionStrategy, Waypoint } from "@/types/route";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ routeId: string }> }
) {
  try {
    const { routeId } = await params;
    const body = await request.json();

    const { strategy, simulationId, totalTime } = body as {
      strategy: NutritionStrategy;
      simulationId: string;
      totalTime: number; // seconds
    };

    // Fetch route
    const route = await db
      .select()
      .from(routes)
      .where(eq(routes.id, routeId))
      .get();

    if (!route) {
      return NextResponse.json({ error: "Route not found" }, { status: 404 });
    }

    // Fetch aid station waypoints
    const allWaypoints = await db
      .select()
      .from(waypoints)
      .where(eq(waypoints.routeId, routeId));

    const aidStations = allWaypoints.filter(
      (wp) => wp.type === "aid_station"
    ) as unknown as Waypoint[];

    // Fetch user's nutrition products (or defaults)
    const products = await db
      .select()
      .from(nutritionProducts)
      .where(eq(nutritionProducts.isDefault, true));

    // Also get user's custom products
    const userProducts = route.userId
      ? await db
          .select()
          .from(nutritionProducts)
          .where(eq(nutritionProducts.userId, route.userId))
      : [];

    const allProducts = [...products, ...userProducts].map((p) => ({
      id: p.id,
      name: p.name,
      type: p.type as "gel" | "bar" | "drink" | "chew" | "real_food" | "custom",
      calories: p.calories,
      carbs: p.carbs,
      sodium: p.sodium ?? 0,
      caffeine: p.caffeine ?? 0,
      fluidMl: p.fluidMl ?? 0,
    }));

    if (allProducts.length === 0) {
      return NextResponse.json(
        { error: "No nutrition products configured. Add products first." },
        { status: 400 }
      );
    }

    const totalDistance = route.totalDistance ?? 0;

    // Generate plan
    const items = generateNutritionPlan({
      strategy,
      totalTime,
      totalDistance,
      aidStations,
      products: allProducts,
    });

    const totals = computeNutritionTotals(items);

    // Save to DB
    const planId = nanoid();
    await db.insert(nutritionPlans).values({
      id: planId,
      simulationId,
      strategy: JSON.stringify(strategy),
      items: JSON.stringify(items),
      totalCalories: totals.totalCalories,
      totalCarbs: totals.totalCarbs,
      totalSodium: totals.totalSodium,
      totalCaffeine: totals.totalCaffeine,
      totalFluid: totals.totalFluid,
    });

    return NextResponse.json({
      id: planId,
      items,
      totals,
    });
  } catch (error) {
    console.error("Nutrition plan error:", error);
    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "Failed to generate nutrition plan",
      },
      { status: 500 }
    );
  }
}
