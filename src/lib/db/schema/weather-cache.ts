import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";

export const weatherCache = sqliteTable("weather_cache", {
  id: text("id").primaryKey(),
  lat: real("lat").notNull(),
  lon: real("lon").notNull(),
  date: text("date").notNull(), // ISO date
  hour: integer("hour").notNull(), // 0-23
  temperature: real("temperature").notNull(), // °C
  humidity: real("humidity").notNull(), // %
  windSpeed: real("wind_speed").notNull(), // m/s
  windDirection: real("wind_direction").notNull(), // degrees
  pressure: real("pressure").notNull(), // hPa
  precipitation: real("precipitation").notNull(), // mm/h
  cloudCover: real("cloud_cover").notNull(), // %
  fetchedAt: integer("fetched_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
