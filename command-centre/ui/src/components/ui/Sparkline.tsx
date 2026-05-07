// Pure-SVG sparkline. No dependencies. Renders a single polyline + an
// optional area fill, scaled to fit the viewport. All-zero series shows
// a flat baseline so the tile doesn't visually disappear.
import { cn } from '@/lib/cn';

export function Sparkline({
  data, width = 80, height = 24, stroke = 'currentColor', fill = 'none', className,
}: {
  data: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  className?: string;
}) {
  if (!data.length) {
    return <svg width={width} height={height} className={className} aria-hidden />;
  }

  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const step = data.length > 1 ? width / (data.length - 1) : width;
  // Top padding so the line doesn't touch the edge.
  const PAD_Y = 2;
  const usableH = height - PAD_Y * 2;

  const pts = data.map((v, i) => {
    const x = i * step;
    const y = PAD_Y + (usableH - ((v - min) / range) * usableH);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const polyline = pts.join(' ');
  // Closed polygon for area fill (only used when fill !== 'none').
  const area = fill !== 'none'
    ? `0,${height} ${polyline} ${width},${height}`
    : '';

  return (
    <svg
      width={width} height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('overflow-visible', className)}
      aria-hidden
    >
      {area && <polygon points={area} fill={fill} />}
      <polyline
        points={polyline}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
