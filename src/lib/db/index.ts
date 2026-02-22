import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import * as schema from "./schema";
import path from "path";

const globalForDb = globalThis as unknown as {
  __db?: ReturnType<typeof drizzle>;
};

function createDb() {
  const dbPath = path.join(process.cwd(), "sqlite.db");
  const sqlite = new Database(dbPath);

  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  sqlite.pragma("busy_timeout = 5000");

  return drizzle(sqlite, { schema });
}

export const db = globalForDb.__db ?? (globalForDb.__db = createDb());
