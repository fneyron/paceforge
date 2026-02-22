"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GPXUploader } from "@/components/gpx/gpx-uploader";
import { RouteCardMenu } from "@/components/routes/route-card-menu";
import { ActivityImportDialog } from "@/components/strava/activity-import-dialog";
import { RouteCardSkeleton } from "@/components/layout/route-card-skeleton";
import { ElevationSparkline } from "@/components/elevation/elevation-sparkline";
import { sportLabel, sportIcon } from "@/lib/sport-labels";

interface RouteListItem {
  id: string;
  name: string;
  sport: string;
  totalDistance: number;
  elevationGain: number;
  elevationLoss: number;
  createdAt: string;
  elevationSample?: number[];
}

export function Dashboard() {
  const [routes, setRoutes] = useState<RouteListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/routes")
      .then((r) => r.json())
      .then(setRoutes)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const updateRoute = (id: string, updates: Partial<RouteListItem>) => {
    setRoutes((prev) =>
      prev.map((r) => (r.id === id ? { ...r, ...updates } : r))
    );
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Routes</h1>
            <p className="text-muted-foreground">
              Import a GPX file to start planning your race.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/routes/new">
              <Badge variant="outline" className="cursor-pointer px-3 py-1.5 text-sm hover:bg-accent">
                Draw Route
              </Badge>
            </Link>
            <ActivityImportDialog />
            <GPXUploader />
          </div>
        </div>

        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <RouteCardSkeleton key={i} />
            ))}
          </div>
        ) : routes.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center justify-center py-12">
              <p className="text-muted-foreground mb-4">
                No routes yet. Upload your first GPX file to get started.
              </p>
              <GPXUploader />
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {routes.map((route) => {
              const SportIcon = sportIcon(route.sport);
              return (
                <Link key={route.id} href={`/routes/${route.id}`}>
                  <Card className="hover:border-primary/50 hover:shadow-md transition-all duration-200 cursor-pointer h-full">
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-2 min-w-0">
                          <SportIcon className="h-4 w-4 text-muted-foreground shrink-0" />
                          <CardTitle className="text-base truncate">
                            {route.name}
                          </CardTitle>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          <Badge variant="secondary">
                            {sportLabel(route.sport)}
                          </Badge>
                          <RouteCardMenu
                            routeId={route.id}
                            routeName={route.name}
                            routeSport={route.sport}
                            onRenamed={(name) => updateRoute(route.id, { name })}
                            onSportChanged={(sport) => updateRoute(route.id, { sport })}
                            onDuplicated={(newRoute) =>
                              setRoutes((prev) => [
                                {
                                  ...route,
                                  id: newRoute.id,
                                  name: newRoute.name,
                                  createdAt: new Date().toISOString(),
                                },
                                ...prev,
                              ])
                            }
                            onDeleted={() =>
                              setRoutes((prev) => prev.filter((r) => r.id !== route.id))
                            }
                          />
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="grid grid-cols-3 gap-2 text-sm">
                        <div>
                          <p className="text-muted-foreground text-xs">Distance</p>
                          <p className="font-medium">
                            {(route.totalDistance / 1000).toFixed(1)} km
                          </p>
                        </div>
                        <div>
                          <p className="text-muted-foreground text-xs">D+</p>
                          <p className="font-medium text-red-500">
                            {route.elevationGain} m
                          </p>
                        </div>
                        <div>
                          <p className="text-muted-foreground text-xs">D-</p>
                          <p className="font-medium text-blue-500">
                            {route.elevationLoss} m
                          </p>
                        </div>
                      </div>
                      {route.elevationSample && route.elevationSample.length > 1 && (
                        <div className="mt-2 text-muted-foreground">
                          <ElevationSparkline
                            elevations={route.elevationSample}
                            width={200}
                            height={24}
                            className="w-full"
                          />
                        </div>
                      )}
                      <p className="text-xs text-muted-foreground mt-2">
                        {new Date(route.createdAt).toLocaleDateString()}
                      </p>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
