import { useMemo } from 'react';
import { Target, Sunrise, Sunset, Coffee } from 'lucide-react';
import { formatTime } from '../../hooks';

const PRODUCTIVE_CATS = new Set(['coding', 'writing', 'learning']);

/**
 * Derive notable moments from sessions + screenshots, render as chips.
 * Each chip seeks to the screenshot index closest to that moment.
 *
 * Props:
 *   sessions
 *   screenshots
 *   onSeek(idx)
 */
export default function QuickJumps({ sessions, screenshots, onSeek }) {
  const jumps = useMemo(() => {
    if (!screenshots?.length || !sessions?.length) return [];

    // 1. Longest productive session
    const productiveSessions = sessions.filter((s) => PRODUCTIVE_CATS.has(s.category));
    let longest = null;
    for (const s of productiveSessions) {
      if (!longest || s.duration_seconds > longest.duration_seconds) longest = s;
    }

    // 2. First / last activity
    const first = screenshots[0];
    const last = screenshots[screenshots.length - 1];

    // 3. Biggest idle gap between consecutive screenshots
    let biggestGap = { gapMin: 0, before: null, after: null };
    for (let i = 1; i < screenshots.length; i++) {
      const prevT = new Date(screenshots[i - 1].timestamp).getTime();
      const curT = new Date(screenshots[i].timestamp).getTime();
      const gapMin = (curT - prevT) / 60_000;
      if (gapMin > biggestGap.gapMin) {
        biggestGap = { gapMin, before: i - 1, after: i };
      }
    }

    const out = [];
    out.push({
      key: 'first',
      icon: Sunrise,
      label: 'First activity',
      time: formatTime(first.timestamp),
      idx: 0,
    });
    if (longest) {
      const idx = nearestIdx(screenshots, new Date(longest.start_time).getTime());
      out.push({
        key: 'longest',
        icon: Target,
        label: `Longest focus (${minutes(longest.duration_seconds)})`,
        time: formatTime(longest.start_time),
        idx,
      });
    }
    if (biggestGap.gapMin >= 15 && biggestGap.before !== null) {
      out.push({
        key: 'gap',
        icon: Coffee,
        label: `Biggest gap (${Math.round(biggestGap.gapMin)}m)`,
        time: formatTime(screenshots[biggestGap.before].timestamp),
        idx: biggestGap.before,
      });
    }
    out.push({
      key: 'last',
      icon: Sunset,
      label: 'Last activity',
      time: formatTime(last.timestamp),
      idx: screenshots.length - 1,
    });
    return out;
  }, [sessions, screenshots]);

  if (!jumps.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {jumps.map(({ key, icon: Icon, label, time, idx }) => (
        <button
          key={key}
          onClick={() => onSeek?.(idx)}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--color-surface)] border border-[var(--color-border)] hover:border-[var(--color-amber)] text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          <Icon className="w-3 h-3" />
          <span>{label}</span>
          <span className="text-[var(--color-amber)] font-mono">{time}</span>
        </button>
      ))}
    </div>
  );
}

function minutes(sec) {
  const m = Math.round(sec / 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const r = m % 60;
    return r ? `${h}h${r}m` : `${h}h`;
  }
  return `${m}m`;
}

function nearestIdx(screenshots, targetMs) {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < screenshots.length; i++) {
    const d = Math.abs(new Date(screenshots[i].timestamp).getTime() - targetMs);
    if (d < bestDist) { bestDist = d; best = i; }
  }
  return best;
}
