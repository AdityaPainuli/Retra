export default function Loading({ message = 'Loading...' }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-[var(--color-amber)]/30 border-t-[var(--color-amber)] rounded-full animate-spin" />
        <span className="text-sm text-[var(--color-text-muted)]">{message}</span>
      </div>
    </div>
  );
}

export function ErrorMessage({ message, onRetry }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="flex flex-col items-center gap-3 text-center">
        <span className="text-red-400 text-sm">{message}</span>
        {onRetry && (
          <button
            onClick={onRetry}
            className="px-4 py-1.5 text-sm bg-[var(--color-surface-2)] rounded-lg hover:bg-[var(--color-surface-3)] transition-colors"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
