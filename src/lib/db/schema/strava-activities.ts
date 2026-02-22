import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { users } from "./users";

export const stravaActivities = sqliteTable("strava_activities", {
  id: text("id").primaryKey(), // nanoid
  userId: text("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  stravaActivityId: text("strava_activity_id").notNull(),
  sport: text("sport").notNull(), // strava sport type string
  name: text("name").notNull(),
  startDate: integer("start_date", { mode: "timestamp" }).notNull(),
  distance: real("distance").notNull(), // meters
  movingTime: real("moving_time").notNull(), // seconds
  elapsedTime: real("elapsed_time").notNull(), // seconds
  elevationGain: real("elevation_gain"), // meters
  averagePower: real("average_power"), // watts
  normalizedPower: real("normalized_power"), // watts
  maxPower: real("max_power"), // watts
  averageHeartRate: real("average_heart_rate"),
  maxHeartRate: real("max_heart_rate"),
  averageSpeed: real("average_speed"), // m/s
  maxSpeed: real("max_speed"), // m/s
  averageCadence: real("average_cadence"),
  rawData: text("raw_data"), // full JSON from Strava
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
