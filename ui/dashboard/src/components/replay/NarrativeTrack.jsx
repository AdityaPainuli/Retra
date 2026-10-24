import { useEffect, useMemo, useState } from 'react';
import { Sparkles, RefreshCw } from 'lucide-react';
import { api } from '../../api';

/**
 * Synced caption strip beneath the screenshot.
 *
 *   - On first load, GET /api/narrative/{date}. If cached, show beats.
 *   - If no cache, show a "Generate" button. POST regenerates on click.
 *   - Active beat = latest beat whose time <= current screenshot's time.
 *
 * Props:
 *   date         — YYYY-MM-DD
 *   screenshot   — current screenshot (for time matching)
 */
export default function NarrativeTrack({ date, screenshot }) {
  const [beats, setBeats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState(null);

  // Fetch on date change
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.getNarrative(date)
      .then((r) => {
        if (!cancelled) setBeats(r.beats || null);
      })
      .catch(() => { if (!cancelled) setBeats(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [date]);

  async function regenerate() {
    setGenerating(true);
    setError(null);
    try {
      const r = await api.generateNarrative(date);
      setBeats(r.beats || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  // Active beat for the current screenshot
  const activeBeat = useMemo(() => {
    if (!beats?.length || !screenshot) return null;
    const t = new Date(screenshot.timestamp);
    const cur = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    let active = null;
    for (const b of beats) {
      if (b.time <= cur) active = b;
      else break;
    }
    return active;
  }, [beats, screenshot]);

  if (loading) {
    return (
      <div className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-3 text-xs text-[var(--color-text-muted)] italic">
        Loading narrative...
      </div>
    );
  }

  if (!beats?.length) {
    return (
      <div className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-3 flex items-center justify-between gap-3">
        <div className="text-xs text-[var(--color-text-muted)]">
          Generate an AI narrative of this day to see captions synced to playback.
        </div>
        <button
          onClick={regenerate}
          disabled={generating}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-[var(--color-amber)] text-black hover:bg-[var(--color-amber)]/90 disabled:opacity-50 transition-colors"
        >
          <Sparkles className="w-3 h-3" />
          {generating ? 'Generating...' : 'Generate narrative'}
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    );
  }

  return (
    <div className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-3 flex items-start gap-3">
      <Sparkles className="w-3.5 h-3.5 text-[var(--color-amber)] mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-xs">
          {activeBeat ? (
            <>
              <span className="font-mono text-[var(--color-amber)] mr-2">
                {activeBeat.time}
              </span>
              <span className="text-[var(--color-text)]">{activeBeat.text}</span>
            </>
          ) : (
            <span className="text-[var(--color-text-muted)] italic">
              No beat yet at this point in the day
            </span>
          )}
        </div>
      </div>
      <button
        onClick={regenerate}
        disabled={generating}
        title="Regenerate"
        className="p-1 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-50"
      >
        <RefreshCw className={`w-3 h-3 ${generating ? 'animate-spin' : ''}`} />
      </button>
    </div>
  );
}
