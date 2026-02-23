import { sqliteTable, text, real, integer } from "drizzle-orm/sqlite-core";
import { users } from "./users";

export const personalRecords = sqliteTable("personal_records", {
  id: text("id").primaryKey(),
  userId: text("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  sport: text("sport").notNull(), // running, cycling, swimming
  category: text("category").notNull(), // "5K", "10K", "FTP_20min", etc.
  value: real("value").notNull(), // seconds (time) or watts (power)
  activityId: text("activity_id"), // link to strava activity
  date: integer("date", { mode: "timestamp" }).notNull(),
  createdAt: integer("created_at", { mode: "timestamp" })
    .notNull()
    .$defaultFn(() => new Date()),
});
