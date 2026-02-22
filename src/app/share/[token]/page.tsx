"use client";

import { useEffect, useState, use } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatTime } from "@/lib/physics/simulate";
import type { SimulationResult } from "@/types/route";

interface SharedData {
  route: {
    name: string;
    sport: string;
    totalDistance: number;
    elevationGain: number;
    elevationLoss: number;
    minElevation: number;
    maxElevation: number;
  };
  simulation: {
    results: SimulationResult;
    totalTime: number;
    sport: string;
  } | null;
}

export default function SharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [data, setData] = useState<SharedData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/share?token=${token}`)
      .then((r) => {
        if (!r.ok) throw new Error("Share not found or expired");
        return r.json();
      })
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground">Loading shared plan...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <h1 className="text-2xl font-bold mb-2">Not Found</h1>
          <p className="text-muted-foreground">
            {error || "This share link is invalid or has expired."}
          </p>
        </div>
      </div>
    );
  }

  const { route, simulation } = data;

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="border-b bg-background px-6 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">{route.name}</h1>
            <Badge variant="secondary">
              {route.sport === "cycling"
                ? "Cycling"
                : route.sport === "trail"
                  ? "Trail"
                  : "Ultra Trail"}
            </Badge>
          </div>
          <span className="text-sm text-muted-foreground">
            Shared via PaceForge
          </span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto p-6 space-y-6">
        {/* Route Stats */}
        <Card>
          <CardHeader>
            <CardTitle>Route</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">Distance</p>
                <p className="font-medium">
                  {(route.totalDistance / 1000).toFixed(1)} km
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">D+</p>
                <p className="font-medium text-red-500">
                  {route.elevationGain} m
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">D-</p>
                <p className="font-medium text-blue-500">
                  {route.elevationLoss} m
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Min elev.</p>
                <p className="font-medium">{route.minElevation} m</p>
              </div>
              <div>
                <p className="text-muted-foreground">Max elev.</p>
                <p className="font-medium">{route.maxElevation} m</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Simulation results */}
        {simulation && (
          <Card>
            <CardHeader>
              <CardTitle>Simulation Results</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <p className="text-muted-foreground text-sm">
                    Estimated total time
                  </p>
                  <p className="text-2xl font-bold">
                    {formatTime(simulation.totalTime)}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground text-sm">Avg speed</p>
                  <p className="text-2xl font-bold">
                    {(
                      (route.totalDistance / simulation.totalTime) *
                      3.6
                    ).toFixed(1)}{" "}
                    km/h
                  </p>
                </div>
              </div>

              {/* Splits */}
              <div className="space-y-1">
                {simulation.results.splits.map((split, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-sm py-1 border-b last:border-0"
                  >
                    <span className="text-muted-foreground">
                      Segment {i + 1}
                    </span>
                    <span>
                      {(split.distance / 1000).toFixed(1)} km
                    </span>
                    <span>{formatTime(split.time)}</span>
                    <span>{(split.speed * 3.6).toFixed(1)} km/h</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}
