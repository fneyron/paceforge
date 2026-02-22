import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { syncActivities } from "@/lib/strava/sync";

export async function POST() {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const count = await syncActivities(session.user.id);
    return NextResponse.json({ imported: count });
  } catch (error) {
    console.error("Strava sync error:", error);
    return NextResponse.json(
      { error: "Failed to sync activities" },
      { status: 500 }
    );
  }
}
