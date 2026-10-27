import { FolderGit2, Info } from 'lucide-react';
import { formatMinutes } from '../hooks';

/**
 * Per-project time breakdown for a day. Each row shows total time and a
 * two-tone bar visualizing direct (capture-time signal) vs context-
 * inherited minutes. Unattributed time gets its own row at the bottom so
 * the math always sums to the user's tracked hours.
 *
 * Props:
 *   projects             — [{name, minutes, direct_minutes, inherited_minutes}]
 *   unattributedMinutes  — int
 */
export default function ProjectsCard({ projects, unattributedMinutes }) {
  const totalAttributed = projects.reduce((sum, p) => sum + p.minutes, 0);
  const totalAll = totalAttributed + unattributedMinutes;
  const maxBar = Math.max(...projects.map((p) => p.minutes), 1);

  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] flex items-center gap-2">
          <FolderGit2 className="w-4 h-4 text-[var(--color-amber)]" />
          Projects
        </h2>
        <div className="text-[10px] text-[var(--color-text-muted)] flex items-center gap-2">
          <span title="Direct = project signal captured at this moment. Context = inherited from nearby work within 10 min.">
            <span className="inline-block w-2 h-2 rounded-sm align-middle mr-1 bg-[var(--color-amber)]" />
            direct
            <span className="inline-block w-2 h-2 rounded-sm align-middle mr-1 ml-2 bg-[var(--color-amber)]/30" />
            context
          </span>
          <Info className="w-3 h-3 opacity-60" />
        </div>
      </header>

      {projects.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          No project signals captured today.
        </p>
      ) : (
        <ul className="space-y-2">
          {projects.map((p) => {
            const directPct = p.minutes > 0 ? (p.direct_minutes / p.minutes) * 100 : 0;
            const barW = (p.minutes / maxBar) * 100;
            return (
              <li key={p.name} className="text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span
                    className="font-medium truncate"
                    title={`${p.direct_minutes}m direct · ${p.inherited_minutes}m via context`}
                  >
                    {p.name}
                  </span>
                  <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
                    {formatMinutes(p.minutes)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-[var(--color-surface-2)] overflow-hidden">
                  <div
                    className="h-full flex"
                    style={{ width: `${barW}%` }}
                  >
                    <div
                      className="h-full bg-[var(--color-amber)]"
                      style={{ width: `${directPct}%` }}
                    />
                    <div
                      className="h-full bg-[var(--color-amber)]/30"
                      style={{ width: `${100 - directPct}%` }}
                    />
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {unattributedMinutes > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--color-border)] flex items-center justify-between text-xs text-[var(--color-text-muted)]">
          <span
            title="Time not tied to any detected project (browsing without project context, communication far from any project signal, etc)."
          >
            Unattributed
          </span>
          <span className="tabular-nums">
            {formatMinutes(unattributedMinutes)}
            {totalAll > 0 && (
              <span className="opacity-60 ml-1.5">
                ({Math.round((unattributedMinutes / totalAll) * 100)}%)
              </span>
            )}
          </span>
        </div>
      )}
    </section>
  );
}
