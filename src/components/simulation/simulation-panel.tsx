"use client";

import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRouteStore } from "@/store/route-store";
import { CyclingConfigForm } from "./cycling-config";
import { TrailConfigForm } from "./trail-config";
import { SwimmingConfigForm } from "./swimming-config";
import { RoadRunningConfigForm } from "./road-running-config";
import { TriathlonConfigForm } from "./triathlon-config";
import { SimulationResults } from "./simulation-results";
import { PacingStrategySelector } from "./pacing-strategy-selector";
import { SimulationHistory } from "./simulation-history";
import { SimulationComparison } from "./simulation-comparison";
import { NutritionConfig } from "@/components/nutrition/nutrition-config";
import { NutritionTimeline } from "@/components/nutrition/nutrition-timeline";
import { ChevronDown, ChevronUp } from "lucide-react";
import type {
  SimulationResult,
  NutritionStrategy,
  CyclingConfig,
  TrailConfig,
  SwimmingConfig,
  RoadRunningConfig,
  TriathlonConfig,
  FatigueConfig,
} from "@/types/route";
import type { PacingStrategy } from "@/types/pacing";
import type { NutritionItem } from "@/lib/nutrition/planner";
import { DEFAULT_FATIGUE } from "@/lib/physics/fatigue";
import { formatTime } from "@/lib/physics/simulate";

type AnyConfig =
  | CyclingConfig
  | TrailConfig
  | SwimmingConfig
  | RoadRunningConfig
  | TriathlonConfig;

function sportToDefaultTab(sport: string): string {
  switch (sport) {
    case "cycling":
    case "gravel":
      return "cycling";
    case "trail":
    case "ultra_trail":
      return "trail";
    case "road_running":
      return "running";
    case "swimming":
      return "swimming";
    case "triathlon":
      return "triathlon";
    default:
      return "cycling";
  }
}

interface AthleteInfo {
  vdot: number | null;
  ftp: number;
  css: number | null;
}

export function SimulationPanel() {
  const routeId = useRouteStore((s) => s.routeId);
  const sport = useRouteStore((s) => s.sport);
  const [results, setResults] = useState<(SimulationResult & { id?: string }) | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nutritionItems, setNutritionItems] = useState<NutritionItem[]>([]);
  const [generatingNutrition, setGeneratingNutrition] = useState(false);
  const [pacingStrategy, setPacingStrategy] = useState<PacingStrategy | null>(null);
  const [athleteInfo, setAthleteInfo] = useState<AthleteInfo | null>(null);
  const [comparison, setComparison] = useState<{
    a: SimulationResult & { name: string };
    b: SimulationResult & { name: string };
  } | null>(null);

  // Collapsible sections
  const [configOpen, setConfigOpen] = useState(true);
  const [nutritionOpen, setNutritionOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const resultsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data) setAthleteInfo({ vdot: data.vdot, ftp: data.ftp, css: data.css });
      })
      .catch(() => {});
  }, []);

  const generateNutrition = async (strategy: NutritionStrategy) => {
    if (!routeId || !results) return;
    setGeneratingNutrition(true);
    try {
      const res = await fetch(`/api/routes/${routeId}/nutrition`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy,
          simulationId: results.id || "",
          totalTime: results.totalTime,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error || "Nutrition plan failed");
        return;
      }
      const data = await res.json();
      setNutritionItems(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nutrition plan failed");
    } finally {
      setGeneratingNutrition(false);
    }
  };

  const runSimulation = async (
    config: AnyConfig,
    sportType: string,
    fatigueConfig?: FatigueConfig
  ) => {
    if (!routeId) return;
    setRunning(true);
    setError(null);
    setNutritionItems([]);

    try {
      const res = await fetch(`/api/routes/${routeId}/simulation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sport: sportType,
          config,
          fatigueConfig:
            fatigueConfig || DEFAULT_FATIGUE[sportType] || DEFAULT_FATIGUE.cycling,
          pacingStrategy: pacingStrategy ?? undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Simulation failed");
      }
      const data = await res.json();
      setResults(data);
      setConfigOpen(false); // Collapse config to show results
      toast.success("Simulation complete");
      // Scroll to results
      setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth" }), 150);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Simulation failed";
      setError(msg);
      toast.error(msg);
    } finally {
      setRunning(false);
    }
  };

  if (!routeId) return null;

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button size="sm">Simulate</Button>
      </SheetTrigger>
      <SheetContent
        className="p-0 gap-0 overflow-hidden flex flex-col"
        style={{ width: "min(92vw, 640px)", maxWidth: "none" }}
      >
        <SheetHeader className="px-5 pt-4 pb-3 border-b shrink-0">
          <SheetTitle className="text-base">Race Simulation</SheetTitle>
        </SheetHeader>

        {/* Single scrollable area */}
        <div className="flex-1 overflow-y-auto">
          <div className="divide-y">

            {/* ── Section 1: Configuration ── */}
            <Section
              title="Configuration"
              open={configOpen}
              onToggle={() => setConfigOpen(!configOpen)}
              summary={results && !configOpen
                ? `${formatTime(results.totalTime)} — Click to modify`
                : undefined
              }
            >
              <div className="space-y-4">
                <PacingStrategySelector onChange={setPacingStrategy} />

                <Tabs defaultValue={sportToDefaultTab(sport)}>
                  <TabsList className="w-full flex-wrap h-auto gap-0.5">
                    <TabsTrigger value="cycling" className="flex-1 text-xs">Cycling</TabsTrigger>
                    <TabsTrigger value="trail" className="flex-1 text-xs">Trail</TabsTrigger>
                    <TabsTrigger value="running" className="flex-1 text-xs">Running</TabsTrigger>
                    <TabsTrigger value="swimming" className="flex-1 text-xs">Swim</TabsTrigger>
                    <TabsTrigger value="triathlon" className="flex-1 text-xs">Tri</TabsTrigger>
                  </TabsList>

                  <TabsContent value="cycling">
                    <CyclingConfigForm
                      onSimulate={(config, fatigue) => runSimulation(config, "cycling", fatigue)}
                      running={running}
                    />
                  </TabsContent>
                  <TabsContent value="trail">
                    <TrailConfigForm
                      onSimulate={(config, fatigue) => runSimulation(config, "trail", fatigue)}
                      running={running}
                    />
                  </TabsContent>
                  <TabsContent value="running">
                    <RoadRunningConfigForm
                      onSimulate={(config, fatigue) => runSimulation(config, "road_running", fatigue)}
                      running={running}
                    />
                  </TabsContent>
                  <TabsContent value="swimming">
                    <SwimmingConfigForm
                      onSimulate={(config, fatigue) => runSimulation(config, "swimming", fatigue)}
                      running={running}
                    />
                  </TabsContent>
                  <TabsContent value="triathlon">
                    <TriathlonConfigForm
                      onSimulate={(config, fatigue) => runSimulation(config, "triathlon", fatigue)}
                      running={running}
                    />
                  </TabsContent>
                </Tabs>
              </div>
            </Section>

            {/* ── Section 2: Results ── */}
            {results && (
              <div ref={resultsRef} className="px-5 py-4">
                {error && (
                  <div className="mb-3 rounded-lg border border-red-200 bg-red-50/50 px-3 py-2">
                    <p className="text-xs text-red-700">{error}</p>
                  </div>
                )}

                <SimulationResults
                  results={results}
                  sport={sport}
                  athleteVdot={athleteInfo?.vdot ?? undefined}
                />

                {comparison && (
                  <div className="mt-4">
                    <SimulationComparison a={comparison.a} b={comparison.b} />
                  </div>
                )}
              </div>
            )}

            {/* ── Section 3: Nutrition ── */}
            {results && (
              <Section
                title="Nutrition"
                open={nutritionOpen}
                onToggle={() => setNutritionOpen(!nutritionOpen)}
              >
                {nutritionItems.length > 0 ? (
                  <NutritionTimeline
                    items={nutritionItems}
                    totalTime={results.totalTime}
                  />
                ) : (
                  <NutritionConfig
                    onGenerate={generateNutrition}
                    generating={generatingNutrition}
                  />
                )}
              </Section>
            )}

            {/* ── Section 4: History ── */}
            <Section
              title="History"
              open={historyOpen}
              onToggle={() => setHistoryOpen(!historyOpen)}
            >
              <SimulationHistory
                routeId={routeId}
                onLoad={(loadedResults) => {
                  setResults(loadedResults);
                  setComparison(null);
                  setNutritionItems([]);
                  setConfigOpen(false);
                  setHistoryOpen(false);
                  setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth" }), 150);
                }}
                onCompare={(a, b) => {
                  setComparison({ a, b });
                  setConfigOpen(false);
                  setHistoryOpen(false);
                  setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth" }), 150);
                }}
              />
            </Section>

          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** Collapsible section with header */
function Section({
  title,
  open,
  onToggle,
  summary,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  summary?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-3 text-sm font-medium hover:bg-muted/50 transition-colors"
      >
        <span>{title}</span>
        <div className="flex items-center gap-2">
          {summary && !open && (
            <span className="text-xs text-muted-foreground font-normal">{summary}</span>
          )}
          {open ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </button>
      {open && (
        <div className="px-5 pb-4">
          {children}
        </div>
      )}
    </div>
  );
}
