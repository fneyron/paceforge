"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { signOut } from "next-auth/react";
import { LayoutGrid, User, LogOut, Unlink } from "lucide-react";
import { toast } from "sonner";
import type { LucideIcon } from "lucide-react";

const navItems: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/", label: "Dashboard", icon: LayoutGrid },
  { href: "/athlete", label: "Profile", icon: User },
];

interface UserInfo {
  displayName: string;
  avatarUrl: string | null;
}

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<UserInfo | null>(null);
  const [showMenu, setShowMenu] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  useEffect(() => {
    fetch("/api/strava/profile")
      .then((r) => {
        if (r.ok) return r.json();
        return null;
      })
      .then((data) => {
        if (data?.user) setUser(data.user);
      })
      .catch(() => {});
  }, []);

  // Close menu on outside click
  useEffect(() => {
    if (!showMenu) return;
    const handler = () => setShowMenu(false);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [showMenu]);

  const handleDisconnectStrava = async () => {
    setDisconnecting(true);
    try {
      const res = await fetch("/api/strava/disconnect", { method: "POST" });
      if (res.ok) {
        toast.success("Strava disconnected");
        // Sign out and redirect to login
        await signOut({ callbackUrl: "/login" });
      } else {
        toast.error("Failed to disconnect Strava");
      }
    } catch {
      toast.error("Failed to disconnect Strava");
    } finally {
      setDisconnecting(false);
      setShowMenu(false);
    }
  };

  const handleSignOut = async () => {
    await signOut({ callbackUrl: "/login" });
  };

  return (
    <aside className="w-56 border-r bg-muted/30 flex flex-col shrink-0">
      <div className="p-4 border-b">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-xl font-bold tracking-tight">PaceForge</span>
        </Link>
      </div>
      <nav className="flex-1 p-2 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors ${
                pathname === item.href
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* User section */}
      <div className="relative p-3 border-t">
        {user ? (
          <>
            <div className="flex items-center gap-2">
              <Link href="/athlete" className="flex items-center gap-2 flex-1 min-w-0 hover:opacity-80">
                {user.avatarUrl ? (
                  <img
                    src={user.avatarUrl}
                    alt={user.displayName}
                    className="w-8 h-8 rounded-full"
                  />
                ) : (
                  <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs font-medium">
                    {user.displayName.charAt(0).toUpperCase()}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{user.displayName}</p>
                  <p className="text-[10px] text-muted-foreground">Strava connected</p>
                </div>
              </Link>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowMenu((prev) => !prev);
                }}
                className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                title="Account menu"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>

            {/* Popup menu */}
            {showMenu && (
              <div className="absolute bottom-full left-3 right-3 mb-1 bg-popover border rounded-md shadow-md py-1 z-50">
                <button
                  onClick={handleDisconnectStrava}
                  disabled={disconnecting}
                  className="flex items-center gap-2 px-3 py-2 text-sm w-full text-left hover:bg-muted transition-colors text-orange-600"
                >
                  <Unlink className="h-3.5 w-3.5" />
                  {disconnecting ? "Disconnecting..." : "Disconnect Strava"}
                </button>
                <button
                  onClick={handleSignOut}
                  className="flex items-center gap-2 px-3 py-2 text-sm w-full text-left hover:bg-muted transition-colors text-destructive"
                >
                  <LogOut className="h-3.5 w-3.5" />
                  Sign out
                </button>
              </div>
            )}
          </>
        ) : (
          <button
            onClick={handleSignOut}
            className="flex items-center gap-2 px-3 py-2 text-sm w-full text-left rounded-md hover:bg-muted transition-colors text-muted-foreground"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}
