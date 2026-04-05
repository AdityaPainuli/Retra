/**
 * Retra API client — talks to FastAPI backend.
 */

const BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Day
  getToday: () => request('/today'),
  getDay: (date) => request(`/day/${date}`),

  // Timeline
  getTimeline: (date) => request(`/timeline/${date}`),

  // Week
  getWeek: (endDate) =>
    request(`/week${endDate ? `?end_date=${endDate}` : ''}`),

  // AI summary
  generateSummary: (date) => request(`/summarize/${date}`, { method: 'POST' }),

  // Obsidian export
  exportJournal: (date) => request(`/export/${date}`, { method: 'POST' }),

  // Screenshots
  getScreenshotUrl: (id) => `${BASE}/screenshot/${id}`,

  // Session tagging
  tagSession: (session, { productive, tag } = {}) => {
    const params = new URLSearchParams();
    params.set('start_time', session.start_time);
    params.set('app_name', session.app_name);
    if (productive !== undefined) params.set('productive', productive);
    if (tag !== undefined) params.set('tag', tag);
    return request(`/sessions/tag?${params}`, { method: 'POST' });
  },

  // Health
  health: () => request('/health'),

  // Insights
  getInsights: (days = 30) => request(`/insights/trends?days=${days}`),

  // Daily Goals
  getGoals: (date) => request(`/goals/${date}`),
  setGoal: (date, category, targetMinutes) =>
    request(`/goals/${date}?category=${category}&target_minutes=${targetMinutes}`, { method: 'POST' }),
  deleteGoal: (date, category) =>
    request(`/goals/${date}?category=${category}`, { method: 'DELETE' }),

  // Comparison
  compareDays: (dateA, dateB) => request(`/compare?date_a=${dateA}&date_b=${dateB}`),

  // Heatmap
  getHeatmap: (days = 180) => request(`/heatmap?days=${days}`),

  // URL tracking
  getUrlStats: (date) => request(`/urls/${date}`),
  getUrlRangeStats: (start, end) => request(`/urls/range/stats?start=${start}&end=${end}`),

  // Settings
  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', {
    method: 'PUT',
    body: JSON.stringify(data),
  }),
};
