import { useState } from 'react';
import { ThumbsUp, ThumbsDown, Tag, Clock } from 'lucide-react';
import { api } from '../api';
import { formatTime, formatDuration, CATEGORY_COLOR_BY_KEY } from '../hooks';
import { useToast } from './Toast';

export default function SessionCard({ session, onTagged }) {
  const toast = useToast();
  const [tagging, setTagging] = useState(false);
  const s = session;
  const color = CATEGORY_COLOR_BY_KEY[s.category] || '#6b7280';

  async function handleTag(productive) {
    setTagging(true);
    try {
      await api.tagSession(s, { productive });
      onTagged?.();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setTagging(false);
    }
  }

  async function handleCustomTag() {
    const tag = prompt('Enter a tag for this session:');
    if (!tag) return;
    setTagging(true);
    try {
      await api.tagSession(s, { tag });
      onTagged?.();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setTagging(false);
    }
  }

  return (
    <div className="flex items-center gap-4 bg-[var(--color-surface)] rounded-xl px-4 py-3 border border-[var(--color-border)] group">
      {/* Color bar */}
      <div className="w-1 h-10 rounded-full shrink-0" style={{ background: color }} />

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm truncate">{s.app_name}</span>
          <span
            className="text-xs px-1.5 py-0.5 rounded-full"
            style={{ background: `${color}20`, color }}
          >
            {s.category}
          </span>
          {s.is_productive === true && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-900/50 text-emerald-400">
              productive
            </span>
          )}
          {s.is_productive === false && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-900/50 text-red-400">
              distraction
            </span>
          )}
          {s.tag && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-[var(--color-surface-3)] text-[var(--color-text-muted)]">
              {s.tag}
            </span>
          )}
        </div>
        {s.domains?.length > 0 ? (
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
            {s.domains.slice(0, 4).join(', ')}
            {s.domains.length > 4 && ` +${s.domains.length - 4} more`}
          </div>
        ) : (
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
            {s.window_titles?.slice(0, 2).join(' / ') || 'No title'}
          </div>
        )}
      </div>

      {/* Time */}
      <div className="text-right shrink-0">
        <div className="text-sm font-medium flex items-center gap-1 justify-end">
          <Clock className="w-3.5 h-3.5 text-[var(--color-text-muted)]" />
          {s.duration_display || formatDuration(s.duration_seconds)}
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          {formatTime(s.start_time)} - {formatTime(s.end_time)}
        </div>
      </div>

      {/* Tag buttons */}
      <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => handleTag(true)}
          disabled={tagging}
          className={`p-1.5 rounded-lg transition-colors ${
            s.is_productive === true
              ? 'bg-emerald-900/50 text-emerald-400'
              : 'hover:bg-emerald-900/30 text-[var(--color-text-muted)] hover:text-emerald-400'
          }`}
          title="Mark productive"
        >
          <ThumbsUp className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => handleTag(false)}
          disabled={tagging}
          className={`p-1.5 rounded-lg transition-colors ${
            s.is_productive === false
              ? 'bg-red-900/50 text-red-400'
              : 'hover:bg-red-900/30 text-[var(--color-text-muted)] hover:text-red-400'
          }`}
          title="Mark distraction"
        >
          <ThumbsDown className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleCustomTag}
          disabled={tagging}
          className="p-1.5 rounded-lg hover:bg-[var(--color-surface-3)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          title="Add custom tag"
        >
          <Tag className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
