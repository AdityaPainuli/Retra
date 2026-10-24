import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Play,
  Pause,
  SkipBack,
  SkipForward,
} from 'lucide-react';
import { api } from '../api';
import { useApi, useDate, formatDate, formatTime } from '../hooks';
import Loading, { ErrorMessage } from '../components/Loading';
import TimelineRibbon from '../components/replay/TimelineRibbon';
import QuickJumps from '../components/replay/QuickJumps';
import SessionContext from '../components/replay/SessionContext';
import FilterPills from '../components/replay/FilterPills';
import HourGrid from '../components/replay/HourGrid';
import NarrativeTrack from '../components/replay/NarrativeTrack';

const PLAYBACK_SPEEDS = [1, 2, 5];

export default function Replay() {
  const { date, prev, next, goToday, isToday } = useDate();
  const { data, loading, error, refetch } = useApi(() => api.getDay(date), [date]);
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState({ type: 'all' });
  const timerRef = useRef(null);

  const allScreenshots = data?.screenshots || [];
  const sessions = data?.sessions || [];
  const urlEvents = data?.url_events || [];

  // Map screenshot timestamps → category by matching to the smallest
  // containing session. Used by FilterPills(type='category').
  const screenshotCategory = useMemo(() => {
    if (!allScreenshots.length || !sessions.length) return new Map();
    const m = new Map();
    for (const sc of allScreenshots) {
      const t = new Date(sc.timestamp).getTime();
      let bestCat = null;
      let bestSpan = Infinity;
      for (const s of sessions) {
        const start = new Date(s.start_time).getTime();
        const end = new Date(s.end_time).getTime();
        if (t >= start && t <= end) {
          const span = end - start;
          if (span < bestSpan) { bestSpan = span; bestCat = s.category; }
        }
      }
      m.set(sc.id, bestCat);
    }
    return m;
  }, [allScreenshots, sessions]);

  // Filter applies to *which screenshots are visible in the scrubber* and
  // constrains keyboard / play-button navigation. The underlying full list
  // still drives the timeline ribbon and quick-jumps.
  const screenshots = useMemo(() => {
    if (filter.type === 'all') return allScreenshots;
    if (filter.type === 'app') {
      return allScreenshots.filter((sc) => sc.app_name === filter.value);
    }
    if (filter.type === 'category') {
      return allScreenshots.filter((sc) => screenshotCategory.get(sc.id) === filter.value);
    }
    return allScreenshots;
  }, [allScreenshots, filter, screenshotCategory]);

  // Clamp `current` if filter shrinks the list
  useEffect(() => {
    if (current >= screenshots.length) setCurrent(Math.max(0, screenshots.length - 1));
  }, [screenshots.length, current]);

  // Auto-playback (restricted to filtered list)
  useEffect(() => {
    if (playing && screenshots.length > 0) {
      timerRef.current = setInterval(() => {
        setCurrent((prev) => {
          if (prev >= screenshots.length - 1) {
            setPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 2000 / speed);
    }
    return () => clearInterval(timerRef.current);
  }, [playing, speed, screenshots.length]);

  // Reset on date change
  useEffect(() => {
    setCurrent(0);
    setPlaying(false);
    setFilter({ type: 'all' });
  }, [date]);

  // Keyboard navigation
  const handleKey = useCallback(
    (e) => {
      // Don't hijack typing into inputs (eg the regenerate narrative button etc)
      if (e.target.matches('input, textarea')) return;
      if (e.key === 'ArrowLeft') {
        setCurrent((p) => Math.max(0, p - 1));
        setPlaying(false);
      } else if (e.key === 'ArrowRight') {
        setCurrent((p) => Math.min(screenshots.length - 1, p + 1));
        setPlaying(false);
      } else if (e.key === ' ') {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    },
    [screenshots.length]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleKey]);

  // Seek by clicking on the timeline / quick-jumps / hour grid. Those
  // components reference the full allScreenshots list, so we need to
  // translate that index into the filtered list (and bail if filtered out).
  function seekByFullIdx(fullIdx) {
    const target = allScreenshots[fullIdx];
    if (!target) return;
    const filteredIdx = screenshots.findIndex((s) => s.id === target.id);
    if (filteredIdx >= 0) {
      setCurrent(filteredIdx);
      setPlaying(false);
    } else {
      // Drop filter so the seek lands somewhere visible
      setFilter({ type: 'all' });
      // Next render: find idx in the unfiltered list
      requestAnimationFrame(() => setCurrent(fullIdx));
    }
  }

  if (loading) return <Loading message="Loading screenshots..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const shot = screenshots[current];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      {/* Header */}
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <button onClick={prev} className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <h1 className="text-xl font-semibold">{formatDate(date)}</h1>
          <button
            onClick={next}
            disabled={isToday}
            className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-30"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
          {!isToday && (
            <button onClick={goToday} className="text-xs text-[var(--color-amber)] hover:underline">
              Back to today
            </button>
          )}
        </div>
        {/* Quick-jump chips on the right */}
        <QuickJumps
          sessions={sessions}
          screenshots={allScreenshots}
          onSeek={seekByFullIdx}
        />
      </header>

      {allScreenshots.length === 0 ? (
        <div className="flex items-center justify-center h-96 bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)]">
          <p className="text-[var(--color-text-muted)]">No screenshots captured for this day</p>
        </div>
      ) : (
        <>
          {/* Day timeline ribbon */}
          <TimelineRibbon
            sessions={sessions}
            screenshots={allScreenshots}
            currentIdx={
              shot ? allScreenshots.findIndex((s) => s.id === shot.id) : 0
            }
            onSeek={seekByFullIdx}
          />

          {/* Two-column: screenshot + session context */}
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
            {/* Screenshot viewer */}
            <div className="relative bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] overflow-hidden">
              {shot ? (
                <>
                  <img
                    src={api.getScreenshotUrl(shot.id)}
                    alt={`Screenshot at ${shot.timestamp}`}
                    className="w-full h-auto max-h-[55vh] object-contain bg-black"
                  />
                  <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-3">
                    <div className="flex items-center justify-between text-white">
                      <div>
                        <div className="font-medium text-sm">{shot.app_name}</div>
                        <div className="text-xs opacity-70 truncate max-w-md">{shot.window_title}</div>
                      </div>
                      <div className="text-sm font-mono">{formatTime(shot.timestamp)}</div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex items-center justify-center h-96">
                  <p className="text-[var(--color-text-muted)] text-sm">
                    No screenshots match the current filter
                  </p>
                </div>
              )}
            </div>

            {/* Right rail: session context */}
            <SessionContext
              sessions={sessions}
              urlEvents={urlEvents}
              screenshot={shot}
            />
          </div>

          {/* Narrative track */}
          <NarrativeTrack key={date} date={date} screenshot={shot} />

          {/* Playback controls */}
          <div className="flex items-center justify-center gap-3">
            <button
              onClick={() => { setCurrent(0); setPlaying(false); }}
              className="p-2 rounded-lg hover:bg-[var(--color-surface-2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="First screenshot"
            >
              <SkipBack className="w-4 h-4" />
            </button>
            <button
              onClick={() => { setCurrent((p) => Math.max(0, p - 1)); setPlaying(false); }}
              className="p-2 rounded-lg hover:bg-[var(--color-surface-2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="Previous (Left arrow)"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <button
              onClick={() => setPlaying((p) => !p)}
              disabled={!shot}
              className="p-3 rounded-full bg-[var(--color-amber)] text-black hover:bg-[var(--color-amber)]/90 disabled:opacity-50 transition-colors"
              title="Play/Pause (Space)"
            >
              {playing ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
            </button>
            <button
              onClick={() => { setCurrent((p) => Math.min(screenshots.length - 1, p + 1)); setPlaying(false); }}
              className="p-2 rounded-lg hover:bg-[var(--color-surface-2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="Next (Right arrow)"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
            <button
              onClick={() => { setCurrent(screenshots.length - 1); setPlaying(false); }}
              className="p-2 rounded-lg hover:bg-[var(--color-surface-2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="Last screenshot"
            >
              <SkipForward className="w-4 h-4" />
            </button>

            <span className="text-xs text-[var(--color-text-muted)] ml-2">
              {screenshots.length ? current + 1 : 0} / {screenshots.length}
              {filter.type !== 'all' && allScreenshots.length !== screenshots.length && (
                <span className="opacity-60"> · {allScreenshots.length} total</span>
              )}
            </span>

            {/* Speed toggle */}
            <div className="ml-4 flex items-center gap-1">
              {PLAYBACK_SPEEDS.map((s) => (
                <button
                  key={s}
                  onClick={() => setSpeed(s)}
                  className={`px-2 py-1 text-xs rounded-md transition-colors ${
                    speed === s
                      ? 'bg-[var(--color-amber)]/20 text-[var(--color-amber)]'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>

          {/* Filter pills */}
          <FilterPills
            screenshots={allScreenshots}
            sessions={sessions}
            filter={filter}
            onChange={setFilter}
          />

          {/* Thumbnail scrubber (filtered list) */}
          <div className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
            <div className="flex gap-1 overflow-x-auto pb-2">
              {screenshots.map((sc, i) => (
                <button
                  key={sc.id}
                  onClick={() => { setCurrent(i); setPlaying(false); }}
                  className={`shrink-0 w-16 h-10 rounded-md overflow-hidden border-2 transition-colors ${
                    i === current
                      ? 'border-[var(--color-amber)]'
                      : 'border-transparent hover:border-[var(--color-border)]'
                  }`}
                >
                  <img
                    src={api.getScreenshotUrl(sc.id)}
                    alt=""
                    className="w-full h-full object-cover"
                    loading="lazy"
                  />
                </button>
              ))}
            </div>
          </div>

          {/* Hour grid — click a card to jump to that hour */}
          <div>
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
              Hour by hour
            </div>
            <HourGrid
              screenshots={allScreenshots}
              sessions={sessions}
              currentIdx={
                shot ? allScreenshots.findIndex((s) => s.id === shot.id) : 0
              }
              onSeek={seekByFullIdx}
            />
          </div>
        </>
      )}
    </div>
  );
}
