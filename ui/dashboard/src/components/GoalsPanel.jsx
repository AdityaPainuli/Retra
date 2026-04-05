import { useState } from 'react';
import { Plus, X, Target, Trophy } from 'lucide-react';
import { api } from '../api';
import { useApi, formatMinutes } from '../hooks';
import { useToast } from './Toast';

const GOAL_CATEGORIES = [
  { key: 'deep_work', label: 'Deep Work', color: '#f59e0b' },
  { key: 'entertainment', label: 'Entertainment', color: '#ef4444' },
  { key: 'communication', label: 'Communication', color: '#3b82f6' },
  { key: 'browsing', label: 'Browsing', color: '#8b5cf6' },
  { key: 'writing', label: 'Writing', color: '#10b981' },
  { key: 'learning', label: 'Learning', color: '#06b6d4' },
];

export default function GoalsPanel({ date }) {
  const { data, loading, refetch } = useApi(() => api.getGoals(date), [date]);
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [newCat, setNewCat] = useState('deep_work');
  const [newHours, setNewHours] = useState('');

  const goals = data?.goals || [];

  // Categories that don't have a goal yet
  const availableCategories = GOAL_CATEGORIES.filter(
    (c) => !goals.some((g) => g.category === c.key)
  );

  async function handleAdd(e) {
    e.preventDefault();
    const minutes = Math.round(parseFloat(newHours) * 60);
    if (!minutes || minutes <= 0) return;
    try {
      await api.setGoal(date, newCat, minutes);
      toast('Goal added', 'success');
      setAdding(false);
      setNewHours('');
      refetch();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function handleDelete(category) {
    try {
      await api.deleteGoal(date, category);
      refetch();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  if (loading) return null;

  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] flex items-center gap-2">
          <Target className="w-4 h-4 text-[var(--color-amber)]" />
          Daily Goals
        </h2>
        {availableCategories.length > 0 && !adding && (
          <button
            onClick={() => setAdding(true)}
            className="flex items-center gap-1 text-xs text-[var(--color-amber)] hover:underline"
          >
            <Plus className="w-3.5 h-3.5" /> Add Goal
          </button>
        )}
      </div>

      {goals.length === 0 && !adding && (
        <p className="text-xs text-[var(--color-text-muted)]">
          No goals set for today. Click "Add Goal" to set a target.
        </p>
      )}

      {/* Goal progress rings */}
      <div className="space-y-3">
        {goals.map((g) => {
          const cat = GOAL_CATEGORIES.find((c) => c.key === g.category);
          const color = cat?.color || '#6b7280';
          const label = cat?.label || g.category;
          const pct = g.progress;
          const completed = pct >= 100;

          return (
            <div key={g.category} className="flex items-center gap-3 group">
              {/* Progress ring */}
              <div className="relative w-11 h-11 shrink-0">
                <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                  <circle
                    cx="18" cy="18" r="14"
                    fill="none"
                    stroke="var(--color-surface-3)"
                    strokeWidth="3"
                  />
                  <circle
                    cx="18" cy="18" r="14"
                    fill="none"
                    stroke={color}
                    strokeWidth="3"
                    strokeDasharray={`${(Math.min(pct, 100) / 100) * 87.96} 87.96`}
                    strokeLinecap="round"
                    className="transition-all duration-500"
                  />
                </svg>
                {completed && (
                  <Trophy className="absolute inset-0 m-auto w-4 h-4 text-[var(--color-amber)]" />
                )}
                {!completed && (
                  <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold">
                    {pct}%
                  </span>
                )}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{label}</span>
                  {completed && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-900/50 text-emerald-400">
                      completed
                    </span>
                  )}
                </div>
                <div className="text-xs text-[var(--color-text-muted)]">
                  {formatMinutes(g.actual_minutes)} / {formatMinutes(g.target_minutes)}
                </div>
                {/* Progress bar */}
                <div className="h-1 bg-[var(--color-surface-3)] rounded-full mt-1 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.min(pct, 100)}%`,
                      backgroundColor: color,
                    }}
                  />
                </div>
              </div>

              {/* Delete */}
              <button
                onClick={() => handleDelete(g.category)}
                className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-[var(--color-surface-3)] text-[var(--color-text-muted)] transition-all"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}
      </div>

      {/* Add form */}
      {adding && (
        <form onSubmit={handleAdd} className="mt-3 flex items-end gap-2">
          <div className="flex-1">
            <label className="text-xs text-[var(--color-text-muted)] block mb-1">Category</label>
            <select
              value={newCat}
              onChange={(e) => setNewCat(e.target.value)}
              className="w-full bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-[var(--color-amber)]"
            >
              {availableCategories.map((c) => (
                <option key={c.key} value={c.key}>{c.label}</option>
              ))}
            </select>
          </div>
          <div className="w-24">
            <label className="text-xs text-[var(--color-text-muted)] block mb-1">Hours</label>
            <input
              type="number"
              step="0.5"
              min="0.5"
              max="16"
              value={newHours}
              onChange={(e) => setNewHours(e.target.value)}
              placeholder="e.g. 4"
              className="w-full bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-[var(--color-amber)]"
              autoFocus
            />
          </div>
          <button
            type="submit"
            className="px-3 py-1.5 text-sm bg-[var(--color-amber)] text-black rounded-lg font-medium hover:bg-[var(--color-amber)]/90"
          >
            Set
          </button>
          <button
            type="button"
            onClick={() => setAdding(false)}
            className="px-3 py-1.5 text-sm bg-[var(--color-surface-2)] rounded-lg hover:bg-[var(--color-surface-3)]"
          >
            Cancel
          </button>
        </form>
      )}
    </section>
  );
}
