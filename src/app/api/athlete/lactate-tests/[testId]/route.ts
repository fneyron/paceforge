import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { lactateTests } from "@/lib/db/schema";
import { eq } from "drizzle-orm";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ testId: string }> }
) {
  try {
    const { testId } = await params;

    const [test] = await db
      .select()
      .from(lactateTests)
      .where(eq(lactateTests.id, testId))
      .limit(1);

    if (!test) {
      return NextResponse.json({ error: "Test not found" }, { status: 404 });
    }

    return NextResponse.json({
      ...test,
      steps: JSON.parse(test.steps),
    });
  } catch (error) {
    console.error("Error fetching lactate test:", error);
    return NextResponse.json({ error: "Failed to fetch test" }, { status: 500 });
  }
}

export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ testId: string }> }
) {
  try {
    const { testId } = await params;

    await db.delete(lactateTests).where(eq(lactateTests.id, testId));

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Error deleting lactate test:", error);
    return NextResponse.json({ error: "Failed to delete test" }, { status: 500 });
  }
}
