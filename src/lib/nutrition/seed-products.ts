import { db } from "@/lib/db";
import { nutritionProducts } from "@/lib/db/schema/nutrition-products";
import { nanoid } from "nanoid";
import { eq } from "drizzle-orm";

const DEFAULT_PRODUCTS = [
  // Gels
  { name: "Maurten Gel 100", brand: "Maurten", type: "gel" as const, calories: 100, carbs: 25, sodium: 0, caffeine: 0, protein: 0, fat: 0, fluidMl: 0, servingSize: "1 gel (40g)" },
  { name: "Maurten Gel 100 Caf 100", brand: "Maurten", type: "gel" as const, calories: 100, carbs: 25, sodium: 0, caffeine: 100, protein: 0, fat: 0, fluidMl: 0, servingSize: "1 gel (40g)" },
  { name: "SIS GO Isotonic Gel", brand: "SIS", type: "gel" as const, calories: 87, carbs: 22, sodium: 10, caffeine: 0, protein: 0, fat: 0, fluidMl: 0, servingSize: "1 gel (60ml)" },
  { name: "GU Energy Gel", brand: "GU", type: "gel" as const, calories: 100, carbs: 22, sodium: 55, caffeine: 0, protein: 0, fat: 0, fluidMl: 0, servingSize: "1 gel (32g)" },
  { name: "GU Roctane Gel + Caffeine", brand: "GU", type: "gel" as const, calories: 100, carbs: 21, sodium: 125, caffeine: 35, protein: 0, fat: 0, fluidMl: 0, servingSize: "1 gel (32g)" },

  // Bars
  { name: "Clif Bar", brand: "Clif", type: "bar" as const, calories: 250, carbs: 44, sodium: 210, caffeine: 0, protein: 9, fat: 5, fluidMl: 0, servingSize: "1 bar (68g)" },
  { name: "Maurten Solid 225", brand: "Maurten", type: "bar" as const, calories: 225, carbs: 45, sodium: 60, caffeine: 0, protein: 2, fat: 4, fluidMl: 0, servingSize: "1 bar (60g)" },

  // Drinks
  { name: "Maurten Drink Mix 320", brand: "Maurten", type: "drink" as const, calories: 320, carbs: 80, sodium: 0, caffeine: 0, protein: 0, fat: 0, fluidMl: 500, servingSize: "500ml bottle" },
  { name: "Tailwind Endurance Fuel", brand: "Tailwind", type: "drink" as const, calories: 200, carbs: 50, sodium: 310, caffeine: 0, protein: 0, fat: 0, fluidMl: 700, servingSize: "2 scoops / 700ml" },
  { name: "Skratch Labs Hydration Mix", brand: "Skratch", type: "drink" as const, calories: 80, carbs: 20, sodium: 380, caffeine: 0, protein: 0, fat: 0, fluidMl: 500, servingSize: "1 scoop / 500ml" },

  // Chews
  { name: "GU Energy Chews", brand: "GU", type: "chew" as const, calories: 90, carbs: 22, sodium: 50, caffeine: 0, protein: 0, fat: 0, fluidMl: 0, servingSize: "4 chews" },
  { name: "Clif Bloks", brand: "Clif", type: "chew" as const, calories: 100, carbs: 24, sodium: 50, caffeine: 0, protein: 0, fat: 0, fluidMl: 0, servingSize: "3 chews" },
];

/**
 * Seed default nutrition products if none exist.
 */
export async function seedNutritionProducts(): Promise<void> {
  const existing = await db
    .select({ id: nutritionProducts.id })
    .from(nutritionProducts)
    .where(eq(nutritionProducts.isDefault, true))
    .limit(1);

  if (existing.length > 0) return;

  for (const product of DEFAULT_PRODUCTS) {
    await db.insert(nutritionProducts).values({
      id: nanoid(),
      ...product,
      isDefault: true,
    });
  }
}
