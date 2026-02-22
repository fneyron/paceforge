import { sqliteTable, text, integer, real } from "drizzle-orm/sqlite-core";

export const lactateTests = sqliteTable("lactate_tests", {
  id: text("id").primaryKey(),
  athleteId: text("athlete_id").notNull(),
  testDate: text("test_date").notNull(), // ISO date string
  protocol: text("protocol", { enum: ["running", "cycling"] }).notNull(),
  stepDuration: integer("step_duration").notNull(), // seconds
  startValue: real("start_value").notNull(), // km/h or watts
  increment: real("increment").notNull(), // km/h or watts per step
  steps: text("steps").notNull(), // JSON: [{value, lactate, hr}]
  lt1Speed: real("lt1_speed"), // km/h or watts
  lt1Lactate: real("lt1_lactate"), // mmol/L
  lt1HR: integer("lt1_hr"), // bpm
  lt2Speed: real("lt2_speed"), // km/h or watts
  lt2Lactate: real("lt2_lactate"), // mmol/L
  lt2HR: integer("lt2_hr"), // bpm
  createdAt: text("created_at")
    .notNull()
    .$defaultFn(() => new Date().toISOString()),
});
