import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { routes } from "./routes";

export const segments = sqliteTable("segments", {
  id: text("id").primaryKey(),
  routeId: text("route_id")
    .notNull()
    .references(() => routes.id, { onDelete: "cascade" }),
  type: text("type", { enum: ["climb", "descent", "flat"] }).notNull(),
  orderIndex: integer("order_index").notNull(),
  startDistance: real("start_distance").notNull(), // meters
  endDistance: real("end_distance").notNull(), // meters
  startIndex: integer("start_index").notNull(),
  endIndex: integer("end_index").notNull(),
  elevationGain: real("elevation_gain").notNull(),
  elevationLoss: real("elevation_loss").notNull(),
  averageGrade: real("average_grade").notNull(), // fraction
  maxGrade: real("max_grade").notNull(), // fraction
  length: real("length").notNull(), // meters
});
