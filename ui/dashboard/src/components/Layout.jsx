import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  CalendarDays,
  Play,
  TrendingUp,
  ScanEye,
  ArrowLeftRight,
  Grid3x3,
  Globe,
  Settings,
} from 'lucide-react';
import SettingsPanel from './SettingsPanel';

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Today' },
  { to: '/week', icon: CalendarDays, label: 'Week' },
  { to: '/heatmap', icon: Grid3x3, label: 'Heatmap' },
  { to: '/replay', icon: Play, label: 'Replay' },
  { to: '/insights', icon: TrendingUp, label: 'Insights' },
  { to: '/compare', icon: ArrowLeftRight, label: 'Compare' },
  { to: '/urls', icon: Globe, label: 'URLs' },
];

export default function Layout() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 bg-[var(--color-surface)] border-r border-[var(--color-border)] flex flex-col">
        <div className="flex items-center gap-2 px-5 py-5">
          <ScanEye className="w-6 h-6 text-[var(--color-amber)]" />
          <span className="text-lg font-semibold tracking-tight">Retra</span>
        </div>

        <nav className="flex-1 px-3 space-y-1">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-[var(--color-amber)]/15 text-[var(--color-amber)]'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)]'
                }`
              }
            >
              <Icon className="w-4.5 h-4.5" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Bottom area */}
        <div className="px-3 pb-3">
          <button
            onClick={() => setSettingsOpen(true)}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)] transition-colors"
          >
            <Settings className="w-4.5 h-4.5" />
            Settings
          </button>
        </div>

        <div className="px-5 py-3 text-xs text-[var(--color-text-muted)] border-t border-[var(--color-border)]">
          All data stays local
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>

      {/* Settings panel */}
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
