"use client";

import type { WeatherCondition } from "@/types/route";

interface Props {
  conditions: WeatherCondition[];
}

function windDirectionLabel(degrees: number): string {
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(degrees / 45) % 8];
}

export function WeatherPanel({ conditions }: Props) {
  if (conditions.length === 0) return null;

  const avgTemp =
    conditions.reduce((s, c) => s + c.temperature, 0) / conditions.length;
  const avgWind =
    conditions.reduce((s, c) => s + c.windSpeed, 0) / conditions.length;
  const maxWind = Math.max(...conditions.map((c) => c.windSpeed));
  const totalPrecip = conditions.reduce((s, c) => s + c.precipitation, 0);

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Weather Summary</h3>

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div className="border rounded-md p-2">
          <p className="text-muted-foreground text-xs">Avg. Temperature</p>
          <p className="font-medium">{avgTemp.toFixed(1)}°C</p>
        </div>
        <div className="border rounded-md p-2">
          <p className="text-muted-foreground text-xs">Avg. Wind</p>
          <p className="font-medium">{avgWind.toFixed(1)} m/s</p>
        </div>
        <div className="border rounded-md p-2">
          <p className="text-muted-foreground text-xs">Max Wind</p>
          <p className="font-medium">{maxWind.toFixed(1)} m/s</p>
        </div>
        <div className="border rounded-md p-2">
          <p className="text-muted-foreground text-xs">Precipitation</p>
          <p className="font-medium">{totalPrecip.toFixed(1)} mm</p>
        </div>
      </div>

      <div className="text-xs space-y-1 max-h-48 overflow-y-auto">
        {conditions.map((wx, i) => (
          <div
            key={i}
            className="flex justify-between py-1 border-b last:border-b-0"
          >
            <span className="text-muted-foreground">
              {(wx.distance / 1000).toFixed(0)} km
            </span>
            <span>{wx.temperature.toFixed(0)}°C</span>
            <span>
              {wx.windSpeed.toFixed(1)} m/s {windDirectionLabel(wx.windDirection)}
            </span>
            <span>{wx.precipitation > 0 ? `${wx.precipitation.toFixed(1)}mm` : "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
