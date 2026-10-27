import { useMemo } from 'react';
import { CATEGORY_COLOR_BY_KEY } from '../../hooks';

function nearestShotIdx(screenshots, targetMs) {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < screenshots.length; i++) {
    const d = Math.abs(new Date(screenshots[i].timestamp).getTime() - targetMs);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
}

/**
 * Per-hour mini cards. For each hour with activity, shows:
 *   - top app of the hour
 *   - non-idle minutes attributed
 *   - dominant category color bar
 * Click → seek replay to the first screenshot in that hour (or the nearest
 * one, since capture skips unchanged frames and an hour can have none).
 *
 * Props:
 *   screenshots
 *   sessions  — chronological blocks (/api/day `timeline`)
 *   currentIdx
 *   onSeek(idx)
 */
export default function HourGrid({ screenshots, sessions, currentIdx, onSeek }) {
  // Map hour → {minutes by category, topAppMinutes, firstScreenshotIdx}
  const hours = useMemo(() => {
    if (!screenshots?.length) return [];
    const dayDate = new Date(screenshots[0].timestamp);
    dayDate.setHours(0, 0, 0, 0);
    const dayStart = dayDate.getTime();

    // Index screenshots by hour
    const firstShotByHour = {};
    for (let i = 0; i < screenshots.length; i++) {
      const h = new Date(screenshots[i].timestamp).getHours();
      if (!(h in firstShotByHour)) firstShotByHour[h] = i;
    }

    // Distribute session minutes across the hours they cover.
    const byHour = new Map(); // hour → {catSeconds: {cat: secs}, appSeconds: {app: secs}}
    for (const s of (sessions || [])) {
      if (s.category === 'idle') continue;
      const start = new Date(s.start_time).getTime();
      const end = new Date(s.end_time).getTime();
      if (end <= dayStart) continue;
      // Walk hour windows
      let cur = Math.max(start, dayStart);
      while (cur < end) {
        const hour = new Date(cur).getHours();
        const nextHourStart = new Date(cur);
        nextHourStart.setMinutes(0, 0, 0);
        nextHourStart.setHours(hour + 1);
        const slice = Math.min(end, nextHourStart.getTime()) - cur;
        const secs = Math.floor(slice / 1000);
        if (!byHour.has(hour)) byHour.set(hour, { catSeconds: {}, appSeconds: {} });
        const b = byHour.get(hour);
        b.catSeconds[s.category] = (b.catSeconds[s.category] || 0) + secs;
        b.appSeconds[s.app_name] = (b.appSeconds[s.app_name] || 0) + secs;
        cur = nextHourStart.getTime();
      }
    }

    const out = [];
    for (const [h, b] of [...byHour.entries()].sort((a, b) => a[0] - b[0])) {
      const totalSecs = Object.values(b.catSeconds).reduce((a, b) => a + b, 0);
      if (totalSecs < 60) continue;
      const topCat = Object.entries(b.catSeconds).sort((a, b) => b[1] - a[1])[0]?.[0];
      const topApp = Object.entries(b.appSeconds).sort((a, b) => b[1] - a[1])[0]?.[0];
      out.push({
        hour: h,
        totalMin: Math.round(totalSecs / 60),
        topCat,
        topApp,
        firstIdx: firstShotByHour[h] ?? nearestShotIdx(screenshots, dayStart + h * 3600_000),
      });
    }
    return out;
  }, [screenshots, sessions]);

  const currentHour = screenshots?.[currentIdx]
    ? new Date(screenshots[currentIdx].timestamp).getHours()
    : null;

  if (!hours.length) return null;

  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2">
      {hours.map(({ hour, totalMin, topCat, topApp, firstIdx }) => {
        const color = CATEGORY_COLOR_BY_KEY[topCat] || '#6b7280';
        const isCurrent = hour === currentHour;
        return (
          <button
            key={hour}
            onClick={() => onSeek?.(firstIdx)}
            className={`text-left rounded-lg border p-2 transition-colors ${
              isCurrent
                ? 'border-[var(--color-amber)] bg-[var(--color-surface-2)]'
                : 'border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-[var(--color-text-muted)]">
                {String(hour).padStart(2, '0')}:00
              </span>
              <span className="w-2 h-2 rounded-full" style={{ background: color }} />
            </div>
            <div className="text-xs font-medium truncate" title={topApp}>
              {topApp || '—'}
            </div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{totalMin}m</div>
          </button>
        );
      })}
    </div>
  );
}
