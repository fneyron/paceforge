import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";

export const routes = sqliteTable("routes", {
  id: text("id").primaryKey(),
  userId: text("user_id"), // nullable for backward compat
  name: text("name").notNull(),
  sport: text("sport", { enum: ["cycling", "gravel", "trail", "ultra_trail", "road_running", "swimming", "triathlon", "cross_country_skiing", "rowing", "duathlon", "swimrun"] })
    .notNull()
    .default("cycling"),
  gpxRaw: text("gpx_raw").notNull(),
  geojson: text("geojson").notNull(), // JSON string
  points: text("points").notNull(), // JSON string of RoutePoint[]
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
  updatedAt: integer("updated_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),

  // Race metadata
  raceDate: text("race_date"), // ISO date string
  raceStartTime: text("race_start_time"), // HH:MM format
  triathlonLegs: text("triathlon_legs"), // JSON string for triathlon leg configs

  // Aggregated stats
  totalDistance: real("total_distance"), // meters
  elevationGain: real("elevation_gain"), // meters
  elevationLoss: real("elevation_loss"), // meters
  minElevation: real("min_elevation"), // meters
  maxElevation: real("max_elevation"), // meters
});
