import { useState } from 'react';
import {
  ArrowLeftRight,
  Trophy,
  Target,
  Flame,
  Gauge,
  Clock,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  Radar,
  Legend,
} from 'recharts';
import { api } from '../api';
import { useApi, formatDate, formatMinutes, todayStr, CATEGORY_COLORS } from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';

export default function Compare() {
  const [dateA, setDateA] = useState(offsetDays(todayStr(), -1));
  const [dateB, setDateB] = useState(todayStr());
  const [trigger, setTrigger] = useState(0);
  const { data, loading, error, refetch } = useApi(
    () => api.compareDays(dateA, dateB),
    [dateA, dateB, trigger]
  );

  if (loading) return <Loading message="Comparing days..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const { day_a, day_b } = data;

  // Determine winner for each metric
  const metrics = [
    {
      label: 'Focus Score',
      icon: Gauge,
      a: day_a.focus_score,
      b: day_b.focus_score,
      format: (v) => `${v}/100`,
      higherBetter: true,
    },
    {
      label: 'Focus Time',
      icon: Target,
      a: day_a.focus_minutes,
      b: day_b.focus_minutes,
      format: formatMinutes,
      higherBetter: true,
    },
    {
      label: 'Total Tracked',
      icon: Clock,
      a: day_a.total_tracked_minutes,
      b: day_b.total_tracked_minutes,
      format: formatMinutes,
      higherBetter: true,
    },
    {
      label: 'Longest Streak',
      icon: Flame,
      a: day_a.longest_focus_streak_minutes,
      b: day_b.longest_focus_streak_minutes,
      format: formatMinutes,
      higherBetter: true,
    },
    {
      label: 'App Switches',
      icon: ArrowLeftRight,
      a: day_a.app_switches,
      b: day_b.app_switches,
      format: (v) => v,
      higherBetter: false,
    },
    {
      label: 'Entertainment',
      icon: Clock,
      a: day_a.entertainment_minutes,
      b: day_b.entertainment_minutes,
      format: formatMinutes,
      higherBetter: false,
    },
  ];

  // Overall winner
  let winsA = 0, winsB = 0;
  metrics.forEach((m) => {
    if (m.higherBetter) {
      if (m.a > m.b) winsA++;
      else if (m.b > m.a) winsB++;
    } else {
      if (m.a < m.b) winsA++;
      else if (m.b < m.a) winsB++;
    }
  });

  // Category comparison bar chart data
  const catBarData = (day_a.categories || []).map((ca) => {
    const cb = (day_b.categories || []).find((c) => c.name === ca.name);
    return {
      name: ca.name,
      day_a: ca.minutes,
      day_b: cb?.minutes || 0,
    };
  }).filter((c) => c.day_a > 0 || c.day_b > 0);

  // Radar data
  const radarData = [
    { metric: 'Focus', a: day_a.focus_percentage || 0, b: day_b.focus_percentage || 0 },
    { metric: 'Streak', a: Math.min(day_a.longest_focus_streak_minutes, 120), b: Math.min(day_b.longest_focus_streak_minutes, 120) },
    { metric: 'Score', a: day_a.focus_score, b: day_b.focus_score },
    {
      metric: 'Discipline',
      a: Math.max(0, 100 - day_a.entertainment_minutes),
      b: Math.max(0, 100 - day_b.entertainment_minutes),
    },
    {
      metric: 'Stability',
      a: Math.max(0, 100 - Math.min(day_a.app_switches, 100)),
      b: Math.max(0, 100 - Math.min(day_b.app_switches, 100)),
    },
  ];

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header with date pickers */}
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Compare Days</h1>
      </header>

      <div className="flex items-center gap-4">
        <DatePicker label="Day A" value={dateA} onChange={setDateA} color="#f59e0b" />
        <ArrowLeftRight className="w-5 h-5 text-[var(--color-text-muted)] shrink-0" />
        <DatePicker label="Day B" value={dateB} onChange={setDateB} color="#3b82f6" />
      </div>

      {/* Overall winner banner */}
      <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)] text-center">
        {winsA > winsB ? (
          <div className="flex items-center justify-center gap-2">
            <Trophy className="w-5 h-5 text-[var(--color-amber)]" />
            <span className="font-semibold text-[var(--color-amber)]">{shortDate(dateA)}</span>
            <span className="text-[var(--color-text-muted)]">was the better day</span>
            <span className="text-xs text-[var(--color-text-muted)]">({winsA} of {metrics.length} metrics)</span>
          </div>
        ) : winsB > winsA ? (
          <div className="flex items-center justify-center gap-2">
            <Trophy className="w-5 h-5 text-blue-400" />
            <span className="font-semibold text-blue-400">{shortDate(dateB)}</span>
            <span className="text-[var(--color-text-muted)]">was the better day</span>
            <span className="text-xs text-[var(--color-text-muted)]">({winsB} of {metrics.length} metrics)</span>
          </div>
        ) : (
          <span className="text-[var(--color-text-muted)]">Both days are evenly matched</span>
        )}
      </div>

      {/* Metric comparison cards */}
      <div className="grid grid-cols-3 gap-3">
        {metrics.map((m) => {
          const aWins = m.higherBetter ? m.a > m.b : m.a < m.b;
          const bWins = m.higherBetter ? m.b > m.a : m.b < m.a;
          const Icon = m.icon;
          return (
            <div
              key={m.label}
              className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]"
            >
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] mb-3">
                <Icon className="w-3.5 h-3.5" />
                {m.label}
              </div>
              <div className="flex items-end justify-between">
                <div className="text-center">
                  <div
                    className={`text-xl font-semibold ${aWins ? 'text-[var(--color-amber)]' : 'text-[var(--color-text-muted)]'}`}
                  >
                    {m.format(m.a)}
                  </div>
                  <div className="text-[10px] text-[var(--color-text-muted)] mt-0.5">
                    {shortDate(dateA)}
                    {aWins && ' *'}
                  </div>
                </div>
                <span className="text-xs text-[var(--color-text-muted)] mb-1">vs</span>
                <div className="text-center">
                  <div
                    className={`text-xl font-semibold ${bWins ? 'text-blue-400' : 'text-[var(--color-text-muted)]'}`}
                  >
                    {m.format(m.b)}
                  </div>
                  <div className="text-[10px] text-[var(--color-text-muted)] mt-0.5">
                    {shortDate(dateB)}
                    {bWins && ' *'}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Category comparison chart */}
        {catBarData.length > 0 && (
          <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
            <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Category Breakdown</h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={catBarData} layout="vertical" barGap={2}>
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
                        <div className="font-medium">{d.name}</div>
                        <div className="text-[var(--color-amber)]">{shortDate(dateA)}: {formatMinutes(d.day_a)}</div>
                        <div className="text-blue-400">{shortDate(dateB)}: {formatMinutes(d.day_b)}</div>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="day_a" fill="#f59e0b" radius={[0, 3, 3, 0]} barSize={8} />
                <Bar dataKey="day_b" fill="#3b82f6" radius={[0, 3, 3, 0]} barSize={8} />
              </BarChart>
            </ResponsiveContainer>
          </section>
        )}

        {/* Radar chart */}
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Performance Radar</h2>
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="#333" />
              <PolarAngleAxis dataKey="metric" tick={{ fill: '#888', fontSize: 11 }} />
              <Radar
                name={shortDate(dateA)}
                dataKey="a"
                stroke="#f59e0b"
                fill="#f59e0b"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Radar
                name={shortDate(dateB)}
                dataKey="b"
                stroke="#3b82f6"
                fill="#3b82f6"
                fillOpacity={0.15}
                strokeWidth={2}
              />
              <Legend
                wrapperStyle={{ fontSize: '11px' }}
                iconType="line"
              />
            </RadarChart>
          </ResponsiveContainer>
        </section>
      </div>
    </div>
  );
}

function DatePicker({ label, value, onChange, color }) {
  return (
    <div className="flex-1">
      <label className="text-xs text-[var(--color-text-muted)] block mb-1">{label}</label>
      <div className="relative">
        <input
          type="date"
          value={value}
          max={todayStr()}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-[var(--color-surface)] border-2 rounded-lg px-3 py-2 text-sm outline-none transition-colors"
          style={{ borderColor: color + '40' }}
        />
        <div className="text-xs text-[var(--color-text-muted)] mt-1">{formatDate(value)}</div>
      </div>
    </div>
  );
}

function shortDate(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function offsetDays(dateStr, n) {
  const d = new Date(dateStr + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().split('T')[0];
}
