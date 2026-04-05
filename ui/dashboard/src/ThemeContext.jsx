import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const ThemeContext = createContext(null);

const DEFAULT_THEME = {
  // Accent
  accent: '#f59e0b',
  accentDim: '#78350f',
  // Surfaces
  bg: '#0f0f0f',
  surface: '#1a1a1a',
  surface2: '#242424',
  surface3: '#2e2e2e',
  border: '#333333',
  // Text
  text: '#e5e5e5',
  textMuted: '#888888',
  // Category colors
  catDeepWork: '#f59e0b',
  catCommunication: '#3b82f6',
  catBrowsing: '#8b5cf6',
  catEntertainment: '#ef4444',
  catWriting: '#10b981',
  catLearning: '#06b6d4',
  catOther: '#6b7280',
  catIdle: '#374151',
  // UI
  fontSize: 14,
  sidebarWidth: 224,
  borderRadius: 12,
  compactMode: false,
};

const PRESETS = {
  amber: { accent: '#f59e0b', accentDim: '#78350f', label: 'Amber', emoji: '🟡' },
  violet: { accent: '#8b5cf6', accentDim: '#4c1d95', label: 'Violet', emoji: '🟣' },
  rose: { accent: '#f43f5e', accentDim: '#881337', label: 'Rose', emoji: '🔴' },
  emerald: { accent: '#10b981', accentDim: '#064e3b', label: 'Emerald', emoji: '🟢' },
  cyan: { accent: '#06b6d4', accentDim: '#164e63', label: 'Cyan', emoji: '🔵' },
  orange: { accent: '#f97316', accentDim: '#7c2d12', label: 'Orange', emoji: '🟠' },
  pink: { accent: '#ec4899', accentDim: '#831843', label: 'Pink', emoji: '💗' },
  lime: { accent: '#84cc16', accentDim: '#3f6212', label: 'Lime', emoji: '💚' },
  sky: { accent: '#0ea5e9', accentDim: '#0c4a6e', label: 'Sky', emoji: '🩵' },
  white: { accent: '#e5e5e5', accentDim: '#525252', label: 'Silver', emoji: '⚪' },
};

const SURFACE_PRESETS = {
  midnight: { bg: '#0f0f0f', surface: '#1a1a1a', surface2: '#242424', surface3: '#2e2e2e', border: '#333333', text: '#e5e5e5', textMuted: '#888888', label: 'Midnight' },
  charcoal: { bg: '#121218', surface: '#1c1c24', surface2: '#26262e', surface3: '#303038', border: '#3a3a42', text: '#e5e5e5', textMuted: '#8888a0', label: 'Charcoal' },
  ocean: { bg: '#0a1118', surface: '#111a22', surface2: '#1a242e', surface3: '#223040', border: '#2a3a4a', text: '#d8e8f0', textMuted: '#6888a0', label: 'Ocean' },
  forest: { bg: '#0a110e', surface: '#111a15', surface2: '#1a241e', surface3: '#223028', border: '#2a3a30', text: '#d8f0e0', textMuted: '#68a080', label: 'Forest' },
  amoled: { bg: '#000000', surface: '#0a0a0a', surface2: '#141414', surface3: '#1e1e1e', border: '#282828', text: '#e5e5e5', textMuted: '#777777', label: 'AMOLED' },
};

function applyTheme(theme) {
  const root = document.documentElement;
  root.style.setProperty('--color-amber', theme.accent);
  root.style.setProperty('--color-amber-dim', theme.accentDim);
  root.style.setProperty('--color-bg', theme.bg);
  root.style.setProperty('--color-surface', theme.surface);
  root.style.setProperty('--color-surface-2', theme.surface2);
  root.style.setProperty('--color-surface-3', theme.surface3);
  root.style.setProperty('--color-border', theme.border);
  root.style.setProperty('--color-text', theme.text);
  root.style.setProperty('--color-text-muted', theme.textMuted);
  root.style.setProperty('--color-deep-work', theme.catDeepWork);
  root.style.setProperty('--color-communication', theme.catCommunication);
  root.style.setProperty('--color-browsing', theme.catBrowsing);
  root.style.setProperty('--color-entertainment', theme.catEntertainment);
  root.style.setProperty('--color-writing', theme.catWriting);
  root.style.setProperty('--color-learning', theme.catLearning);
  root.style.setProperty('--color-other', theme.catOther);
  root.style.setProperty('--color-idle', theme.catIdle);
  root.style.fontSize = `${theme.fontSize}px`;
  document.body.style.background = theme.bg;
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    try {
      const saved = localStorage.getItem('retra-theme');
      return saved ? { ...DEFAULT_THEME, ...JSON.parse(saved) } : DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  });

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((updates) => {
    setThemeState(prev => {
      const next = { ...prev, ...updates };
      localStorage.setItem('retra-theme', JSON.stringify(next));
      return next;
    });
  }, []);

  const resetTheme = useCallback(() => {
    localStorage.removeItem('retra-theme');
    setThemeState(DEFAULT_THEME);
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resetTheme, PRESETS, SURFACE_PRESETS, DEFAULT_THEME }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
