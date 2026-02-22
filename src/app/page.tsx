"use client";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { Dashboard } from "@/components/layout/dashboard";

export default function Home() {
  return (
    <div className="flex h-screen">
      <AppSidebar />
      <Dashboard />
    </div>
  );
}
