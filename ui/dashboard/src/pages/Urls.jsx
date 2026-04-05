import { useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Globe,
  Clock,
  TrendingUp,
  TrendingDown,
  Minus,
  ArrowRight,
  FileText,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  PieChart,
  Pie,
} from 'recharts';
import { api } from '../api';
import {
  useApi,
  useDate,
  formatDate,
  formatTime,
  formatDuration,
  CATEGORY_COLOR_BY_KEY,
  CATEGORY_COLORS,
} from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';

const CAT_LABELS = {
  coding: 'Coding',
  communication: 'Communication',
  browsing: 'Browsing',
  entertainment: 'Entertainment',
  writing: 'Writing',
  learning: 'Learning',
  other: 'Other',
};

export default function Urls() {
  const { date, prev, next, goToday, isToday } = useDate();
  const { data, loading, error, refetch } = useApi(() => api.getUrlStats(date), [date]);
  const [activeTab, setActiveTab] = useState('overview');

  if (loading) return <Loading message="Loading URL data..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const { summary, domains, top_pages, category_breakdown, hourly, sequences, url_events } = data;

  const tabs = [
    { key: 'overview', label: 'Overview' },
    { key: 'pages', label: 'Pages' },
    { key: 'flow', label: 'Browsing Flow' },
    { key: 'events', label: 'Raw Events' },
  ];

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={prev} className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="text-center">
            <h1 className="text-xl font-semibold">URL Analytics</h1>
            <p className="text-xs text-[var(--color-text-muted)]">{formatDate(date)}</p>
          </div>
          <button
            onClick={next}
            disabled={isToday}
            className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-30"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
        {!isToday && (
          <button onClick={goToday} className="text-xs text-[var(--color-amber)] hover:underline">
            Back to today
          </button>
        )}
      </header>

      {!domains?.length ? (
        <div className="bg-[var(--color-surface)] rounded-xl p-8 border border-[var(--color-border)] text-center">
          <Globe className="w-8 h-8 text-[var(--color-text-muted)] mx-auto mb-3" />
          <p className="text-[var(--color-text-muted)]">No URL data captured yet for this day.</p>
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-5 gap-3">
            <SummaryCard label="Browser Time" value={formatDuration(summary.total_seconds)} icon={Clock} />
            <SummaryCard label="Pages Visited" value={summary.unique_pages} icon={FileText} />
            <SummaryCard label="Domains" value={summary.unique_domains} icon={Globe} />
            <SummaryCard
              label="Productive"
              value={formatDuration(summary.productive_seconds)}
              icon={TrendingUp}
              color="#10b981"
            />
            <SummaryCard
              label="Distractions"
              value={formatDuration(summary.distraction_seconds)}
              icon={TrendingDown}
              color="#ef4444"
            />
          </div>

          {/* Productivity split bar */}
          {summary.total_seconds > 0 && <ProductivityBar summary={summary} />}

          {/* Tabs */}
          <div className="flex gap-1 bg-[var(--color-surface)] rounded-lg p-1 border border-[var(--color-border)] w-fit">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                  activeTab === t.key
                    ? 'bg-[var(--color-surface-3)] text-[var(--color-text)] font-medium'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {activeTab === 'overview' && (
            <OverviewTab
              domains={domains}
              categoryBreakdown={category_breakdown}
              hourly={hourly}
            />
          )}
          {activeTab === 'pages' && <PagesTab pages={top_pages} />}
          {activeTab === 'flow' && <FlowTab sequences={sequences} />}
          {activeTab === 'events' && <EventsTab events={url_events} />}
        </>
      )}
    </div>
  );
}

function SummaryCard({ label, value, icon: Icon, color }) {
  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-3 border border-[var(--color-border)]">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="w-3.5 h-3.5 text-[var(--color-text-muted)]" style={color ? { color } : {}} />
        <span className="text-xs text-[var(--color-text-muted)]">{label}</span>
      </div>
      <div className="text-lg font-semibold" style={color ? { color } : {}}>
        {value}
      </div>
    </div>
  );
}

function ProductivityBar({ summary }) {
  const total = summary.total_seconds || 1;
  const prodPct = Math.round((summary.productive_seconds / total) * 100);
  const distPct = Math.round((summary.distraction_seconds / total) * 100);
  const neutPct = 100 - prodPct - distPct;

  return (
    <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] mb-2">
        <span className="flex items-center gap-1">
          <TrendingUp className="w-3 h-3 text-emerald-500" /> Productive {prodPct}%
        </span>
        <span className="flex items-center gap-1">
          <Minus className="w-3 h-3" /> Neutral {neutPct}%
        </span>
        <span className="flex items-center gap-1">
          <TrendingDown className="w-3 h-3 text-red-500" /> Distractions {distPct}%
        </span>
      </div>
      <div className="flex h-3 rounded-full overflow-hidden gap-0.5">
        {prodPct > 0 && (
          <div className="bg-emerald-500 rounded-full" style={{ width: `${prodPct}%` }} />
        )}
        {neutPct > 0 && (
          <div className="bg-[var(--color-surface-3)] rounded-full" style={{ width: `${neutPct}%` }} />
        )}
        {distPct > 0 && (
          <div className="bg-red-500 rounded-full" style={{ width: `${distPct}%` }} />
        )}
      </div>
    </div>
  );
}

function OverviewTab({ domains, categoryBreakdown, hourly }) {
  const topDomains = (domains || []).slice(0, 15);
  const pieData = (categoryBreakdown || []).filter((c) => c.seconds > 0);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        {/* Top domains */}
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Time by Domain</h2>
          <ResponsiveContainer width="100%" height={Math.max(topDomains.length * 28, 100)}>
            <BarChart data={topDomains} layout="vertical">
              <XAxis type="number" hide />
              <YAxis
                dataKey="domain"
                type="category"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={120}
              />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                      <div className="font-medium">{d.domain}</div>
                      <div>{formatDuration(d.total_seconds || 0)}</div>
                      <div>{d.visit_count} visits</div>
                      <div className="capitalize text-[var(--color-text-muted)]">
                        {CAT_LABELS[d.category] || d.category}
                      </div>
                    </div>
                  );
                }}
              />
              <Bar dataKey="total_seconds" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                {topDomains.map((d) => (
                  <Cell key={d.domain} fill={CATEGORY_COLOR_BY_KEY[d.category] || '#6b7280'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>

        {/* Category pie */}
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Browser Time by Category</h2>
          <div className="flex items-start gap-4">
            <div className="w-36 h-36 shrink-0">
              <ResponsiveContainer>
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="seconds"
                    nameKey="category"
                    cx="50%"
                    cy="50%"
                    innerRadius={35}
                    outerRadius={60}
                    paddingAngle={2}
                    isAnimationActive={false}
                  >
                    {pieData.map((c) => (
                      <Cell key={c.category} fill={CATEGORY_COLOR_BY_KEY[c.category] || '#6b7280'} />
                    ))}
                  </Pie>
                  <Tooltip
                    content={({ payload }) => {
                      if (!payload?.[0]) return null;
                      const d = payload[0].payload;
                      return (
                        <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                          <span className="font-medium capitalize">{CAT_LABELS[d.category] || d.category}</span>:{' '}
                          {formatDuration(d.seconds)}
                        </div>
                      );
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="space-y-2 text-xs pt-2">
              {pieData.map((c) => (
                <div key={c.category} className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: CATEGORY_COLOR_BY_KEY[c.category] || '#6b7280' }}
                    />
                    <span className="capitalize">{CAT_LABELS[c.category] || c.category}</span>
                    <span className="ml-auto font-medium">{formatDuration(c.seconds)}</span>
                  </div>
                  <div className="text-[var(--color-text-muted)] pl-[18px] truncate">
                    {(c.domains || []).slice(0, 3).join(', ')}
                    {(c.domains || []).length > 3 && ` +${c.domains.length - 3}`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      {/* Hourly browsing heatmap */}
      {hourly?.length > 0 && (
        <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">Browsing by Hour</h2>
          <ResponsiveContainer width="100%" height={120}>
            <BarChart data={hourly}>
              <XAxis dataKey="hour" tick={{ fill: '#888', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis hide />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                      {d.hour}: {formatDuration(d.seconds)}
                    </div>
                  );
                }}
              />
              <Bar dataKey="seconds" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                {hourly.map((d) => (
                  <Cell
                    key={d.hour}
                    fill={d.seconds > 300 ? '#8b5cf6' : d.seconds > 0 ? '#4c1d95' : '#1a1a1a'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}
    </div>
  );
}

function PagesTab({ pages }) {
  if (!pages?.length) {
    return <p className="text-sm text-[var(--color-text-muted)]">No page data yet.</p>;
  }

  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">
        Top Pages by Time Spent
      </h2>
      <div className="space-y-1">
        {pages.map((p, i) => (
          <div
            key={i}
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-[var(--color-surface-2)] group"
          >
            <span className="text-xs text-[var(--color-text-muted)] w-6 text-right shrink-0">{i + 1}</span>
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: CATEGORY_COLOR_BY_KEY[p.category] || '#6b7280' }}
            />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{p.title || p.url}</div>
              <div className="text-xs text-[var(--color-text-muted)] truncate">{p.domain}</div>
            </div>
            <div className="text-right shrink-0">
              <div className="text-sm font-medium">{formatDuration(p.total_seconds)}</div>
              <div className="text-xs text-[var(--color-text-muted)]">{p.visits} visit{p.visits !== 1 ? 's' : ''}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function FlowTab({ sequences }) {
  if (!sequences?.length) {
    return <p className="text-sm text-[var(--color-text-muted)]">No browsing flow data yet.</p>;
  }

  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-1">Browsing Flow</h2>
      <p className="text-xs text-[var(--color-text-muted)] mb-4">
        Your domain-to-domain navigation sequence. See how you move across sites.
      </p>
      <div className="space-y-0">
        {sequences.map((s, i) => (
          <div key={i} className="flex items-center gap-3 group">
            {/* Vertical connector */}
            <div className="flex flex-col items-center w-5 shrink-0">
              <div
                className="w-2.5 h-2.5 rounded-full shrink-0 z-10"
                style={{ background: CATEGORY_COLOR_BY_KEY[s.category] || '#6b7280' }}
              />
              {i < sequences.length - 1 && (
                <div className="w-px h-6 bg-[var(--color-border)]" />
              )}
            </div>
            <div className="flex items-center gap-2 py-1 flex-1 min-w-0">
              <span className="text-xs text-[var(--color-text-muted)] shrink-0 w-14">
                {s.time ? formatTime(s.time) : ''}
              </span>
              <span className="text-sm font-medium shrink-0">{s.domain}</span>
              {s.title && (
                <span className="text-xs text-[var(--color-text-muted)] truncate">
                  — {s.title}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function EventsTab({ events }) {
  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">
        Raw URL Events ({events?.length || 0})
      </h2>
      <div className="space-y-1 max-h-[500px] overflow-y-auto">
        {(events || []).slice(0, 200).map((e, i) => (
          <div
            key={i}
            className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-[var(--color-surface-2)] text-xs"
          >
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: CATEGORY_COLOR_BY_KEY[e.category] || '#6b7280' }}
            />
            <span className="text-[var(--color-text-muted)] shrink-0 w-16">
              {e.timestamp ? formatTime(e.timestamp) : ''}
            </span>
            <span className="truncate flex-1" title={e.url}>
              {e.page_title || e.domain}
            </span>
            <span className="text-[var(--color-text-muted)] shrink-0">{e.domain}</span>
            {e.duration_seconds > 0 && (
              <span className="text-[var(--color-text-muted)] shrink-0 flex items-center gap-0.5">
                <Clock className="w-3 h-3" />
                {formatDuration(e.duration_seconds)}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
