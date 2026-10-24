import { useMemo } from 'react';
import { Clock, ArrowLeft, ArrowRight, Globe, FileText } from 'lucide-react';
import { CATEGORY_COLOR_BY_KEY, formatTime } from '../../hooks';

/**
 * Right-side panel that gives meaning to the current screenshot.
 * Shows current session, came-from / next-switch, URL when in browser.
 *
 * Props:
 *   sessions       — sessions for the day (sorted by start_time)
 *   urlEvents      — url_events for the day, sorted by timestamp
 *   screenshot     — current screenshot
 */
export default function SessionContext({ sessions, urlEvents, screenshot }) {
  const ctx = useMemo(() => {
    if (!screenshot || !sessions?.length) return null;
    const t = new Date(screenshot.timestamp).getTime();

    // Sessions list is grouped-by-app from /api/day; for time bracketing
    // we want the *raw* time blocks. The grouped sessions still contain
    // start_time and end_time covering their first and last sub-stretch,
    // so for the purposes of "which session am I in" we accept any
    // session whose [start, end] window contains t — pick the smallest
    // (most specific) such window.
    let containing = null;
    let containingSpan = Infinity;
    for (const s of sessions) {
      const start = new Date(s.start_time).getTime();
      const end = new Date(s.end_time).getTime();
      if (t >= start && t <= end) {
        const span = end - start;
        if (span < containingSpan) {
          containingSpan = span;
          containing = s;
        }
      }
    }

    // Previous and next sessions by time (regardless of containment)
    const sortedByStart = [...sessions].sort(
      (a, b) => new Date(a.start_time) - new Date(b.start_time)
    );
    let prev = null;
    let next = null;
    for (const s of sortedByStart) {
      const start = new Date(s.start_time).getTime();
      if (start < t && (!prev || start > new Date(prev.start_time).getTime())) prev = s;
      if (start > t && !next) next = s;
    }

    // URL-at-timestamp: nearest URL event within 60s before, else nearest forward within 60s
    let url = null;
    if (urlEvents?.length) {
      let best = null;
      let bestDist = Infinity;
      for (const u of urlEvents) {
        const ut = new Date(u.timestamp).getTime();
        const dist = Math.abs(ut - t);
        if (dist < bestDist && dist <= 90_000) { bestDist = dist; best = u; }
      }
      url = best;
    }

    return { containing, prev, next, url };
  }, [screenshot, sessions, urlEvents]);

  if (!ctx) return null;

  const { containing, prev, next, url } = ctx;
  const session = containing;
  const sessionColor = session
    ? CATEGORY_COLOR_BY_KEY[session.category] || '#6b7280'
    : '#374151';

  // How far into the session is `screenshot.timestamp`?
  const intoMin = session
    ? Math.floor((new Date(screenshot.timestamp) - new Date(session.start_time)) / 60_000)
    : null;

  return (
    <aside className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-4 space-y-4 text-sm">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
          Now
        </div>
        {session ? (
          <>
            <div className="flex items-center gap-2">
              <span
                className="w-2 h-2 rounded-full"
                style={{ background: sessionColor }}
              />
              <span className="font-medium">{session.app_name}</span>
              <span
                className="text-[10px] uppercase px-1.5 py-0.5 rounded-full"
                style={{ background: `${sessionColor}22`, color: sessionColor }}
              >
                {session.category}
              </span>
            </div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1 flex items-center gap-2">
              <Clock className="w-3 h-3" />
              {intoMin !== null && intoMin >= 0 ? (
                <span>{intoMin}m into a {minutes(session.duration_seconds)} block</span>
              ) : (
                <span>{minutes(session.duration_seconds)} block</span>
              )}
            </div>
          </>
        ) : (
          <div className="text-[var(--color-text-muted)] italic text-xs">
            Between sessions
          </div>
        )}
      </div>

      {url && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
            URL
          </div>
          <div className="text-xs flex items-start gap-1.5">
            <Globe className="w-3 h-3 mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
            <div className="min-w-0">
              <div className="text-[var(--color-amber)] truncate" title={url.domain}>
                {url.domain}
              </div>
              {url.page_title && (
                <div
                  className="text-[var(--color-text-muted)] line-clamp-2 mt-0.5"
                  title={url.page_title}
                >
                  {url.page_title}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {screenshot.window_title && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
            Window
          </div>
          <div className="text-xs flex items-start gap-1.5">
            <FileText className="w-3 h-3 mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
            <span className="line-clamp-3 break-words">{screenshot.window_title}</span>
          </div>
        </div>
      )}

      <div className="border-t border-[var(--color-border)] pt-3 space-y-2">
        {prev && (
          <div className="text-xs">
            <div className="flex items-center gap-1 text-[var(--color-text-muted)] mb-0.5">
              <ArrowLeft className="w-3 h-3" /> Came from
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: CATEGORY_COLOR_BY_KEY[prev.category] || '#6b7280' }}
              />
              <span>{prev.app_name}</span>
              <span className="text-[var(--color-text-muted)] font-mono ml-auto">
                {formatTime(prev.start_time)}
              </span>
            </div>
          </div>
        )}
        {next && (
          <div className="text-xs">
            <div className="flex items-center gap-1 text-[var(--color-text-muted)] mb-0.5">
              <ArrowRight className="w-3 h-3" /> Next
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: CATEGORY_COLOR_BY_KEY[next.category] || '#6b7280' }}
              />
              <span>{next.app_name}</span>
              <span className="text-[var(--color-text-muted)] font-mono ml-auto">
                {formatTime(next.start_time)}
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
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
