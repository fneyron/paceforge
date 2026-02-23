"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { ZoneDisplay } from "@/components/zones/zone-display";
import { RacePredictions } from "@/components/predictions/race-predictions";
import { LactateHistory } from "@/components/lactate/lactate-history";
import { LactateProtocolWizard } from "@/components/lactate/lactate-protocol-wizard";
import { PMCChart } from "@/components/training/pmc-chart";
import { TrainingSummary } from "@/components/training/training-summary";
import { PersonalRecordsTable } from "@/components/training/personal-records";

interface AthleteData {
  name: string;
  weight: number;
  height: number | null;
  ftp: number | null;
  bikeWeight: number;
  cda: number | null;
  crr: number;
  efficiency: number;
  vma: number;
  vo2max: number | null;
  fcMax: number | null;
  lactateThreshold: number | null;
  css: number | null;
  swimHasWetsuit: boolean;
  vdot: number | null;
  t1Time: number;
  t2Time: number;
  stravaFtp: number | null;
  stravaVdot: number | null;
  stravaCss: number | null;
  stravaCda: number | null;
}

interface StravaUser {
  displayName: string;
  avatarUrl: string | null;
  stravaId: string | null;
}

function ProfileSkeleton() {
  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <Skeleton className="h-8 w-40" />
        <div className="flex gap-2">
          <Skeleton className="h-9 w-40" />
          <Skeleton className="h-9 w-16" />
        </div>
      </div>
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-20" />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="space-y-2">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      <Skeleton className="h-10 w-full" />
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-36" />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-2">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function AthletePage() {
  const [athlete, setAthlete] = useState<AthleteData | null>(null);
  const [stravaUser, setStravaUser] = useState<StravaUser | null>(null);
  const [saving, setSaving] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [lactateWizardOpen, setLactateWizardOpen] = useState(false);
  const [pmcData, setPmcData] = useState<Array<{ date: string; tss: number; ctl: number; atl: number; tsb: number }>>([]);
  const [records, setRecords] = useState<Array<{ sport: string; category: string; value: number; date: Date }>>([]);

  useEffect(() => {
    // Load athlete profile
    fetch("/api/athlete")
      .then((r) => r.json())
      .then(setAthlete)
      .catch(() => toast.error("Failed to load athlete profile"));

    // Load PMC data
    fetch("/api/athlete/pmc")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => { if (Array.isArray(data)) setPmcData(data); })
      .catch(() => {});

    // Load personal records
    fetch("/api/athlete/records")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (Array.isArray(data)) {
          setRecords(data.map((r: { sport: string; category: string; value: number; date: string }) => ({
            ...r,
            date: new Date(r.date),
          })));
        }
      })
      .catch(() => {});

    // Load Strava user info (avatar, name) + auto-sync estimates
    fetch("/api/strava/profile")
      .then((r) => {
        if (r.ok) return r.json();
        return null;
      })
      .then((data) => {
        if (data?.user) {
          setStravaUser(data.user);
        }
        // If we got new estimates, refresh athlete data
        if (data?.estimates) {
          fetch("/api/athlete")
            .then((r) => r.json())
            .then(setAthlete)
            .catch(() => {});
        }
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    if (!athlete) return;
    setSaving(true);

    try {
      const res = await fetch("/api/athlete", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(athlete),
      });
      if (res.ok) {
        const data = await res.json();
        setAthlete(data);
        toast.success("Profile saved");
      } else {
        toast.error("Failed to save profile");
      }
    } catch (err) {
      toast.error("Failed to save profile");
      console.error("Failed to save:", err);
    } finally {
      setSaving(false);
    }
  };

  const handleAutoDetect = async () => {
    setDetecting(true);
    try {
      const res = await fetch("/api/strava/sync", { method: "POST" });
      if (res.ok) {
        const athleteRes = await fetch("/api/athlete");
        if (athleteRes.ok) {
          setAthlete(await athleteRes.json());
          toast.success("Strava data synced");
        }
      } else {
        toast.error("Strava sync failed");
      }
    } catch (err) {
      toast.error("Auto-detect failed");
      console.error("Auto-detect failed:", err);
    } finally {
      setDetecting(false);
    }
  };

  const update = (field: keyof AthleteData, value: string | number | boolean) => {
    if (!athlete) return;
    setAthlete({ ...athlete, [field]: value });
  };

  if (!athlete) {
    return (
      <div className="flex h-screen">
        <AppSidebar />
        <div className="flex-1 p-6 overflow-auto">
          <ProfileSkeleton />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      <AppSidebar />
      <div className="flex-1 p-6 overflow-auto">
        <div className="max-w-2xl mx-auto space-y-6">
          {/* Header with avatar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {stravaUser?.avatarUrl ? (
                <img
                  src={stravaUser.avatarUrl}
                  alt={stravaUser.displayName}
                  className="w-12 h-12 rounded-full border"
                />
              ) : (
                <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-lg font-semibold">
                  {athlete.name.charAt(0).toUpperCase()}
                </div>
              )}
              <div>
                <h1 className="text-2xl font-bold">
                  {stravaUser?.displayName || athlete.name}
                </h1>
                {stravaUser?.stravaId && (
                  <p className="text-xs text-muted-foreground">Strava connected</p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={handleAutoDetect} disabled={detecting}>
                {detecting ? "Syncing..." : "Sync Strava"}
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>
          </div>

          {/* Strava auto-detected values banner */}
          {(athlete.stravaFtp || athlete.stravaVdot || athlete.stravaCss || athlete.stravaCda) && (
            <Card className="border-blue-200 bg-blue-50/50">
              <CardContent className="py-3">
                <p className="text-sm font-medium mb-2">Strava auto-detected values:</p>
                <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                  {athlete.stravaFtp && <span>FTP: {athlete.stravaFtp}W</span>}
                  {athlete.stravaVdot && <span>VDOT: {athlete.stravaVdot}</span>}
                  {athlete.stravaCss && <span>CSS: {athlete.stravaCss}s/100m</span>}
                  {athlete.stravaCda && <span>CdA: {athlete.stravaCda}m²</span>}
                </div>
              </CardContent>
            </Card>
          )}

          {/* General */}
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label>Name</Label>
                  <Input
                    value={athlete.name}
                    onChange={(e) => update("name", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Weight (kg)</Label>
                  <Input
                    type="number"
                    value={athlete.weight}
                    onChange={(e) => update("weight", Number(e.target.value))}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Height (cm)</Label>
                  <Input
                    type="number"
                    value={athlete.height || ""}
                    onChange={(e) =>
                      update("height", e.target.value ? Number(e.target.value) : 0)
                    }
                    placeholder="Optional"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Tabs defaultValue="cycling">
            <TabsList className="w-full flex-wrap h-auto gap-1">
              <TabsTrigger value="cycling" className="flex-1">Cycling</TabsTrigger>
              <TabsTrigger value="running" className="flex-1">Running</TabsTrigger>
              <TabsTrigger value="swimming" className="flex-1">Swimming</TabsTrigger>
              <TabsTrigger value="triathlon" className="flex-1">Triathlon</TabsTrigger>
              <TabsTrigger value="training" className="flex-1">Training</TabsTrigger>
            </TabsList>

            <TabsContent value="cycling">
              <Card>
                <CardHeader>
                  <CardTitle>Cycling Parameters</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>FTP (W)</Label>
                      <Input
                        type="number"
                        value={athlete.ftp ?? ""}
                        onChange={(e) => update("ftp", e.target.value ? Number(e.target.value) : 0)}
                        placeholder="e.g. 250"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Bike weight (kg)</Label>
                      <Input
                        type="number"
                        value={athlete.bikeWeight}
                        onChange={(e) => update("bikeWeight", Number(e.target.value))}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>CdA (m²)</Label>
                      <Input
                        type="number"
                        step="0.01"
                        value={athlete.cda ?? ""}
                        onChange={(e) => update("cda", e.target.value ? Number(e.target.value) : 0)}
                        placeholder="e.g. 0.32"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Crr</Label>
                      <Input
                        type="number"
                        step="0.001"
                        value={athlete.crr}
                        onChange={(e) => update("crr", Number(e.target.value))}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Drivetrain efficiency</Label>
                      <Input
                        type="number"
                        step="0.01"
                        value={athlete.efficiency}
                        onChange={(e) => update("efficiency", Number(e.target.value))}
                      />
                    </div>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="running">
              <Card>
                <CardHeader>
                  <CardTitle>Running Parameters</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>VDOT</Label>
                      <Input
                        type="number"
                        value={athlete.vdot || ""}
                        onChange={(e) =>
                          update("vdot", e.target.value ? Number(e.target.value) : 0)
                        }
                        placeholder="30-85"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>VMA (km/h)</Label>
                      <Input
                        type="number"
                        step="0.5"
                        value={athlete.vma}
                        onChange={(e) => update("vma", Number(e.target.value))}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>VO2max (ml/kg/min)</Label>
                      <Input
                        type="number"
                        value={athlete.vo2max || ""}
                        onChange={(e) =>
                          update("vo2max", e.target.value ? Number(e.target.value) : 0)
                        }
                        placeholder="Optional"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>FC max (bpm)</Label>
                      <Input
                        type="number"
                        value={athlete.fcMax || ""}
                        onChange={(e) =>
                          update("fcMax", e.target.value ? Number(e.target.value) : 0)
                        }
                        placeholder="Optional"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Lactate threshold (bpm)</Label>
                      <Input
                        type="number"
                        value={athlete.lactateThreshold || ""}
                        onChange={(e) =>
                          update("lactateThreshold", e.target.value ? Number(e.target.value) : 0)
                        }
                        placeholder="Optional"
                      />
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground mt-3">
                    VDOT is used for road running simulation. VMA is used for trail simulation. Use Strava auto-detect to estimate from your best performances.
                  </p>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="swimming">
              <Card>
                <CardHeader>
                  <CardTitle>Swimming Parameters</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>CSS (sec/100m)</Label>
                      <Input
                        type="number"
                        value={athlete.css || ""}
                        onChange={(e) =>
                          update("css", e.target.value ? Number(e.target.value) : 0)
                        }
                        placeholder="e.g. 95"
                      />
                    </div>
                    <div className="flex items-end pb-2">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={athlete.swimHasWetsuit}
                          onChange={(e) => update("swimHasWetsuit", e.target.checked)}
                          className="rounded border-input"
                        />
                        Has wetsuit
                      </label>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground mt-3">
                    CSS (Critical Swim Speed) is your sustainable pace. Test: (T400 - T200) / 2 gives pace/100m.
                  </p>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="triathlon">
              <Card>
                <CardHeader>
                  <CardTitle>Triathlon Parameters</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>T1 time (sec)</Label>
                      <Input
                        type="number"
                        value={athlete.t1Time}
                        onChange={(e) => update("t1Time", Number(e.target.value))}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>T2 time (sec)</Label>
                      <Input
                        type="number"
                        value={athlete.t2Time}
                        onChange={(e) => update("t2Time", Number(e.target.value))}
                      />
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground mt-3">
                    Set your swim, bike and run parameters in their respective tabs. T1 = swim-to-bike, T2 = bike-to-run transition times.
                  </p>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="training">
              <div className="space-y-4">
                <TrainingSummary data={pmcData} />
                {pmcData.length > 0 && <PMCChart data={pmcData} />}
                {records.length > 0 && <PersonalRecordsTable records={records} />}
                {pmcData.length === 0 && records.length === 0 && (
                  <Card>
                    <CardContent className="py-8 text-center text-muted-foreground">
                      <p>No training data yet. Sync your Strava activities to see your fitness metrics, fatigue, and personal records.</p>
                    </CardContent>
                  </Card>
                )}
              </div>
            </TabsContent>
          </Tabs>

          {/* Training Zones */}
          <ZoneDisplay athlete={athlete} />

          {/* Race Predictions */}
          <RacePredictions athlete={athlete} />

          {/* Lactate Testing */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Lactate Testing</CardTitle>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setLactateWizardOpen(true)}
                >
                  New Test
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <LactateHistory
                onThresholdApplied={(lt, hr) => {
                  if (hr && athlete) {
                    update("lactateThreshold", hr);
                    toast.success(`Lactate threshold HR updated to ${hr} bpm`);
                  }
                }}
              />
            </CardContent>
          </Card>

          <LactateProtocolWizard
            open={lactateWizardOpen}
            onOpenChange={setLactateWizardOpen}
            onSaved={() => {
              toast.success("Lactate test saved");
            }}
            onThresholdApplied={(lt, hr) => {
              if (hr && athlete) {
                update("lactateThreshold", hr);
                toast.success(`Lactate threshold HR updated to ${hr} bpm`);
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}
