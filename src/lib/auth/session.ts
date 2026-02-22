import { auth } from "@/lib/auth";

/**
 * Get the current user ID from the session.
 * Returns null if not authenticated (allows anonymous access in dev).
 */
export async function getSessionUserId(): Promise<string | null> {
  const session = await auth();
  return session?.user?.id ?? null;
}
