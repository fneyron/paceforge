import type { Zone } from "@/lib/zones";

interface Props {
  zones: Zone[];
  unit?: string;
}

export function ZoneBar({ zones, unit }: Props) {
  // Calculate total range for proportional widths
  const validZones = zones.filter((z) => z.max !== Infinity);
  const maxVal = validZones.length > 0
    ? Math.max(...validZones.map((z) => z.max))
    : 100;

  return (
    <div className="space-y-1">
      <div className="flex h-6 rounded-md overflow-hidden">
        {zones.map((zone) => {
          const width = zone.max === Infinity
            ? 10 // small segment for "infinity" zones
            : ((zone.max - zone.min) / maxVal) * 100;

          return (
            <div
              key={zone.number}
              className="flex items-center justify-center text-[10px] font-medium text-white truncate"
              style={{
                backgroundColor: zone.color,
                width: `${Math.max(width, 5)}%`,
              }}
              title={`Z${zone.number} ${zone.name}: ${zone.min}${zone.max === Infinity ? "+" : `–${zone.max}`}${unit ? ` ${unit}` : ""}`}
            >
              Z{zone.number}
            </div>
          );
        })}
      </div>
    </div>
  );
}
