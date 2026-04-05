import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useApi, todayStr, formatMinutes } from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';

const RANGE_OPTIONS = [
  { label: '3 months', days: 90 },
  { label: '6 months', days: 180 },
  { label: '1 year', days: 365 },
];

export default function Heatmap() {
  const [days, setDays] = useState(180);
  const { data, loading, error, refetch } = useApi(() => api.getHeatmap(days), [days]);
  const navigate = useNavigate();

  if (loading) return <Loading message="Building your heatmap..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const entries = data?.days || [];

  // Build a map: date -> entry
  const dateMap = {};
  for (const e of entries) {
    dateMap[e.date] = e;
  }

  // Build weeks grid (columns) — GitHub style
  const today = new Date(todayStr() + 'T12:00:00');
  const start = new Date(today);
  start.setDate(start.getDate() - days + 1);
  // Align to Sunday
  start.setDate(start.getDate() - start.getDay());

  const weeks = [];
  let current = new Date(start);
  while (current <= today) {
    const week = [];
    for (let d = 0; d < 7; d++) {
      const dateStr = current.toISOString().split('T')[0];
      const entry = dateMap[dateStr];
      week.push({
        date: dateStr,
        dayOfWeek: current.getDay(),
        score: entry?.focus_score ?? null,
        tracked: entry?.total_tracked_minutes ?? 0,
        focus: entry?.focus_minutes ?? 0,
        isFuture: current > today,
      });
      current.setDate(current.getDate() + 1);
    }
    weeks.push(week);
  }

  // Stats
  const tracked = entries.filter((e) => e.total_tracked_minutes > 0);
  const avgScore = tracked.length > 0
    ? Math.round(tracked.reduce((s, e) => s + e.focus_score, 0) / tracked.length)
    : 0;
  const bestDay = tracked.reduce((best, e) => (e.focus_score > (best?.focus_score || 0) ? e : best), null);
  const streakDays = computeStreak(entries);

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Focus Heatmap</h1>
        <div className="flex items-center gap-1 bg-[var(--color-surface)] rounded-lg p-1 border border-[var(--color-border)]">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              onClick={() => setDays(opt.days)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                days === opt.days
                  ? 'bg-[var(--color-amber)]/15 text-[var(--color-amber)]'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </header>

      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-4">
        <MiniStat label="Days Tracked" value={tracked.length} />
        <MiniStat label="Avg Focus Score" value={avgScore} />
        <MiniStat label="Best Day" value={bestDay ? `${bestDay.focus_score}` : '—'} sub={bestDay?.date} />
        <MiniStat label="Current Streak" value={`${streakDays}d`} />
      </div>

      {/* Heatmap grid */}
      <section className="bg-[var(--color-surface)] rounded-xl p-5 border border-[var(--color-border)] overflow-x-auto">
        <div className="flex gap-[3px] min-w-fit">
          {/* Day labels */}
          <div className="flex flex-col gap-[3px] mr-2 text-[10px] text-[var(--color-text-muted)]">
            <div className="h-[13px]" /> {/* spacer for month row */}
            {['', 'Mon', '', 'Wed', '', 'Fri', ''].map((d, i) => (
              <div key={i} className="h-[13px] flex items-center">{d}</div>
            ))}
          </div>

          {/* Month labels row + cells */}
          <div>
            {/* Month labels */}
            <div className="flex gap-[3px] mb-[3px]">
              {weeks.map((week, wi) => {
                // Show month label on the first week of a new month
                const firstDay = week.find((d) => d.dayOfWeek === 0) || week[0];
                const monthDay = new Date(firstDay.date + 'T12:00:00');
                const showLabel = wi === 0 || monthDay.getDate() <= 7;
                return (
                  <div key={wi} className="w-[13px] text-[10px] text-[var(--color-text-muted)]">
                    {showLabel ? monthDay.toLocaleDateString('en-US', { month: 'short' }) : ''}
                  </div>
                );
              })}
            </div>

            {/* Grid: 7 rows (days), N columns (weeks) */}
            {[0, 1, 2, 3, 4, 5, 6].map((dow) => (
              <div key={dow} className="flex gap-[3px]">
                {weeks.map((week, wi) => {
                  const cell = week[dow];
                  if (!cell || cell.isFuture) {
                    return <div key={wi} className="w-[13px] h-[13px]" />;
                  }
                  return (
                    <button
                      key={wi}
                      onClick={() => navigate(`/?date=${cell.date}`)}
                      className="w-[13px] h-[13px] rounded-sm transition-colors hover:ring-1 hover:ring-[var(--color-amber)]"
                      style={{ backgroundColor: scoreToColor(cell.score) }}
                      title={`${cell.date}: Score ${cell.score ?? '—'}, ${formatMinutes(cell.focus)}  focus`}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        {/* Legend */}
        <div className="flex items-center gap-2 mt-4 text-[10px] text-[var(--color-text-muted)]">
          <span>Less</span>
          {[null, 20, 40, 60, 80].map((s) => (
            <div
              key={s ?? 'none'}
              className="w-[13px] h-[13px] rounded-sm"
              style={{ backgroundColor: scoreToColor(s) }}
            />
          ))}
          <span>More focused</span>
        </div>
      </section>
    </div>
  );
}

function MiniStat({ label, value, sub }) {
  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="text-xs text-[var(--color-text-muted)] mb-1">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-[var(--color-text-muted)] mt-0.5">{sub}</div>}
    </div>
  );
}

function scoreToColor(score) {
  if (score === null || score === undefined) return '#161616';
  if (score >= 80) return '#f59e0b';
  if (score >= 60) return '#b45309';
  if (score >= 40) return '#78350f';
  if (score >= 20) return '#451a03';
  if (score > 0) return '#2a1000';
  return '#1a1a1a';
}

function computeStreak(entries) {
  // Sort descending by date
  const sorted = [...entries]
    .filter((e) => e.total_tracked_minutes > 0)
    .sort((a, b) => b.date.localeCompare(a.date));

  if (sorted.length === 0) return 0;

  let streak = 0;
  const today = todayStr();
  let expected = today;

  for (const e of sorted) {
    if (e.date === expected) {
      streak++;
      // Go back one day
      const d = new Date(expected + 'T12:00:00');
      d.setDate(d.getDate() - 1);
      expected = d.toISOString().split('T')[0];
    } else if (e.date < expected) {
      break;
    }
  }

  return streak;
}
