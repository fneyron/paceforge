"use client";

import type { NutritionItem } from "@/lib/nutrition/planner";
import { formatTime } from "@/lib/physics/simulate";

interface Props {
  items: NutritionItem[];
  totalTime: number;
}

export function NutritionTimeline({ items, totalTime }: Props) {
  if (items.length === 0) return null;

  const totalCarbs = items.reduce((s, i) => s + i.carbs, 0);
  const totalCalories = items.reduce((s, i) => s + i.calories, 0);
  const totalFluid = items.reduce((s, i) => s + i.fluid, 0);
  const totalSodium = items.reduce((s, i) => s + i.sodium, 0);
  const totalCaffeine = items.reduce((s, i) => s + i.caffeine, 0);
  const hours = totalTime / 3600;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Nutrition Plan</h3>

      {/* Totals */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="border rounded p-2 text-center">
          <p className="text-muted-foreground">Carbs</p>
          <p className="font-medium">{Math.round(totalCarbs)}g</p>
          <p className="text-muted-foreground">
            {Math.round(totalCarbs / hours)}g/h
          </p>
        </div>
        <div className="border rounded p-2 text-center">
          <p className="text-muted-foreground">Calories</p>
          <p className="font-medium">{Math.round(totalCalories)}</p>
        </div>
        <div className="border rounded p-2 text-center">
          <p className="text-muted-foreground">Fluid</p>
          <p className="font-medium">{Math.round(totalFluid)}ml</p>
        </div>
      </div>

      {totalSodium > 0 && (
        <p className="text-xs text-muted-foreground">
          Sodium: {Math.round(totalSodium)}mg | Caffeine:{" "}
          {Math.round(totalCaffeine)}mg
        </p>
      )}

      {/* Timeline */}
      <div className="space-y-1 max-h-64 overflow-y-auto">
        {items.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-2 text-xs border-b py-1 last:border-b-0"
          >
            <span className="text-muted-foreground w-16 shrink-0">
              {formatTime(item.time)}
            </span>
            <span className="w-14 shrink-0 text-muted-foreground">
              {(item.distance / 1000).toFixed(0)}km
            </span>
            <span className="flex-1 truncate">{item.productName}</span>
            <span className="text-muted-foreground">{item.carbs}g</span>
          </div>
        ))}
      </div>
    </div>
  );
}
