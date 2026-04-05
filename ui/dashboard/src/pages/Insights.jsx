import { useState } from 'react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { api } from '../api';
import { useApi, formatMinutes, CATEGORY_COLORS } from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';

const RANGE_OPTIONS = [
  { label: '7 days', days: 7 },
  { label: '14 days', days: 14 },
  { label: '30 days', days: 30 },
];

export default function Insights() {
  const [days, setDays] = useState(30);
  const { data, loading, error, refetch } = useApi(() => api.getInsights(days), [days]);

  if (loading) return <Loading message="Crunching your patterns..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const { focus_scores, app_usage, category_totals, daily_hours, comparison } = data;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Insights</h1>
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

      {/* Comparison cards */}
      {comparison && (
        <div className="grid grid-cols-3 gap-4">
          <ComparisonCard
            label="Avg Focus Score"
            current={comparison.current_avg_score}
            previous={comparison.previous_avg_score}
          />
          <ComparisonCard
            label="Avg Focus Hours"
            current={comparison.current_avg_focus_hours}
            previous={comparison.previous_avg_focus_hours}
            format={(v) => `${v.toFixed(1)}h`}
          />
          <ComparisonCard
            label="Avg App Switches"
            current={comparison.current_avg_switches}
            previous={comparison.previous_avg_switches}
            lowerIsBetter
          />
        </div>
      )}

      {/* Focus score over time */}
      {focus_scores?.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Focus Score Trend</h2>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={focus_scores}>
              <XAxis
                dataKey="date"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(d) => new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              />
              <YAxis domain={[0, 100]} tick={{ fill: '#888', fontSize: 11 }} axisLine={false} tickLine={false} width={30} />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                      <div className="font-medium">{d.date}</div>
                      <div>Score: {d.score}</div>
                    </div>
                  );
                }}
              />
              <Line
                type="monotone"
                dataKey="score"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={{ r: 2, fill: '#f59e0b' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Category breakdown */}
        {category_totals?.length > 0 && (
          <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
            <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Time by Category</h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={category_totals} layout="vertical">
                <XAxis type="number" hide />
                <YAxis
                  dataKey="name"
                  type="category"
                  tick={{ fill: '#888', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={90}
                />
                <Tooltip
                  content={({ payload }) => {
                    if (!payload?.[0]) return null;
                    const d = payload[0].payload;
                    return (
                      <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                        {d.name}: {formatMinutes(d.minutes)}
                      </div>
                    );
                  }}
                />
                <Bar dataKey="minutes" radius={[0, 4, 4, 0]}>
                  {category_totals.map((c) => (
                    <Cell key={c.name} fill={CATEGORY_COLORS[c.name] || '#6b7280'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </section>
        )}

        {/* Top apps */}
        {app_usage?.length > 0 && (
          <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
            <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Top Apps</h2>
            <div className="space-y-3">
              {app_usage.slice(0, 10).map((app, i) => {
                const maxMins = app_usage[0].minutes;
                const pct = maxMins > 0 ? (app.minutes / maxMins) * 100 : 0;
                return (
                  <div key={app.name}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="truncate">{app.name}</span>
                      <span className="text-[var(--color-text-muted)] shrink-0 ml-2">
                        {formatMinutes(app.minutes)}
                      </span>
                    </div>
                    <div className="h-1.5 bg-[var(--color-surface-3)] rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${pct}%`,
                          backgroundColor: i === 0 ? '#f59e0b' : i < 3 ? '#78350f' : '#333',
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>

      {/* Daily hours heatmap-style chart */}
      {daily_hours?.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Daily Tracked Hours</h2>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={daily_hours}>
              <XAxis
                dataKey="date"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(d) => new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                interval={Math.max(Math.floor(daily_hours.length / 8), 0)}
              />
              <YAxis hide />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                      <div className="font-medium">{d.date}</div>
                      <div>{d.hours.toFixed(1)}h tracked</div>
                    </div>
                  );
                }}
              />
              <Bar dataKey="hours" radius={[2, 2, 0, 0]}>
                {daily_hours.map((d) => (
                  <Cell key={d.date} fill={d.hours > 6 ? '#f59e0b' : d.hours > 3 ? '#78350f' : '#333'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}
    </div>
  );
}

function ComparisonCard({ label, current, previous, format = (v) => Math.round(v), lowerIsBetter = false }) {
  const diff = previous > 0 ? ((current - previous) / previous) * 100 : 0;
  const improved = lowerIsBetter ? diff < 0 : diff > 0;
  const TrendIcon = diff > 0 ? TrendingUp : diff < 0 ? TrendingDown : Minus;

  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="text-xs text-[var(--color-text-muted)] mb-1">{label}</div>
      <div className="text-2xl font-semibold">{format(current)}</div>
      <div className={`flex items-center gap-1 text-xs mt-1 ${improved ? 'text-emerald-400' : diff === 0 ? 'text-[var(--color-text-muted)]' : 'text-red-400'}`}>
        <TrendIcon className="w-3 h-3" />
        {Math.abs(diff).toFixed(0)}% vs previous period
      </div>
    </div>
  );
}
