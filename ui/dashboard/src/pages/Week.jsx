import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Target, Clock, Gauge } from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api } from '../api';
import { useApi, todayStr } from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';

export default function Week() {
  const [endDate, setEndDate] = useState(todayStr());
  const { data, loading, error, refetch } = useApi(() => api.getWeek(endDate), [endDate]);
  const navigate = useNavigate();

  function prevWeek() {
    const d = new Date(endDate + 'T12:00:00');
    d.setDate(d.getDate() - 7);
    setEndDate(d.toISOString().split('T')[0]);
  }

  function nextWeek() {
    const d = new Date(endDate + 'T12:00:00');
    d.setDate(d.getDate() + 7);
    const today = todayStr();
    const next = d.toISOString().split('T')[0];
    setEndDate(next > today ? today : next);
  }

  const isCurrentWeek = endDate === todayStr();

  if (loading) return <Loading message="Loading week data..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const { days, avg_focus_score, total_focus_hours, start_date, end_date } = data;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={prevWeek} className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="text-center">
            <h1 className="text-xl font-semibold">Weekly Overview</h1>
            <p className="text-xs text-[var(--color-text-muted)]">
              {formatWeekDate(start_date)} — {formatWeekDate(end_date)}
            </p>
          </div>
          <button
            onClick={nextWeek}
            disabled={isCurrentWeek}
            className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-30"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] mb-1">
            <Gauge className="w-4 h-4" />
            Avg Focus Score
          </div>
          <div className="text-3xl font-semibold text-[var(--color-amber)]">{avg_focus_score}</div>
        </div>
        <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] mb-1">
            <Target className="w-4 h-4" />
            Total Focus
          </div>
          <div className="text-3xl font-semibold text-emerald-400">{total_focus_hours}h</div>
        </div>
        <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] mb-1">
            <Clock className="w-4 h-4" />
            Days Tracked
          </div>
          <div className="text-3xl font-semibold text-blue-400">{days.length}</div>
        </div>
      </div>

      {/* Focus trend chart */}
      {days.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Focus Hours This Week</h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={days}>
              <defs>
                <linearGradient id="focusGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="day_name" tick={{ fill: '#888', fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#888', fontSize: 11 }} axisLine={false} tickLine={false} width={30} />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                      <div className="font-medium">{d.day_name} — {d.date}</div>
                      <div>Focus: {d.focus_hours}h / {d.total_hours}h</div>
                      <div>Score: {d.focus_score}</div>
                    </div>
                  );
                }}
              />
              <Area
                type="monotone"
                dataKey="focus_hours"
                stroke="#f59e0b"
                strokeWidth={2}
                fill="url(#focusGrad)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </section>
      )}

      {/* Day cards grid */}
      <section>
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Daily Breakdown</h2>
        <div className="grid grid-cols-7 gap-2">
          {days.map((d) => (
            <button
              key={d.date}
              onClick={() => {
                navigate(`/?date=${d.date}`);
              }}
              className="bg-[var(--color-surface)] rounded-xl p-3 border border-[var(--color-border)] hover:border-[var(--color-amber)]/50 transition-colors text-left"
            >
              <div className="text-xs text-[var(--color-text-muted)]">{d.day_name}</div>
              <div className="text-xs text-[var(--color-text-muted)]">
                {new Date(d.date + 'T12:00:00').getDate()}
              </div>
              <div className="mt-2">
                {/* Mini score ring */}
                <div className="relative w-10 h-10 mx-auto">
                  <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                    <circle
                      cx="18" cy="18" r="15"
                      fill="none"
                      stroke="var(--color-surface-3)"
                      strokeWidth="3"
                    />
                    <circle
                      cx="18" cy="18" r="15"
                      fill="none"
                      stroke={d.focus_score >= 60 ? '#f59e0b' : d.focus_score >= 30 ? '#78350f' : '#333'}
                      strokeWidth="3"
                      strokeDasharray={`${(d.focus_score / 100) * 94.2} 94.2`}
                      strokeLinecap="round"
                    />
                  </svg>
                  <span className="absolute inset-0 flex items-center justify-center text-xs font-medium">
                    {d.focus_score}
                  </span>
                </div>
              </div>
              <div className="text-center text-xs text-[var(--color-text-muted)] mt-1">
                {d.focus_hours}h focus
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function formatWeekDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
