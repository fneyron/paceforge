import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { routes } from "./routes";

export const simulations = sqliteTable("simulations", {
  id: text("id").primaryKey(),
  userId: text("user_id"), // nullable for backward compat
  routeId: text("route_id")
    .notNull()
    .references(() => routes.id, { onDelete: "cascade" }),
  name: text("name"), // optional name for the simulation
  sport: text("sport", { enum: ["cycling", "gravel", "trail", "ultra_trail", "road_running", "swimming", "triathlon"] }).notNull(),
  config: text("config").notNull(), // JSON string of sport-specific config
  fatigueConfig: text("fatigue_config").notNull(), // JSON string
  weatherConfig: text("weather_config"), // JSON string of WeatherCondition[]
  results: text("results").notNull(), // JSON string of SimulationResult
  totalTime: real("total_time").notNull(), // seconds
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
