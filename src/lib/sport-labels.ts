import type { LucideIcon } from "lucide-react";
import { Bike, Mountain, Footprints, Waves, Trophy, Route, Snowflake, Ship, Repeat, Shuffle } from "lucide-react";

const SPORT_LABELS: Record<string, string> = {
  cycling: "Cycling",
  gravel: "Gravel",
  trail: "Trail",
  ultra_trail: "Ultra Trail",
  road_running: "Road Running",
  swimming: "Swimming",
  triathlon: "Triathlon",
  cross_country_skiing: "Cross-Country Skiing",
  rowing: "Rowing",
  duathlon: "Duathlon",
  swimrun: "SwimRun",
};

const SPORT_ICONS: Record<string, LucideIcon> = {
  cycling: Bike,
  gravel: Bike,
  trail: Mountain,
  ultra_trail: Mountain,
  road_running: Footprints,
  swimming: Waves,
  triathlon: Trophy,
  cross_country_skiing: Snowflake,
  rowing: Ship,
  duathlon: Repeat,
  swimrun: Shuffle,
};

export function sportLabel(sport: string): string {
  return SPORT_LABELS[sport] ?? sport;
}

export function sportIcon(sport: string): LucideIcon {
  return SPORT_ICONS[sport] ?? Route;
}

export const SPORT_OPTIONS = Object.entries(SPORT_LABELS).map(([value, label]) => ({
  value,
  label,
}));
