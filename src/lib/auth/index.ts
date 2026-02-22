import NextAuth from "next-auth";
import { StravaProvider } from "./strava-provider";
import { db } from "@/lib/db";
import { users, stravaTokens } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { nanoid } from "nanoid";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    StravaProvider({
      clientId: process.env.STRAVA_CLIENT_ID!,
      clientSecret: process.env.STRAVA_CLIENT_SECRET!,
    }),
  ],
  session: {
    strategy: "jwt",
  },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account && profile) {
        const stravaId = String(profile.id ?? account.providerAccountId);
        const displayName =
          (profile as { firstname?: string }).firstname && (profile as { lastname?: string }).lastname
            ? `${(profile as { firstname: string }).firstname} ${(profile as { lastname: string }).lastname}`
            : (profile.name as string) || "Athlete";
        const avatarUrl =
          (profile as { profile_medium?: string }).profile_medium ||
          (profile as { profile?: string }).profile ||
          (profile.image as string) ||
          null;

        // Upsert user
        const existing = await db
          .select()
          .from(users)
          .where(eq(users.stravaId, stravaId))
          .limit(1);

        let userId: string;
        if (existing.length > 0) {
          userId = existing[0].id;
          await db
            .update(users)
            .set({ displayName, avatarUrl, updatedAt: new Date() })
            .where(eq(users.id, userId));
        } else {
          userId = nanoid();
          await db.insert(users).values({
            id: userId,
            stravaId,
            displayName,
            avatarUrl,
          });
        }

        // Upsert Strava tokens
        const existingToken = await db
          .select()
          .from(stravaTokens)
          .where(eq(stravaTokens.userId, userId))
          .limit(1);

        const tokenData = {
          accessToken: account.access_token!,
          refreshToken: account.refresh_token!,
          expiresAt: account.expires_at!,
          scope: (account as { scope?: string }).scope || null,
        };

        if (existingToken.length > 0) {
          await db
            .update(stravaTokens)
            .set(tokenData)
            .where(eq(stravaTokens.userId, userId));
        } else {
          await db.insert(stravaTokens).values({
            id: nanoid(),
            userId,
            ...tokenData,
          });
        }

        token.userId = userId;
        token.stravaId = stravaId;
        token.displayName = displayName;
        token.avatarUrl = avatarUrl;
      }
      return token;
    },
    async session({ session, token }) {
      if (token) {
        session.user.id = token.userId as string;
        (session.user as { stravaId?: string }).stravaId = token.stravaId as string;
        session.user.name = token.displayName as string;
        session.user.image = token.avatarUrl as string | null;
      }
      return session;
    },
  },
});
