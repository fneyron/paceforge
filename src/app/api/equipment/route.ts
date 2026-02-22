import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { equipment } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { eq, or, isNull } from "drizzle-orm";

export async function GET() {
  try {
    const userId = await getSessionUserId();

    const profiles = userId
      ? await db
          .select()
          .from(equipment)
          .where(or(eq(equipment.userId, userId), isNull(equipment.userId)))
      : await db.select().from(equipment).where(isNull(equipment.userId));

    return NextResponse.json(profiles);
  } catch (error) {
    console.error("Error fetching equipment:", error);
    return NextResponse.json({ error: "Failed to fetch equipment" }, { status: 500 });
  }
}
