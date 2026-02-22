"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { sportLabel, SPORT_OPTIONS } from "@/lib/sport-labels";

interface StravaActivity {
  id: string;
  stravaActivityId: string;
  name: string;
  sport: string;
  distance: number;
  elevationGain: number | null;
  startDate: string;
}

export function ActivityImportDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [activities, setActivities] = useState<StravaActivity[]>([]);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [sportFilter, setSportFilter] = useState("all");

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetch("/api/strava/activities?limit=100")
      .then((r) => r.json())
      .then(setActivities)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [open]);

  const filtered = activities.filter((a) => {
    if (filter && !a.name.toLowerCase().includes(filter.toLowerCase())) return false;
    if (sportFilter !== "all" && !a.sport.toLowerCase().includes(sportFilter.toLowerCase())) return false;
    return true;
  });

  const handleImport = async (activity: StravaActivity) => {
    setImporting(activity.stravaActivityId);
    try {
      const res = await fetch("/api/strava/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          activityId: activity.stravaActivityId,
          name: activity.name,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setOpen(false);
        router.push(`/routes/${data.id}`);
      }
    } finally {
      setImporting(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Import from Strava
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Import from Strava</DialogTitle>
        </DialogHeader>

        <div className="flex gap-2">
          <Input
            placeholder="Filter by name..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="flex-1"
          />
          <Select value={sportFilter} onValueChange={setSportFilter}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sports</SelectItem>
              {SPORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex-1 overflow-y-auto space-y-1 min-h-0">
          {loading ? (
            <p className="text-sm text-muted-foreground text-center py-8">Loading activities...</p>
          ) : filtered.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">No activities found.</p>
          ) : (
            filtered.map((a) => (
              <div
                key={a.id}
                className="flex items-center gap-3 border rounded-md p-2 hover:bg-muted/50 cursor-pointer"
                onClick={() => handleImport(a)}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{a.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {sportLabel(a.sport)} &middot;{" "}
                    {(a.distance / 1000).toFixed(1)} km
                    {a.elevationGain ? ` &middot; ${Math.round(a.elevationGain)}m D+` : ""} &middot;{" "}
                    {new Date(a.startDate).toLocaleDateString()}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="shrink-0"
                  disabled={importing === a.stravaActivityId}
                >
                  {importing === a.stravaActivityId ? "Importing..." : "Import"}
                </Button>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
