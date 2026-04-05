import { useState, useEffect, useCallback } from 'react';

/**
 * Generic async data fetcher hook with loading/error states.
 * @param {Function} fetcher - async function that returns data
 * @param {Array} deps - dependency array for refetch
 * @param {object} options - { pollInterval: ms } to auto-refresh
 */
export function useApi(fetcher, deps = [], options = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = useCallback(() => {
    setLoading((prev) => prev || data === null);
    setError(null);
    fetcher()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, deps);

  useEffect(() => {
    refetch();
  }, [refetch]);

  // Auto-poll when pollInterval is set
  useEffect(() => {
    if (!options.pollInterval) return;
    const id = setInterval(() => {
      fetcher()
        .then(setData)
        .catch(() => {});
    }, options.pollInterval);
    return () => clearInterval(id);
  }, [refetch, options.pollInterval]);

  return { data, loading, error, refetch };
}

/**
 * Date navigation helper.
 * Auto-advances to the new day at midnight if the user is viewing "today".
 */
export function useDate(initial) {
  const [date, setDate] = useState(initial || todayStr());
  const [today, setToday] = useState(todayStr());

  // Check every 30s if the date rolled over midnight
  useEffect(() => {
    const timer = setInterval(() => {
      const now = todayStr();
      if (now !== today) {
        setToday(now);
        // If user was viewing "today", advance to the new day
        setDate((prev) => (prev === today ? now : prev));
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [today]);

  const prev = () => setDate((d) => offsetDate(d, -1));
  const next = () => setDate((d) => offsetDate(d, 1));
  const goToday = () => setDate(todayStr());
  const isToday = date === today;
  return { date, setDate, prev, next, goToday, isToday };
}

export function todayStr() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function offsetDate(dateStr, days) {
  const d = new Date(dateStr + 'T12:00:00');
  d.setDate(d.getDate() + days);
  return d.toISOString().split('T')[0];
}

export function formatDate(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}

export function formatTime(isoStr) {
  return new Date(isoStr).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

export function formatMinutes(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

function getCSSVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function getCategoryColors() {
  return {
    'Deep Work': getCSSVar('--color-deep-work') || '#f59e0b',
    'Communication': getCSSVar('--color-communication') || '#3b82f6',
    'Browsing': getCSSVar('--color-browsing') || '#8b5cf6',
    'Entertainment': getCSSVar('--color-entertainment') || '#ef4444',
    'Writing': getCSSVar('--color-writing') || '#10b981',
    'Learning': getCSSVar('--color-learning') || '#06b6d4',
    'Other': getCSSVar('--color-other') || '#6b7280',
  };
}

export function getCategoryColorByKey() {
  return {
    coding: getCSSVar('--color-deep-work') || '#f59e0b',
    communication: getCSSVar('--color-communication') || '#3b82f6',
    browsing: getCSSVar('--color-browsing') || '#8b5cf6',
    entertainment: getCSSVar('--color-entertainment') || '#ef4444',
    writing: getCSSVar('--color-writing') || '#10b981',
    learning: getCSSVar('--color-learning') || '#06b6d4',
    other: getCSSVar('--color-other') || '#6b7280',
    idle: getCSSVar('--color-idle') || '#374151',
  };
}

// Static fallbacks for places that import the constants directly
export const CATEGORY_COLORS = {
  'Deep Work': '#f59e0b',
  'Communication': '#3b82f6',
  'Browsing': '#8b5cf6',
  'Entertainment': '#ef4444',
  'Writing': '#10b981',
  'Learning': '#06b6d4',
  'Other': '#6b7280',
};

export const CATEGORY_COLOR_BY_KEY = {
  coding: '#f59e0b',
  communication: '#3b82f6',
  browsing: '#8b5cf6',
  entertainment: '#ef4444',
  writing: '#10b981',
  learning: '#06b6d4',
  other: '#6b7280',
  idle: '#374151',
};
