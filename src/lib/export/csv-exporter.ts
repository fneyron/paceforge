import type { SplitResult } from "@/types/route";
import type { NutritionItem } from "@/lib/nutrition/planner";
import { formatTime } from "@/lib/physics/simulate";

/**
 * Generate CSV of simulation splits.
 */
export function generateSplitsCSV(
  splits: SplitResult[],
  routeName: string
): string {
  const headers = [
    "Segment",
    "Distance (km)",
    "Elevation Gain (m)",
    "Elevation Loss (m)",
    "Time",
    "Cumulative Time",
    "Speed (km/h)",
    "Pace (min/km)",
    "Power (W)",
  ];

  const rows: string[][] = [];
  let cumulativeTime = 0;

  for (let i = 0; i < splits.length; i++) {
    const s = splits[i];
    cumulativeTime += s.time;

    rows.push([
      String(i + 1),
      (s.distance / 1000).toFixed(2),
      s.elevationGain.toFixed(0),
      s.elevationLoss.toFixed(0),
      formatTime(s.time),
      formatTime(cumulativeTime),
      (s.speed * 3.6).toFixed(1),
      s.pace.toFixed(2),
      s.power ? s.power.toFixed(0) : "",
    ]);
  }

  return [
    `# PaceForge - ${routeName}`,
    headers.join(","),
    ...rows.map((r) => r.join(",")),
  ].join("\n");
}

/**
 * Generate CSV of nutrition plan.
 */
export function generateNutritionCSV(
  items: NutritionItem[],
  routeName: string
): string {
  const headers = [
    "Time",
    "Distance (km)",
    "Product",
    "Type",
    "Calories",
    "Carbs (g)",
    "Sodium (mg)",
    "Caffeine (mg)",
    "Fluid (ml)",
  ];

  const rows = items.map((item) => [
    formatTime(item.time),
    (item.distance / 1000).toFixed(1),
    item.productName,
    item.type,
    item.calories.toFixed(0),
    item.carbs.toFixed(0),
    item.sodium.toFixed(0),
    item.caffeine.toFixed(0),
    item.fluid.toFixed(0),
  ]);

  return [
    `# PaceForge Nutrition Plan - ${routeName}`,
    headers.join(","),
    ...rows.map((r) => r.join(",")),
  ].join("\n");
}
