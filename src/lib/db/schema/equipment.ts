import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { users } from "./users";

export const equipment = sqliteTable("equipment", {
  id: text("id").primaryKey(),
  userId: text("user_id")
    .references(() => users.id, { onDelete: "cascade" }),
  name: text("name").notNull(), // e.g. "Race setup", "Training setup"
  description: text("description"),
  bikeType: text("bike_type", {
    enum: ["road", "tt", "gravel", "mtb"],
  }).default("road"),
  bikeWeight: real("bike_weight").default(7), // kg
  cda: real("cda").default(0.32), // m²
  crr: real("crr").default(0.005),
  wheelType: text("wheel_type"), // e.g. "deep section", "disc"
  helmetType: text("helmet_type"), // e.g. "aero", "road", "tt"
  position: text("position"), // e.g. "hoods", "drops", "aero bars"
  isDefault: integer("is_default", { mode: "boolean" }).default(false),
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
