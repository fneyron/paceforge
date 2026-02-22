import type { RoutePoint, SplitResult } from "@/types/route";

/**
 * Generate a TCX (Training Center XML) file.
 * TCX is widely supported by Garmin, Wahoo, and other platforms.
 */
export function generateTCX(
  name: string,
  points: RoutePoint[],
  splits?: SplitResult[],
  sport: string = "Biking"
): string {
  const startTime = new Date().toISOString();

  // Sample points for reasonable file size
  const sampleStep = Math.max(1, Math.floor(points.length / 2000));
  const sampled: RoutePoint[] = [];
  for (let i = 0; i < points.length; i += sampleStep) {
    sampled.push(points[i]);
  }
  if (sampled[sampled.length - 1] !== points[points.length - 1]) {
    sampled.push(points[points.length - 1]);
  }

  const tcxSport = sport.toLowerCase().includes("run")
    ? "Running"
    : sport.toLowerCase().includes("swim")
    ? "Other"
    : "Biking";

  let xml = `<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
  xmlns="http://www.garmin.com/xmlschemas/TrainingCenter/v2"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.garmin.com/xmlschemas/TrainingCenter/v2 http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd">
  <Courses>
    <Course>
      <Name>${escapeXml(name)}</Name>
      <Lap>
        <TotalTimeSeconds>${splits ? splits.reduce((s, sp) => s + sp.time, 0).toFixed(0) : "0"}</TotalTimeSeconds>
        <DistanceMeters>${points[points.length - 1].distance.toFixed(1)}</DistanceMeters>
        <BeginPosition>
          <LatitudeDegrees>${points[0].lat}</LatitudeDegrees>
          <LongitudeDegrees>${points[0].lon}</LongitudeDegrees>
        </BeginPosition>
        <EndPosition>
          <LatitudeDegrees>${points[points.length - 1].lat}</LatitudeDegrees>
          <LongitudeDegrees>${points[points.length - 1].lon}</LongitudeDegrees>
        </EndPosition>
        <Intensity>Active</Intensity>
      </Lap>
      <Track>`;

  // Estimate time per point from splits
  const totalDist = points[points.length - 1].distance;
  const totalTime = splits ? splits.reduce((s, sp) => s + sp.time, 0) : totalDist / 5; // default 5 m/s
  const baseTime = new Date(startTime).getTime();

  for (const pt of sampled) {
    const elapsed = (pt.distance / totalDist) * totalTime;
    const time = new Date(baseTime + elapsed * 1000).toISOString();

    xml += `
        <Trackpoint>
          <Time>${time}</Time>
          <Position>
            <LatitudeDegrees>${pt.lat}</LatitudeDegrees>
            <LongitudeDegrees>${pt.lon}</LongitudeDegrees>
          </Position>
          <AltitudeMeters>${pt.ele.toFixed(1)}</AltitudeMeters>
          <DistanceMeters>${pt.distance.toFixed(1)}</DistanceMeters>
        </Trackpoint>`;
  }

  xml += `
      </Track>
    </Course>
  </Courses>
</TrainingCenterDatabase>`;

  return xml;
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
