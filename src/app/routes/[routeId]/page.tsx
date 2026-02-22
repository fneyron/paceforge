"use client";

import { useEffect, useState, use, useRef } from "react";
import { useRouter } from "next/navigation";
import { useRouteStore } from "@/store/route-store";
import { RouteMap } from "@/components/map/route-map";
import { ElevationProfile } from "@/components/elevation/elevation-profile";
import { RouteSidebar, RouteSegmentsSidebar } from "@/components/layout/route-sidebar";
import { RoutePredictionsSidebar } from "@/components/predictions/route-predictions-sidebar";
import { WaypointEditor } from "@/components/waypoints/waypoint-editor";
import { SimulationPanel } from "@/components/simulation/simulation-panel";
import { ShareDialog } from "@/components/share/share-dialog";
import { ExportDialog } from "@/components/export/export-dialog";
import { WeatherPanel } from "@/components/weather/weather-panel";
import { ChangeSportDialog } from "@/components/routes/change-sport-dialog";
import { DeleteRouteDialog } from "@/components/routes/delete-route-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { sportLabel } from "@/lib/sport-labels";
import Link from "next/link";
import type { WeatherCondition } from "@/types/route";

export default function RoutePage({
  params,
}: {
  params: Promise<{ routeId: string }>;
}) {
  const { routeId } = use(params);
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [weather, setWeather] = useState<WeatherCondition[]>([]);
  const [fetchingWeather, setFetchingWeather] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [sportDialogOpen, setSportDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const setRoute = useRouteStore((s) => s.setRoute);
  const setWaypoints = useRouteStore((s) => s.setWaypoints);
  const setName = useRouteStore((s) => s.setName);
  const setSport = useRouteStore((s) => s.setSport);
  const setRaceDate = useRouteStore((s) => s.setRaceDate);
  const setRaceStartTime = useRouteStore((s) => s.setRaceStartTime);
  const routeName = useRouteStore((s) => s.name);
  const routeSport = useRouteStore((s) => s.sport);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`/api/routes/${routeId}`);
        if (!res.ok) throw new Error("Failed to load route");
        const data = await res.json();

        setRoute({
          routeId: data.id,
          name: data.name,
          sport: data.sport,
          geojson: data.geojson,
          points: data.points,
          segments: data.segments,
          stats: {
            totalDistance: data.totalDistance,
            elevationGain: data.elevationGain,
            elevationLoss: data.elevationLoss,
            minElevation: data.minElevation,
            maxElevation: data.maxElevation,
          },
          raceDate: data.raceDate,
          raceStartTime: data.raceStartTime,
        });

        // Load waypoints
        const wpRes = await fetch(`/api/routes/${routeId}/waypoints`);
        if (wpRes.ok) {
          const wpData = await wpRes.json();
          setWaypoints(wpData);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [routeId, setRoute, setWaypoints]);

  const handleFetchWeather = async () => {
    setFetchingWeather(true);
    try {
      const res = await fetch(`/api/routes/${routeId}/weather`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setWeather(data.conditions || []);
      }
    } catch (err) {
      console.error("Weather fetch failed:", err);
    } finally {
      setFetchingWeather(false);
    }
  };

  const startEditingName = () => {
    setNameInput(routeName);
    setEditingName(true);
    setTimeout(() => nameInputRef.current?.select(), 0);
  };

  const saveName = async () => {
    const trimmed = nameInput.trim();
    if (!trimmed || trimmed === routeName) {
      setEditingName(false);
      return;
    }
    try {
      const res = await fetch(`/api/routes/${routeId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (res.ok) {
        setName(trimmed);
      }
    } catch {
      // Revert on error
    }
    setEditingName(false);
  };

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") saveName();
    if (e.key === "Escape") setEditingName(false);
  };

  const handleSportChanged = (sport: string) => {
    setSport(sport);
  };

  if (loading) {
    return (
      <div className="h-screen flex flex-col">
        <header className="border-b px-4 py-2 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <div className="h-8 w-20 animate-pulse rounded bg-muted" />
            <div className="h-6 w-48 animate-pulse rounded bg-muted" />
            <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
          </div>
          <div className="flex items-center gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-8 w-16 animate-pulse rounded bg-muted" />
            ))}
          </div>
        </header>
        <div className="flex-1 flex overflow-hidden">
          <aside className="w-80 border-r shrink-0 hidden lg:block p-4 space-y-4">
            <div className="h-6 w-24 animate-pulse rounded bg-muted" />
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-4 w-full animate-pulse rounded bg-muted" />
            ))}
          </aside>
          <main className="flex-1 flex flex-col overflow-hidden">
            <div className="flex-[2] min-h-0 animate-pulse bg-muted" />
            <div className="flex-[1] min-h-[200px] border-t animate-pulse bg-muted/50" />
          </main>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center space-y-4">
          <p className="text-destructive">{error}</p>
          <Button asChild variant="outline">
            <Link href="/">Back to dashboard</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col animate-in fade-in duration-300">
      {/* Header */}
      <header className="border-b px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link href="/">← Routes</Link>
          </Button>
          {editingName ? (
            <Input
              ref={nameInputRef}
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              onBlur={saveName}
              onKeyDown={handleNameKeyDown}
              className="h-8 w-64 text-lg font-semibold"
              autoFocus
            />
          ) : (
            <h1
              className="text-lg font-semibold truncate max-w-md cursor-pointer hover:text-primary/80"
              onClick={startEditingName}
              title="Click to rename"
            >
              {routeName}
            </h1>
          )}
          <Badge
            variant="secondary"
            className="cursor-pointer hover:bg-secondary/80"
            onClick={() => setSportDialogOpen(true)}
            title="Click to change sport"
          >
            {sportLabel(routeSport)}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleFetchWeather}
            disabled={fetchingWeather}
          >
            {fetchingWeather ? "Loading..." : "Weather"}
          </Button>
          <WaypointEditor />
          <SimulationPanel />
          <ExportDialog routeId={routeId} />
          <ShareDialog />
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive"
            onClick={() => setDeleteDialogOpen(true)}
            title="Delete route"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </Button>
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <aside className="w-80 border-r shrink-0 hidden lg:block">
          <Tabs defaultValue="info" className="h-full flex flex-col">
            <TabsList className="mx-4 mt-2 flex-wrap h-auto gap-0.5">
              <TabsTrigger value="info">Info</TabsTrigger>
              <TabsTrigger value="segments">Segments</TabsTrigger>
              <TabsTrigger value="predictions">Predictions</TabsTrigger>
              {weather.length > 0 && (
                <TabsTrigger value="weather">Weather</TabsTrigger>
              )}
            </TabsList>
            <TabsContent value="info" className="flex-1 overflow-hidden">
              <RouteSidebar />
            </TabsContent>
            <TabsContent value="segments" className="flex-1 overflow-hidden">
              <RouteSegmentsSidebar />
            </TabsContent>
            <TabsContent value="predictions" className="flex-1 overflow-hidden">
              <RoutePredictionsSidebar />
            </TabsContent>
            {weather.length > 0 && (
              <TabsContent value="weather" className="flex-1 overflow-auto p-4">
                <WeatherPanel conditions={weather} />
              </TabsContent>
            )}
          </Tabs>
        </aside>

        {/* Map + Profile */}
        <main className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-[2] min-h-0">
            <RouteMap />
          </div>
          <div className="flex-[1] min-h-[200px] border-t">
            <ElevationProfile />
          </div>
        </main>
      </div>

      <ChangeSportDialog
        open={sportDialogOpen}
        onOpenChange={setSportDialogOpen}
        routeId={routeId}
        currentSport={routeSport}
        onSportChanged={handleSportChanged}
      />
      <DeleteRouteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        routeId={routeId}
        routeName={routeName}
        onDeleted={() => router.push("/")}
      />
    </div>
  );
}
