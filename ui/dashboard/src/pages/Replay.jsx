import { useState, useEffect, useCallback, useRef } from 'react';
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

const PLAYBACK_SPEEDS = [1, 2, 5];

export default function Replay() {
  const { date, prev, next, goToday, isToday } = useDate();
  const { data, loading, error, refetch } = useApi(() => api.getDay(date), [date]);
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const timerRef = useRef(null);

  const screenshots = data?.screenshots || [];

  // Auto-playback
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

  // Reset index on date change
  useEffect(() => {
    setCurrent(0);
    setPlaying(false);
  }, [date]);

  // Keyboard navigation
  const handleKey = useCallback(
    (e) => {
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

  if (loading) return <Loading message="Loading screenshots..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  const shot = screenshots[current];

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-4">
      {/* Header */}
      <header className="flex items-center justify-between">
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
        </div>
        {!isToday && (
          <button onClick={goToday} className="text-xs text-[var(--color-amber)] hover:underline">
            Back to today
          </button>
        )}
      </header>

      {screenshots.length === 0 ? (
        <div className="flex items-center justify-center h-96 bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)]">
          <p className="text-[var(--color-text-muted)]">No screenshots captured for this day</p>
        </div>
      ) : (
        <>
          {/* Main screenshot viewer */}
          <div className="relative bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] overflow-hidden">
            <img
              src={api.getScreenshotUrl(shot.id)}
              alt={`Screenshot at ${shot.timestamp}`}
              className="w-full h-auto max-h-[65vh] object-contain bg-black"
            />
            {/* Overlay info */}
            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-4">
              <div className="flex items-center justify-between text-white">
                <div>
                  <div className="font-medium text-sm">{shot.app_name}</div>
                  <div className="text-xs opacity-70 truncate max-w-md">{shot.window_title}</div>
                </div>
                <div className="text-sm font-mono">{formatTime(shot.timestamp)}</div>
              </div>
            </div>
          </div>

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
              className="p-3 rounded-full bg-[var(--color-amber)] text-black hover:bg-[var(--color-amber)]/90 transition-colors"
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
              {current + 1} / {screenshots.length}
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

          {/* Timeline scrubber */}
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
        </>
      )}
    </div>
  );
}
