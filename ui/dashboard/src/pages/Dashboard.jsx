import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Target,
  Flame,
  ArrowLeftRight,
  Gauge,
  FileText,
  Sparkles,
  FolderGit2,
} from 'lucide-react';
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api } from '../api';
import {
  useApi,
  useDate,
  formatDate,
  formatTime,
  formatMinutes,
  formatDuration,
  CATEGORY_COLORS,
  CATEGORY_COLOR_BY_KEY,
} from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';
import { useToast } from '../components/Toast';
import { AnimatePresence } from 'framer-motion';
import Markdown from 'react-markdown';
import SessionCard from '../components/SessionCard';
import GoalsPanel from '../components/GoalsPanel';
import AISummaryWrapped from '../components/AISummaryWrapped';

export default function Dashboard() {
  const [searchParams] = useSearchParams();
  const initialDate = searchParams.get('date') || undefined;
  const { date, prev, next, goToday, isToday } = useDate(initialDate);
  const pollInterval = isToday ? 30_000 : 0;
  const { data, loading, error, refetch } = useApi(() => api.getDay(date), [date], { pollInterval });
  const { data: timeline } = useApi(() => api.getTimeline(date), [date], { pollInterval });
  const toast = useToast();
  const [exporting, setExporting] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [showWrapped, setShowWrapped] = useState(false);

  async function handleExport() {
    setExporting(true);
    try {
      const res = await api.exportJournal(date);
      toast(`Journal exported to ${res.path}`, 'success');
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setExporting(false);
    }
  }

  async function handleSummarize() {
    setSummarizing(true);
    try {
      await api.generateSummary(date);
      await refetch();
      setShowWrapped(true);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setSummarizing(false);
    }
  }

  if (loading) return <Loading message="Loading day data..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const { summary, sessions, screenshots } = data;
  const categories = (summary.categories || []).filter((c) => c.minutes > 0);

  // Build hourly focus data from timeline blocks
  const hourlyData = buildHourlyData(timeline?.blocks || []);

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header with date nav */}
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={prev} className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="text-center">
            <h1 className="text-xl font-semibold">{formatDate(date)}</h1>
            {!isToday && (
              <button onClick={goToday} className="text-xs text-[var(--color-amber)] hover:underline mt-0.5">
                Back to today
              </button>
            )}
          </div>
          <button
            onClick={next}
            disabled={isToday}
            className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-30"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleSummarize}
            disabled={summarizing}
            className="flex items-center gap-2 px-3 py-2 text-sm bg-[var(--color-surface-2)] rounded-lg hover:bg-[var(--color-surface-3)] transition-colors disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            {summarizing ? 'Generating...' : 'AI Summary'}
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-2 px-3 py-2 text-sm bg-[var(--color-amber)] text-black rounded-lg hover:bg-[var(--color-amber)]/90 font-medium transition-colors disabled:opacity-50"
          >
            <FileText className="w-4 h-4" />
            {exporting ? 'Exporting...' : 'Generate Journal'}
          </button>
        </div>
      </header>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard
          icon={Target}
          label="Focus Time"
          value={formatMinutes(summary.focus_minutes || 0)}
          sub={`${summary.focus_percentage || 0}% of active time`}
          color="var(--color-amber)"
        />
        <StatCard
          icon={Flame}
          label="Longest Streak"
          value={formatMinutes(summary.longest_focus_streak_minutes || 0)}
          sub="Continuous deep work"
          color="#ef4444"
        />
        <StatCard
          icon={ArrowLeftRight}
          label="App Switches"
          value={summary.app_switches || 0}
          sub="Context switches today"
          color="#8b5cf6"
        />
        <StatCard
          icon={Gauge}
          label="Focus Score"
          value={`${summary.focus_score || 0}/100`}
          sub={scoreLabel(summary.focus_score)}
          color="#10b981"
        />
      </div>

      {/* Timeline bar */}
      {timeline?.blocks?.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Timeline</h2>
          <TimelineBar blocks={timeline.blocks} />
          <TimelineLabels blocks={timeline.blocks} />
        </section>
      )}

      <div className="grid grid-cols-3 gap-4">
        {/* Category donut */}
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Categories</h2>
          {categories.length > 0 ? (
            <div className="flex items-center gap-4">
              <div className="w-36 h-36">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie
                      data={categories}
                      dataKey="minutes"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius={35}
                      outerRadius={60}
                      paddingAngle={2}
                      isAnimationActive={false}
                    >
                      {categories.map((c) => (
                        <Cell key={c.name} fill={CATEGORY_COLORS[c.name] || '#6b7280'} />
                      ))}
                    </Pie>
                    <Tooltip
                      content={({ payload }) => {
                        if (!payload?.[0]) return null;
                        const d = payload[0].payload;
                        return (
                          <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                            <span className="font-medium">{d.name}</span>: {formatMinutes(d.minutes)}
                          </div>
                        );
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-1.5 text-xs">
                {categories.map((c) => (
                  <div key={c.name} className="flex items-center gap-2">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: CATEGORY_COLORS[c.name] || '#6b7280' }}
                    />
                    <span className="text-[var(--color-text-muted)]">{c.name}</span>
                    <span className="ml-auto font-medium">{formatMinutes(c.minutes)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-sm text-[var(--color-text-muted)]">No data yet</p>
          )}
        </section>

        {/* Hourly focus chart */}
        <section className="col-span-2 bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Hourly Activity</h2>
          {hourlyData.length > 0 ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={hourlyData}>
                <XAxis dataKey="hour" tick={{ fill: '#888', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis hide />
                <Tooltip
                  content={({ payload }) => {
                    if (!payload?.[0]) return null;
                    const d = payload[0].payload;
                    return (
                      <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                        {d.hour}: {d.minutes}m active
                      </div>
                    );
                  }}
                />
                <Bar dataKey="minutes" radius={[3, 3, 0, 0]}>
                  {hourlyData.map((d) => (
                    <Cell key={d.hour} fill={d.minutes > 30 ? '#f59e0b' : d.minutes > 0 ? '#78350f' : '#1a1a1a'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-[var(--color-text-muted)]">No data yet</p>
          )}
        </section>
      </div>

      {/* Detected Projects */}
      {summary.projects?.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3 flex items-center gap-2">
            <FolderGit2 className="w-4 h-4 text-[var(--color-amber)]" />
            Projects Detected
          </h2>
          <div className="flex flex-wrap gap-2">
            {summary.projects.map((p) => (
              <div
                key={p.name}
                className="flex items-center gap-2 bg-[var(--color-surface-2)] px-3 py-2 rounded-lg border border-[var(--color-border)]"
              >
                <span className="text-sm font-medium">{p.name}</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {p.minutes >= 60
                    ? `${Math.floor(p.minutes / 60)}h ${p.minutes % 60}m`
                    : `${p.minutes}m`}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Daily Goals */}
      <GoalsPanel date={date} />

      {/* AI Reflection */}
      {summary.ai_summary && (
        <button
          onClick={() => setShowWrapped(true)}
          className="w-full text-left group relative overflow-hidden bg-gradient-to-r from-[#1a0a2e] via-[#16213e] to-[#1a0a2e] rounded-xl p-5 border border-[var(--color-amber)]/20 hover:border-[var(--color-amber)]/50 transition-all duration-300"
        >
          <div className="absolute inset-0 bg-gradient-to-r from-amber-500/5 via-purple-500/5 to-amber-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
          <div className="relative flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-gradient-to-br from-amber-400 to-orange-600 flex items-center justify-center">
                <Sparkles className="w-5 h-5 text-black" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-white">Your Day, Wrapped</h2>
                <p className="text-xs text-white/40 mt-0.5">Tap to view your immersive daily summary</p>
              </div>
            </div>
            <div className="text-xs text-[var(--color-amber)] font-medium group-hover:translate-x-1 transition-transform">
              View &rarr;
            </div>
          </div>
        </button>
      )}

      {/* Wrapped fullscreen overlay */}
      <AnimatePresence>
        {showWrapped && summary.ai_summary && (
          <AISummaryWrapped
            summary={summary}
            sessions={sessions}
            categories={categories}
            onClose={() => setShowWrapped(false)}
          />
        )}
      </AnimatePresence>

      {/* Sessions */}
      <section>
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">
          Sessions ({sessions.length})
        </h2>
        {sessions.length > 0 ? (
          <div className="space-y-2">
            {sessions.map((s) => (
              <SessionCard key={s.id} session={s} onTagged={refetch} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--color-text-muted)]">No sessions recorded yet</p>
        )}
      </section>
    </div>
  );
}

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-4 h-4" style={{ color }} />
        <span className="text-xs text-[var(--color-text-muted)]">{label}</span>
      </div>
      <div className="text-2xl font-semibold" style={{ color }}>{value}</div>
      <div className="text-xs text-[var(--color-text-muted)] mt-1">{sub}</div>
    </div>
  );
}

function TimelineBar({ blocks }) {
  if (!blocks.length) return null;
  const startMin = timeToMinutes(blocks[0].start);
  const endMin = timeToMinutes(blocks[blocks.length - 1].end);
  const span = Math.max(endMin - startMin, 1);

  return (
    <div className="relative h-10 rounded-lg overflow-hidden bg-[var(--color-surface-2)]">
      {blocks.map((b, i) => {
        const left = ((timeToMinutes(b.start) - startMin) / span) * 100;
        const width = Math.max(((timeToMinutes(b.end) - timeToMinutes(b.start)) / span) * 100, 0.5);
        const dur = timeToMinutes(b.end) - timeToMinutes(b.start);
        return (
          <div
            key={i}
            className="absolute top-0 h-full group cursor-pointer hover:brightness-125 transition-all"
            style={{
              left: `${left}%`,
              width: `${width}%`,
              backgroundColor: CATEGORY_COLOR_BY_KEY[b.category] || '#6b7280',
              // Add subtle border between segments
              borderRight: i < blocks.length - 1 ? '1px solid rgba(0,0,0,0.3)' : 'none',
            }}
          >
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50 pointer-events-none">
              <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)] whitespace-nowrap">
                <div className="font-medium">{b.app_name}</div>
                {b.title && <div className="text-[var(--color-text-muted)] max-w-48 truncate">{b.title}</div>}
                <div className="text-[var(--color-text-muted)] mt-0.5">{b.start} – {b.end} ({dur}m)</div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TimelineLabels({ blocks }) {
  if (!blocks.length) return null;
  const startMin = timeToMinutes(blocks[0].start);
  const endMin = timeToMinutes(blocks[blocks.length - 1].end);
  const span = Math.max(endMin - startMin, 1);

  // Generate hour labels within range
  const startHour = Math.floor(startMin / 60);
  const endHour = Math.ceil(endMin / 60);
  const labels = [];
  for (let h = startHour; h <= endHour; h++) {
    const pos = ((h * 60 - startMin) / span) * 100;
    if (pos >= 0 && pos <= 100) {
      labels.push({ hour: h, pos });
    }
  }

  return (
    <div className="relative h-4 mt-1">
      {labels.map(({ hour, pos }) => (
        <span
          key={hour}
          className="absolute text-[10px] text-[var(--color-text-muted)] -translate-x-1/2"
          style={{ left: `${pos}%` }}
        >
          {hour}:00
        </span>
      ))}
    </div>
  );
}

function timeToMinutes(t) {
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
}

function buildHourlyData(blocks) {
  const hours = {};
  for (let h = 6; h <= 23; h++) hours[h] = 0;

  for (const b of blocks) {
    // Count all non-idle activity
    if (b.category === 'idle') continue;
    const startH = parseInt(b.start.split(':')[0]);
    const endH = parseInt(b.end.split(':')[0]);
    const startM = parseInt(b.start.split(':')[1]);
    const endM = parseInt(b.end.split(':')[1]);

    if (startH === endH) {
      hours[startH] = (hours[startH] || 0) + (endM - startM);
    } else {
      hours[startH] = (hours[startH] || 0) + (60 - startM);
      for (let h = startH + 1; h < endH; h++) {
        hours[h] = (hours[h] || 0) + 60;
      }
      hours[endH] = (hours[endH] || 0) + endM;
    }
  }

  return Object.entries(hours)
    .map(([h, m]) => ({
      hour: `${h}:00`,
      minutes: Math.min(m, 60),
    }))
    .filter((d) => d.minutes > 0 || parseInt(d.hour) >= 6);
}

function scoreLabel(score) {
  if (score >= 80) return 'Excellent focus day';
  if (score >= 60) return 'Good focus day';
  if (score >= 40) return 'Moderate focus';
  return 'Scattered day';
}
