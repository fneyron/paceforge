import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { routes } from "./routes";

export const waypoints = sqliteTable("waypoints", {
  id: text("id").primaryKey(),
  routeId: text("route_id")
    .notNull()
    .references(() => routes.id, { onDelete: "cascade" }),
  type: text("type", {
    enum: ["aid_station", "power_target", "pace_change", "nutrition", "transition", "custom"],
  }).notNull(),
  name: text("name").notNull(),
  distance: real("distance").notNull(), // meters along route
  lat: real("lat").notNull(),
  lon: real("lon").notNull(),
  ele: real("ele").notNull(),
  config: text("config").notNull().default("{}"), // JSON string
  orderIndex: integer("order_index").notNull().default(0),
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
