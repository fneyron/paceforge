"use client";

import { signIn } from "next-auth/react";
import { Button } from "@/components/ui/button";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function LoginContent() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") || "/";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="mx-auto flex w-full max-w-sm flex-col items-center space-y-6 p-6">
        <div className="space-y-2 text-center">
          <h1 className="text-3xl font-bold tracking-tight">PaceForge</h1>
          <p className="text-muted-foreground">
            Physics-based race planning for endurance athletes
          </p>
        </div>

        <Button
          size="lg"
          className="w-full"
          onClick={() => signIn("strava", { callbackUrl })}
        >
          <svg
            className="mr-2 h-5 w-5"
            viewBox="0 0 24 24"
            fill="currentColor"
          >
            <path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169" />
          </svg>
          Connect with Strava
        </Button>

        <p className="text-xs text-muted-foreground text-center">
          Sign in with your Strava account to access your activities, sync
          performance data, and plan your races.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
