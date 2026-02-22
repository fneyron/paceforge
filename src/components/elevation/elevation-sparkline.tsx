interface Props {
  elevations: number[];
  width?: number;
  height?: number;
  className?: string;
}

export function ElevationSparkline({
  elevations,
  width = 64,
  height = 24,
  className,
}: Props) {
  if (!elevations || elevations.length < 2) return null;

  const min = Math.min(...elevations);
  const max = Math.max(...elevations);
  const range = max - min || 1;

  const padding = 1;
  const innerH = height - padding * 2;
  const step = (width - padding * 2) / (elevations.length - 1);

  const points = elevations.map((ele, i) => {
    const x = padding + i * step;
    const y = padding + innerH - ((ele - min) / range) * innerH;
    return `${x},${y}`;
  });

  // Close the polygon at bottom
  const pathD = `M${points[0]} ${points.slice(1).map((p) => `L${p}`).join(" ")} L${padding + (elevations.length - 1) * step},${height} L${padding},${height} Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
    >
      <path
        d={pathD}
        fill="currentColor"
        opacity={0.15}
      />
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        opacity={0.5}
      />
    </svg>
  );
}
