// 7×24 weekday × hour-of-day session activity heatmap.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useActivityHeatmap } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';

export function HeatmapGrid() {
  const [range, setRange] = useState<Range>('30d');
  const { data, isLoading } = useActivityHeatmap(range);

  const grid = data?.grid;
  const peak = data?.peak ?? 0;
  const weekdays = data?.weekdays ?? ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  // Color scale: 0 = empty surface, peak = full accent.
  const cellColor = (n: number): string => {
    if (peak === 0 || n === 0) return 'bg-surface-3/40';
    const t = n / peak;
    if (t < 0.2)  return 'bg-accent-blue/15';
    if (t < 0.4)  return 'bg-accent-blue/30';
    if (t < 0.6)  return 'bg-accent-blue/50';
    if (t < 0.8)  return 'bg-accent-blue/70';
    return 'bg-accent-blue';
  };

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Activity heatmap</Kicker>
          <CardTitle>Sessions by weekday × hour</CardTitle>
          <CardDescription>
            Local time. Darker = more sessions. {data && (
              <>peak <span className="font-mono text-text">{peak}</span> · total <span className="font-mono text-text">{data.total}</span></>
            )}
          </CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading || !grid ? (
          <div className="space-y-1">
            {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-5 w-full" />)}
          </div>
        ) : data?.total === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no sessions in this range</div>
        ) : (
          <div className="overflow-x-auto">
            <div className="min-w-[640px]">
              {/* Hour labels */}
              <div className="flex items-center text-[10px] font-mono text-text-subtle pl-9 mb-1">
                {Array.from({ length: 24 }).map((_, h) => (
                  <span key={h} className="flex-1 text-center">{h % 3 === 0 ? `${h}h` : ''}</span>
                ))}
              </div>
              {/* Rows */}
              <div className="space-y-[2px]">
                {grid.map((row, dow) => (
                  <div key={dow} className="flex items-center gap-1">
                    <span className="kicker w-7 text-right pr-1">{weekdays[dow]}</span>
                    <div className="flex-1 flex gap-[2px]">
                      {row.map((n, hr) => (
                        <div
                          key={hr}
                          className={`flex-1 h-5 rounded-sm ${cellColor(n)} relative group cursor-default`}
                          title={`${weekdays[dow]} ${hr}:00 · ${n} session${n === 1 ? '' : 's'}`}
                        >
                          {n > 0 && (
                            <span className="absolute inset-0 flex items-center justify-center text-[9px] font-mono opacity-0 group-hover:opacity-100 transition-opacity text-white pointer-events-none">
                              {n}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {/* Legend */}
              <div className="flex items-center gap-2 mt-3 text-[10px] font-mono text-text-subtle">
                <span>less</span>
                <div className="flex gap-[2px]">
                  {['bg-surface-3/40', 'bg-accent-blue/15', 'bg-accent-blue/30', 'bg-accent-blue/50', 'bg-accent-blue/70', 'bg-accent-blue'].map((c, i) => (
                    <div key={i} className={`w-3 h-3 rounded-sm ${c}`} />
                  ))}
                </div>
                <span>more</span>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
