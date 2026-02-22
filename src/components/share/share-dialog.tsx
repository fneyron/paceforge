"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useRouteStore } from "@/store/route-store";

export function ShareDialog() {
  const [open, setOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState(false);
  const routeId = useRouteStore((s) => s.routeId);

  const createShareLink = async () => {
    if (!routeId) return;
    setCreating(true);

    try {
      const res = await fetch("/api/share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ routeId }),
      });

      if (res.ok) {
        const data = await res.json();
        setShareUrl(`${window.location.origin}${data.url}`);
      }
    } catch (err) {
      console.error("Failed to create share link:", err);
    } finally {
      setCreating(false);
    }
  };

  const copyToClipboard = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!routeId) return null;

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) {
          setShareUrl(null);
          setCopied(false);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Share
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share Route</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {!shareUrl ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Create a public link to share your route and simulation
                results. Anyone with the link can view the data (read-only).
              </p>
              <Button
                onClick={createShareLink}
                disabled={creating}
                className="w-full"
              >
                {creating ? "Creating..." : "Create Share Link"}
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex gap-2">
                <Input value={shareUrl} readOnly className="flex-1" />
                <Button onClick={copyToClipboard} variant="outline">
                  {copied ? "Copied!" : "Copy"}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                This link does not expire.
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
