import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
import { routes } from "./routes";

export const shares = sqliteTable("shares", {
  id: text("id").primaryKey(),
  token: text("token").notNull().unique(),
  routeId: text("route_id")
    .notNull()
    .references(() => routes.id, { onDelete: "cascade" }),
  simulationId: text("simulation_id"),
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
  expiresAt: integer("expires_at", { mode: "timestamp" }),
});
