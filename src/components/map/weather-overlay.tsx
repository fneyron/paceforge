"use client";

import type { WeatherCondition } from "@/types/route";

interface Props {
  conditions: WeatherCondition[];
}

/**
 * Weather overlay legend component.
 * Displays wind direction/speed color coding for the route.
 * The actual map layer integration would be in route-map.tsx.
 */
export function WeatherOverlayLegend({ conditions }: Props) {
  if (conditions.length === 0) return null;

  const maxWind = Math.max(...conditions.map((c) => c.windSpeed));

  return (
    <div className="absolute bottom-4 right-4 bg-background/90 backdrop-blur-sm border rounded-md p-2 text-xs z-10">
      <p className="font-medium mb-1">Wind</p>
      <div className="flex items-center gap-1">
        <div className="w-3 h-3 rounded-full bg-green-500" />
        <span>Tailwind</span>
      </div>
      <div className="flex items-center gap-1">
        <div className="w-3 h-3 rounded-full bg-yellow-500" />
        <span>Crosswind</span>
      </div>
      <div className="flex items-center gap-1">
        <div className="w-3 h-3 rounded-full bg-red-500" />
        <span>Headwind</span>
      </div>
      <p className="text-muted-foreground mt-1">
        Max: {maxWind.toFixed(1)} m/s
      </p>
    </div>
  );
}
