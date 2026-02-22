import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { simulations } from "./simulations";

export const nutritionPlans = sqliteTable("nutrition_plans", {
  id: text("id").primaryKey(),
  simulationId: text("simulation_id")
    .notNull()
    .references(() => simulations.id, { onDelete: "cascade" }),
  strategy: text("strategy").notNull(), // JSON NutritionStrategy
  items: text("items").notNull(), // JSON array of planned intake items
  totalCalories: real("total_calories").notNull(),
  totalCarbs: real("total_carbs").notNull(), // grams
  totalSodium: real("total_sodium").notNull(), // mg
  totalCaffeine: real("total_caffeine").notNull(), // mg
  totalFluid: real("total_fluid").notNull(), // ml
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
