import { useMemo, useRef } from 'react';
import { CATEGORY_COLOR_BY_KEY } from '../../hooks';

/**
 * 24h horizontal ribbon. Each session draws a colored segment proportional
 * to its duration. The cursor marker shows where the current screenshot
 * falls. Clicking a segment (or any point) seeks playback.
 *
 * Props:
 *   sessions      — [{start_time, end_time, category, app_name, duration_seconds}]
 *   screenshots   — sorted by timestamp [{id, timestamp, ...}]
 *   currentIdx    — index into screenshots
 *   onSeek(idx)   — called with the screenshot index closest to a click
 *   filterFn?     — optional predicate(screenshot) → bool to mark filtered-out segments dimmed
 */
export default function TimelineRibbon({ sessions, screenshots, currentIdx, onSeek }) {
  const wrapRef = useRef(null);

  // Day bounds: 00:00 → 24:00 of the date in the screenshots.
  const dayStart = useMemo(() => {
    if (!screenshots?.length) return null;
    const d = new Date(screenshots[0].timestamp);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }, [screenshots]);

  const DAY_MS = 24 * 60 * 60 * 1000;

  // Convert sessions to %-positioned segments.
  const segments = useMemo(() => {
    if (!sessions?.length || dayStart === null) return [];
    return sessions.map((s) => {
      const start = new Date(s.start_time).getTime();
      const end = new Date(s.end_time).getTime();
      const left = Math.max(0, ((start - dayStart) / DAY_MS) * 100);
      const width = Math.max(0.15, ((end - start) / DAY_MS) * 100);
      return {
        left, width,
        color: CATEGORY_COLOR_BY_KEY[s.category] || '#6b7280',
        label: `${s.app_name} • ${s.category}`,
        startMs: start,
      };
    });
  }, [sessions, dayStart]);

  // Active hours range (min → max screenshot time) so we can dim the
  // hours that had no activity.
  const activeBounds = useMemo(() => {
    if (!screenshots?.length || dayStart === null) return null;
    const first = new Date(screenshots[0].timestamp).getTime();
    const last = new Date(screenshots[screenshots.length - 1].timestamp).getTime();
    return {
      leftPct: ((first - dayStart) / DAY_MS) * 100,
      rightPct: ((last - dayStart) / DAY_MS) * 100,
    };
  }, [screenshots, dayStart]);

  // Marker position from current screenshot
  const markerLeftPct = useMemo(() => {
    if (!screenshots?.length || dayStart === null) return 0;
    const ts = new Date(screenshots[currentIdx]?.timestamp || screenshots[0].timestamp).getTime();
    return ((ts - dayStart) / DAY_MS) * 100;
  }, [currentIdx, screenshots, dayStart]);

  function handleClick(e) {
    const rect = wrapRef.current.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    const targetMs = dayStart + pct * DAY_MS;
    // Find closest screenshot by timestamp
    let bestIdx = 0;
    let bestDist = Infinity;
    screenshots.forEach((s, i) => {
      const d = Math.abs(new Date(s.timestamp).getTime() - targetMs);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    });
    onSeek?.(bestIdx);
  }

  if (!screenshots?.length) return null;

  return (
    <div className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-3 select-none">
      {/* Hour labels */}
      <div className="relative h-3 mb-1 text-[10px] text-[var(--color-text-muted)] font-mono">
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => (
          <span key={h} className="absolute" style={{ left: `${(h / 24) * 100}%` }}>
            {String(h).padStart(2, '0')}
          </span>
        ))}
        <span className="absolute right-0">24</span>
      </div>

      {/* Bars */}
      <div
        ref={wrapRef}
        onClick={handleClick}
        className="relative h-6 rounded-md cursor-pointer overflow-hidden bg-[var(--color-bg)]"
      >
        {/* Active range backdrop (very subtle) */}
        {activeBounds && (
          <div
            className="absolute inset-y-0 bg-[var(--color-surface-2)]"
            style={{
              left: `${activeBounds.leftPct}%`,
              width: `${Math.max(0.1, activeBounds.rightPct - activeBounds.leftPct)}%`,
            }}
          />
        )}

        {/* Session segments */}
        {segments.map((seg, i) => (
          <div
            key={i}
            className="absolute top-1 bottom-1 rounded-sm"
            style={{ left: `${seg.left}%`, width: `${seg.width}%`, background: seg.color, opacity: 0.85 }}
            title={seg.label}
          />
        ))}

        {/* Playback marker */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-white shadow-[0_0_6px_rgba(255,255,255,0.7)] pointer-events-none transition-[left] duration-100"
          style={{ left: `${markerLeftPct}%` }}
        />
      </div>

      {/* Category legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[10px] text-[var(--color-text-muted)]">
        {[
          ['coding', 'Deep Work'],
          ['communication', 'Comm'],
          ['browsing', 'Browse'],
          ['writing', 'Write'],
          ['learning', 'Learn'],
          ['entertainment', 'Entertainment'],
        ].map(([k, label]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm" style={{ background: CATEGORY_COLOR_BY_KEY[k] }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
