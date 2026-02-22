import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";

export const nutritionProducts = sqliteTable("nutrition_products", {
  id: text("id").primaryKey(),
  userId: text("user_id"), // null = default product, set = user custom
  name: text("name").notNull(),
  brand: text("brand"),
  type: text("type", {
    enum: ["gel", "bar", "drink", "chew", "real_food", "custom"],
  }).notNull(),
  calories: real("calories").notNull(), // kcal per serving
  carbs: real("carbs").notNull(), // grams per serving
  sodium: real("sodium").default(0), // mg per serving
  caffeine: real("caffeine").default(0), // mg per serving
  protein: real("protein").default(0), // grams per serving
  fat: real("fat").default(0), // grams per serving
  fluidMl: real("fluid_ml").default(0), // ml of fluid per serving
  servingSize: text("serving_size"), // e.g. "1 gel", "500ml"
  isDefault: integer("is_default", { mode: "boolean" }).default(false),
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
