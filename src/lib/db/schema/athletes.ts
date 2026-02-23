import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";

export const athletes = sqliteTable("athletes", {
  id: text("id").primaryKey(),
  userId: text("user_id"), // nullable for backward compat
  name: text("name").notNull().default("Athlete"),
  weight: real("weight").default(70), // kg
  height: real("height"), // cm
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
  updatedAt: integer("updated_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),

  // Cycling params
  ftp: real("ftp").default(250), // watts
  bikeWeight: real("bike_weight").default(8), // kg
  cda: real("cda").default(0.32), // m²
  crr: real("crr").default(0.005),
  efficiency: real("efficiency").default(0.25),

  // Trail params
  vma: real("vma").default(15), // km/h
  vo2max: real("vo2max"),
  fcMax: real("fc_max"),
  lactateThreshold: real("lactate_threshold"),

  // Swimming params
  css: real("css"), // sec/100m (Critical Swim Speed)
  swimHasWetsuit: integer("swim_has_wetsuit", { mode: "boolean" }).default(false),

  // Running params
  vdot: real("vdot"), // VDOT score (30-85)

  // Triathlon params
  t1Time: real("t1_time").default(120), // seconds (swim → bike)
  t2Time: real("t2_time").default(60), // seconds (bike → run)

  // Critical Power model
  cp: real("cp"), // Critical Power (watts)
  wPrime: real("w_prime"), // W' anaerobic capacity (joules)
  referenceRaceDistance: real("ref_race_distance"), // meters (for Riegel model)
  referenceRaceTime: real("ref_race_time"), // seconds (for Riegel model)

  // Strava auto-detected values
  stravaFtp: real("strava_ftp"),
  stravaVdot: real("strava_vdot"),
  stravaCss: real("strava_css"),
  stravaCda: real("strava_cda"),
  lastStravaSync: integer("last_strava_sync", { mode: "timestamp" }),
});
