import type { NutritionStrategy, Waypoint } from "@/types/route";

export interface NutritionItem {
  time: number; // seconds from start
  distance: number; // meters
  productId: string;
  productName: string;
  type: "gel" | "bar" | "drink" | "chew" | "real_food" | "custom";
  calories: number;
  carbs: number;
  sodium: number;
  caffeine: number;
  fluid: number; // ml
}

interface PlanInput {
  strategy: NutritionStrategy;
  totalTime: number; // seconds
  totalDistance: number; // meters
  aidStations: Waypoint[]; // waypoints of type "aid_station"
  products: Array<{
    id: string;
    name: string;
    type: "gel" | "bar" | "drink" | "chew" | "real_food" | "custom";
    calories: number;
    carbs: number;
    sodium: number;
    caffeine: number;
    fluidMl: number;
  }>;
}

/**
 * Generate a nutrition plan aligned to timing and aid stations.
 *
 * Strategy: distribute products evenly to meet carbs/h target,
 * aligning with aid stations when possible.
 */
export function generateNutritionPlan(input: PlanInput): NutritionItem[] {
  const { strategy, totalTime, totalDistance, aidStations, products } = input;
  const items: NutritionItem[] = [];

  if (products.length === 0) return items;

  const totalHours = totalTime / 3600;
  const intervalSeconds = 1800; // Target every 30 minutes

  // Separate portable products from aid-station-only products
  const portableProducts = products.filter(
    (p) => p.type === "gel" || p.type === "chew" || p.type === "bar"
  );
  const drinkProducts = products.filter((p) => p.type === "drink");
  const defaultProduct = portableProducts[0] || products[0];
  const defaultDrink = drinkProducts[0];

  // Caffeine tracking
  let totalCaffeine = 0;
  const caffeineStartTime = strategy.caffeineStrategy
    ? strategy.caffeineStrategy.startAfterHours * 3600
    : Infinity;

  // Generate intake points
  for (
    let timeS = intervalSeconds;
    timeS < totalTime;
    timeS += intervalSeconds
  ) {
    const distance = (timeS / totalTime) * totalDistance;
    const hours = timeS / 3600;

    // Find nearest aid station (within 2km)
    const nearAid = aidStations.find(
      (ws) => Math.abs(ws.distance - distance) < 2000
    );

    // Solid nutrition (gel/bar)
    const targetCarbsPerIntake = (strategy.carbsPerHour * intervalSeconds) / 3600;
    const product = defaultProduct;

    if (product) {
      const servings = Math.max(1, Math.round(targetCarbsPerIntake / product.carbs));

      // Caffeine logic
      let caffeineAmount = 0;
      if (
        strategy.caffeineStrategy &&
        timeS >= caffeineStartTime &&
        totalCaffeine < (strategy.caffeineStrategy.maxTotal || 400)
      ) {
        caffeineAmount = Math.min(
          product.caffeine * servings,
          strategy.caffeineStrategy.dosePerIntake
        );
        totalCaffeine += caffeineAmount;
      }

      items.push({
        time: timeS,
        distance,
        productId: product.id,
        productName: product.name,
        type: product.type,
        calories: product.calories * servings,
        carbs: product.carbs * servings,
        sodium: product.sodium * servings,
        caffeine: caffeineAmount,
        fluid: 0,
      });
    }

    // Fluid at aid stations or every 30 min
    if (defaultDrink && (nearAid || timeS % 1800 === 0)) {
      const targetFluid = (strategy.fluidPerHour * intervalSeconds) / 3600;
      const drinkServings = Math.max(
        1,
        Math.round(targetFluid / (defaultDrink.fluidMl || 250))
      );

      items.push({
        time: timeS,
        distance,
        productId: defaultDrink.id,
        productName: defaultDrink.name,
        type: "drink",
        calories: defaultDrink.calories * drinkServings,
        carbs: defaultDrink.carbs * drinkServings,
        sodium: defaultDrink.sodium * drinkServings,
        caffeine: 0,
        fluid: (defaultDrink.fluidMl || 250) * drinkServings,
      });
    }
  }

  return items;
}

/**
 * Compute totals from a nutrition plan.
 */
export function computeNutritionTotals(items: NutritionItem[]) {
  return {
    totalCalories: items.reduce((s, i) => s + i.calories, 0),
    totalCarbs: items.reduce((s, i) => s + i.carbs, 0),
    totalSodium: items.reduce((s, i) => s + i.sodium, 0),
    totalCaffeine: items.reduce((s, i) => s + i.caffeine, 0),
    totalFluid: items.reduce((s, i) => s + i.fluid, 0),
  };
}
