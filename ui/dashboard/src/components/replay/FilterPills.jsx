import { useMemo } from 'react';
import { CATEGORY_COLOR_BY_KEY } from '../../hooks';

const CATEGORY_LABELS = {
  coding: 'Deep Work',
  communication: 'Comm',
  browsing: 'Browse',
  writing: 'Write',
  learning: 'Learn',
  entertainment: 'Entertainment',
};

function Pill({ active, color, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
        active
          ? 'border-transparent text-black font-medium'
          : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
      }`}
      style={active && color ? { background: color } : undefined}
    >
      {children}
    </button>
  );
}

/**
 * Pills to filter the screenshot stream by category or app.
 *
 * Props:
 *   screenshots — used to derive which categories/apps are even present
 *   sessions    — mapping screenshot.timestamp → category (we use sessions
 *                 because screenshots only carry app_name, not category)
 *   filter      — {type: 'all'} | {type: 'category', value: 'coding'} | {type: 'app', value: 'iTerm2'}
 *   onChange(f)
 */
export default function FilterPills({ screenshots, sessions, filter, onChange }) {
  const { categoriesPresent, topApps } = useMemo(() => {
    const cats = new Set();
    const appCount = new Map();
    for (const sc of (screenshots || [])) {
      if (sc.app_name) appCount.set(sc.app_name, (appCount.get(sc.app_name) || 0) + 1);
    }
    for (const s of (sessions || [])) {
      cats.add(s.category);
    }
    const apps = [...appCount.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([name]) => name);
    return { categoriesPresent: cats, topApps: apps };
  }, [screenshots, sessions]);

  const isAll = filter?.type === 'all' || !filter;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Pill
        active={isAll}
        color="var(--color-amber)"
        onClick={() => onChange({ type: 'all' })}
      >
        All
      </Pill>
      {Object.entries(CATEGORY_LABELS).map(([key, label]) => {
        if (!categoriesPresent.has(key)) return null;
        const active = filter?.type === 'category' && filter.value === key;
        return (
          <Pill
            key={key}
            active={active}
            color={CATEGORY_COLOR_BY_KEY[key]}
            onClick={() =>
              onChange(active ? { type: 'all' } : { type: 'category', value: key })
            }
          >
            {label}
          </Pill>
        );
      })}
      {topApps.length > 0 && (
        <span className="text-[10px] text-[var(--color-text-muted)] mx-1">·</span>
      )}
      {topApps.map((app) => {
        const active = filter?.type === 'app' && filter.value === app;
        return (
          <Pill
            key={app}
            active={active}
            color="var(--color-amber)"
            onClick={() =>
              onChange(active ? { type: 'all' } : { type: 'app', value: app })
            }
          >
            {app}
          </Pill>
        );
      })}
    </div>
  );
}
