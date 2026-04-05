import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  X, Save, FolderOpen, Shield, Camera, Brain, BookOpen,
  Clock, Eye, EyeOff, Trash2, Monitor, Palette, Database,
  ExternalLink, ChevronRight, Check, RotateCcw, Paintbrush, Info,
} from 'lucide-react';
import { api } from '../api';
import { useTheme } from '../ThemeContext';

const TABS = [
  { id: 'appearance', label: 'Appearance', icon: Paintbrush },
  { id: 'general', label: 'General', icon: Monitor },
  { id: 'capture', label: 'Capture', icon: Camera },
  { id: 'privacy', label: 'Privacy', icon: Shield },
  { id: 'ai', label: 'AI', icon: Brain },
  { id: 'obsidian', label: 'Obsidian', icon: BookOpen },
  { id: 'about', label: 'About', icon: Info },
];

export default function SettingsPanel({ open, onClose }) {
  const [tab, setTab] = useState('appearance');
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (open) {
      setLoading(true);
      api.getSettings()
        .then(setSettings)
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [open]);

  function update(section, key, value) {
    setSettings(prev => ({
      ...prev,
      [section]: { ...prev[section], [key]: value },
    }));
    setDirty(true);
    setSaved(false);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await api.updateSettings(settings);
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[80] bg-black/50 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Panel */}
          <motion.div
            initial={{ x: '-100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '-100%', opacity: 0 }}
            transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            className="fixed left-0 top-0 bottom-0 z-[90] w-[560px] max-w-[90vw] bg-[var(--color-bg)] border-r border-[var(--color-border)] flex overflow-hidden"
          >
            {/* Tab sidebar */}
            <div className="w-44 shrink-0 bg-[var(--color-surface)] border-r border-[var(--color-border)] flex flex-col">
              <div className="px-4 py-5 flex items-center justify-between">
                <h2 className="text-base font-semibold">Settings</h2>
                <button onClick={onClose} className="p-1 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors">
                  <X className="w-4 h-4 text-[var(--color-text-muted)]" />
                </button>
              </div>
              <nav className="flex-1 px-2 space-y-0.5">
                {TABS.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => setTab(id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                      tab === id
                        ? 'bg-[var(--color-amber)]/15 text-[var(--color-amber)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)]'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    {label}
                  </button>
                ))}
              </nav>

              {/* Save button */}
              <div className="p-3 border-t border-[var(--color-border)]">
                <button
                  onClick={handleSave}
                  disabled={!dirty || saving}
                  className={`w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    saved
                      ? 'bg-emerald-500/15 text-emerald-400'
                      : dirty
                        ? 'bg-[var(--color-amber)] text-black hover:bg-[var(--color-amber)]/90'
                        : 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]'
                  }`}
                >
                  {saved ? <><Check className="w-4 h-4" /> Saved</> :
                   saving ? 'Saving...' :
                   <><Save className="w-4 h-4" /> Save Changes</>}
                </button>
              </div>
            </div>

            {/* Content area */}
            <div className="flex-1 overflow-y-auto p-6">
              {loading ? (
                <div className="flex items-center justify-center h-full text-[var(--color-text-muted)]">
                  Loading settings...
                </div>
              ) : settings ? (
                <AnimatePresence mode="wait">
                  <motion.div
                    key={tab}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ duration: 0.15 }}
                  >
                    {tab === 'appearance' && <AppearanceTab />}
                    {tab === 'general' && <GeneralTab settings={settings} update={update} />}
                    {tab === 'capture' && <CaptureTab settings={settings} update={update} />}
                    {tab === 'privacy' && <PrivacyTab settings={settings} update={update} />}
                    {tab === 'ai' && <AITab settings={settings} update={update} />}
                    {tab === 'obsidian' && <ObsidianTab settings={settings} update={update} />}
                    {tab === 'about' && <AboutTab />}
                  </motion.div>
                </AnimatePresence>
              ) : (
                <div className="text-[var(--color-text-muted)]">Failed to load settings</div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

// ── Reusable form elements ──

function Section({ title, description, children }) {
  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold mb-0.5">{title}</h3>
      {description && <p className="text-xs text-[var(--color-text-muted)] mb-3">{description}</p>}
      <div className="space-y-3 mt-3">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div>
      <label className="text-xs font-medium text-[var(--color-text-muted)] mb-1 block">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-[var(--color-text-muted)]/60 mt-1">{hint}</p>}
    </div>
  );
}

function TextInput({ value, onChange, placeholder, type = 'text' }) {
  return (
    <input
      type={type}
      value={value ?? ''}
      onChange={(e) => onChange(type === 'number' ? Number(e.target.value) : e.target.value)}
      placeholder={placeholder}
      className="w-full px-3 py-2 text-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-amber)]/50 transition-colors"
    />
  );
}

function NumberInput({ value, onChange, min, max, step = 1 }) {
  return (
    <input
      type="number"
      value={value ?? ''}
      onChange={(e) => onChange(Number(e.target.value))}
      min={min}
      max={max}
      step={step}
      className="w-full px-3 py-2 text-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-amber)]/50 transition-colors"
    />
  );
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className="flex items-center gap-3"
    >
      <div className={`relative w-9 h-5 rounded-full transition-colors ${checked ? 'bg-[var(--color-amber)]' : 'bg-[var(--color-surface-3)]'}`}>
        <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${checked ? 'translate-x-4' : 'translate-x-0.5'}`} />
      </div>
      <span className="text-sm">{label}</span>
    </button>
  );
}

function SelectInput({ value, onChange, options }) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      className="w-full px-3 py-2 text-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-amber)]/50 transition-colors appearance-none cursor-pointer"
    >
      {options.map(opt => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  );
}

function TagList({ tags, onRemove, onAdd }) {
  const [input, setInput] = useState('');
  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {(tags || []).map((tag, i) => (
          <span key={i} className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-[var(--color-surface-2)] rounded-md border border-[var(--color-border)]">
            {tag}
            <button onClick={() => onRemove(i)} className="text-[var(--color-text-muted)] hover:text-red-400 transition-colors">
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && input.trim()) { onAdd(input.trim()); setInput(''); } }}
          placeholder="Add item and press Enter..."
          className="flex-1 px-3 py-1.5 text-xs bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-amber)]/50 transition-colors"
        />
      </div>
    </div>
  );
}

// ── Tab content panels ──

function ColorSwatch({ color, selected, onClick, label, size = 'md' }) {
  const sz = size === 'lg' ? 'w-10 h-10' : 'w-7 h-7';
  return (
    <button
      onClick={onClick}
      className={`group flex flex-col items-center gap-1`}
      title={label}
    >
      <div
        className={`${sz} rounded-full border-2 transition-all ${
          selected ? 'border-white scale-110 shadow-lg' : 'border-transparent hover:border-white/30 hover:scale-105'
        }`}
        style={{ backgroundColor: color, boxShadow: selected ? `0 0 12px ${color}50` : undefined }}
      />
      {label && size === 'lg' && (
        <span className="text-[10px] text-[var(--color-text-muted)]">{label}</span>
      )}
    </button>
  );
}

function ColorPicker({ label, color, onChange }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-[var(--color-text-muted)]">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono text-[var(--color-text-muted)]">{color}</span>
        <label className="relative cursor-pointer">
          <div className="w-7 h-7 rounded-lg border border-[var(--color-border)] hover:border-white/30 transition-colors" style={{ backgroundColor: color }} />
          <input
            type="color"
            value={color}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
          />
        </label>
      </div>
    </div>
  );
}

function MiniPreview({ theme }) {
  return (
    <div className="rounded-xl overflow-hidden border border-[var(--color-border)] text-[10px]" style={{ background: theme.bg }}>
      <div className="flex h-24">
        <div className="w-14 shrink-0 p-1.5 flex flex-col gap-0.5" style={{ background: theme.surface, borderRight: `1px solid ${theme.border}` }}>
          <div className="h-1.5 w-8 rounded-sm" style={{ background: theme.accent }} />
          <div className="h-1.5 w-6 rounded-sm mt-1" style={{ background: theme.textMuted + '40' }} />
          <div className="h-1.5 w-7 rounded-sm" style={{ background: theme.textMuted + '40' }} />
          <div className="h-1.5 w-5 rounded-sm" style={{ background: theme.textMuted + '40' }} />
        </div>
        <div className="flex-1 p-2 space-y-1.5">
          <div className="flex gap-1">
            <div className="h-8 flex-1 rounded" style={{ background: theme.surface, border: `1px solid ${theme.border}` }}>
              <div className="h-1 w-6 m-1.5 rounded-sm" style={{ background: theme.accent }} />
            </div>
            <div className="h-8 flex-1 rounded" style={{ background: theme.surface, border: `1px solid ${theme.border}` }}>
              <div className="h-1 w-8 m-1.5 rounded-sm" style={{ background: theme.catCommunication }} />
            </div>
          </div>
          <div className="h-5 rounded flex gap-0.5 overflow-hidden" style={{ background: theme.surface2 }}>
            <div className="h-full" style={{ background: theme.catDeepWork, width: '40%' }} />
            <div className="h-full" style={{ background: theme.catBrowsing, width: '25%' }} />
            <div className="h-full" style={{ background: theme.catCommunication, width: '20%' }} />
            <div className="h-full" style={{ background: theme.catEntertainment, width: '15%' }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function AppearanceTab() {
  const { theme, setTheme, resetTheme, PRESETS, SURFACE_PRESETS, DEFAULT_THEME } = useTheme();

  return (
    <>
      {/* Live preview */}
      <Section title="Preview">
        <MiniPreview theme={theme} />
      </Section>

      {/* Accent color */}
      <Section title="Accent Color" description="Primary color used for highlights, active states, and focus indicators.">
        <div className="flex flex-wrap gap-2">
          {Object.entries(PRESETS).map(([key, preset]) => (
            <ColorSwatch
              key={key}
              color={preset.accent}
              selected={theme.accent === preset.accent}
              onClick={() => setTheme({ accent: preset.accent, accentDim: preset.accentDim })}
              label={preset.label}
              size="lg"
            />
          ))}
        </div>
        <div className="mt-3">
          <ColorPicker label="Custom accent" color={theme.accent} onChange={(c) => setTheme({ accent: c })} />
        </div>
      </Section>

      {/* Background theme */}
      <Section title="Background Theme" description="Surface and background colors.">
        <div className="grid grid-cols-5 gap-2">
          {Object.entries(SURFACE_PRESETS).map(([key, preset]) => (
            <button
              key={key}
              onClick={() => setTheme({
                bg: preset.bg, surface: preset.surface, surface2: preset.surface2,
                surface3: preset.surface3, border: preset.border, text: preset.text, textMuted: preset.textMuted,
              })}
              className={`flex flex-col items-center gap-1.5 p-2 rounded-lg border transition-all ${
                theme.bg === preset.bg && theme.surface === preset.surface
                  ? 'border-[var(--color-amber)] bg-[var(--color-amber)]/5'
                  : 'border-[var(--color-border)] hover:border-[var(--color-text-muted)]/30'
              }`}
            >
              <div className="flex gap-0.5 w-full">
                <div className="h-4 flex-1 rounded-sm" style={{ background: preset.bg }} />
                <div className="h-4 flex-1 rounded-sm" style={{ background: preset.surface }} />
                <div className="h-4 flex-1 rounded-sm" style={{ background: preset.surface2 }} />
              </div>
              <span className="text-[10px] text-[var(--color-text-muted)]">{preset.label}</span>
            </button>
          ))}
        </div>
        <div className="mt-3 space-y-2">
          <ColorPicker label="Background" color={theme.bg} onChange={(c) => setTheme({ bg: c })} />
          <ColorPicker label="Surface" color={theme.surface} onChange={(c) => setTheme({ surface: c })} />
          <ColorPicker label="Surface 2" color={theme.surface2} onChange={(c) => setTheme({ surface2: c })} />
          <ColorPicker label="Border" color={theme.border} onChange={(c) => setTheme({ border: c })} />
          <ColorPicker label="Text" color={theme.text} onChange={(c) => setTheme({ text: c })} />
          <ColorPicker label="Muted text" color={theme.textMuted} onChange={(c) => setTheme({ textMuted: c })} />
        </div>
      </Section>

      {/* Category colors */}
      <Section title="Category Colors" description="Colors for each activity category in charts and timeline.">
        <div className="space-y-2">
          <ColorPicker label="Deep Work" color={theme.catDeepWork} onChange={(c) => setTheme({ catDeepWork: c })} />
          <ColorPicker label="Communication" color={theme.catCommunication} onChange={(c) => setTheme({ catCommunication: c })} />
          <ColorPicker label="Browsing" color={theme.catBrowsing} onChange={(c) => setTheme({ catBrowsing: c })} />
          <ColorPicker label="Entertainment" color={theme.catEntertainment} onChange={(c) => setTheme({ catEntertainment: c })} />
          <ColorPicker label="Writing" color={theme.catWriting} onChange={(c) => setTheme({ catWriting: c })} />
          <ColorPicker label="Learning" color={theme.catLearning} onChange={(c) => setTheme({ catLearning: c })} />
          <ColorPicker label="Other" color={theme.catOther} onChange={(c) => setTheme({ catOther: c })} />
        </div>
      </Section>

      {/* Typography & Layout */}
      <Section title="Typography & Layout">
        <Field label="Font size" hint={`${theme.fontSize}px`}>
          <div className="flex items-center gap-3">
            <span className="text-xs text-[var(--color-text-muted)]">Sm</span>
            <input
              type="range" min={11} max={18} step={1}
              value={theme.fontSize}
              onChange={(e) => setTheme({ fontSize: Number(e.target.value) })}
              className="flex-1 accent-[var(--color-amber)]"
            />
            <span className="text-xs text-[var(--color-text-muted)]">Lg</span>
          </div>
        </Field>
        <Field label="Border radius" hint={`${theme.borderRadius}px`}>
          <div className="flex items-center gap-3">
            <span className="text-xs text-[var(--color-text-muted)]">Sharp</span>
            <input
              type="range" min={0} max={24} step={2}
              value={theme.borderRadius}
              onChange={(e) => setTheme({ borderRadius: Number(e.target.value) })}
              className="flex-1 accent-[var(--color-amber)]"
            />
            <span className="text-xs text-[var(--color-text-muted)]">Round</span>
          </div>
        </Field>
      </Section>

      {/* Reset */}
      <div className="pt-2">
        <button
          onClick={resetTheme}
          className="flex items-center gap-2 px-3 py-2 text-xs text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-500/5 rounded-lg transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset to defaults
        </button>
      </div>
    </>
  );
}

function GeneralTab({ settings, update }) {
  return (
    <>
      <Section title="Data Storage" description="Where Retra stores your activity data and screenshots.">
        <Field label="Database location">
          <div className="flex items-center gap-2 px-3 py-2 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
            <Database className="w-4 h-4 text-[var(--color-text-muted)] shrink-0" />
            <span className="text-xs text-[var(--color-text-muted)] truncate">{settings.storage?.db_path}</span>
          </div>
        </Field>
        <Field label="Screenshots directory">
          <div className="flex items-center gap-2 px-3 py-2 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
            <FolderOpen className="w-4 h-4 text-[var(--color-text-muted)] shrink-0" />
            <span className="text-xs text-[var(--color-text-muted)] truncate">{settings.storage?.screenshots_dir}</span>
          </div>
        </Field>
      </Section>

      <Section title="Dashboard" description="Configure the web dashboard.">
        <Field label="Port" hint="Requires restart to take effect.">
          <NumberInput value={settings.dashboard?.port} onChange={(v) => update('dashboard', 'port', v)} min={1024} max={65535} />
        </Field>
      </Section>

      <Section title="Data Retention" description="How long to keep historical data before auto-cleanup.">
        <Field label="Retention period (days)" hint="Events, sessions, and screenshots older than this are deleted on daemon startup.">
          <NumberInput value={settings.privacy?.retention_days} onChange={(v) => update('privacy', 'retention_days', v)} min={7} max={365} />
        </Field>
      </Section>
    </>
  );
}

function CaptureTab({ settings, update }) {
  return (
    <>
      <Section title="Polling" description="How frequently Retra checks your active window.">
        <Field label="Poll interval (seconds)" hint="Lower = more detailed tracking, higher CPU usage.">
          <NumberInput value={settings.capture?.poll_interval} onChange={(v) => update('capture', 'poll_interval', v)} min={1} max={30} />
        </Field>
        <Field label="Idle threshold (seconds)" hint="How long without input before marking you as idle.">
          <NumberInput value={settings.capture?.idle_threshold} onChange={(v) => update('capture', 'idle_threshold', v)} min={60} max={1800} />
        </Field>
      </Section>

      <Section title="Screenshots" description="Periodic screenshots for visual replay of your day.">
        <Field label="Screenshot interval (seconds)" hint="Time between automatic screenshots. Default: 180s (3 min).">
          <NumberInput value={settings.capture?.screenshot_interval} onChange={(v) => update('capture', 'screenshot_interval', v)} min={30} max={600} />
        </Field>
        <Field label="JPEG quality (1–100)" hint="Lower = smaller files, slightly blurrier.">
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={10} max={100} step={5}
              value={settings.capture?.screenshot_quality ?? 40}
              onChange={(e) => update('capture', 'screenshot_quality', Number(e.target.value))}
              className="flex-1 accent-[var(--color-amber)]"
            />
            <span className="text-sm font-medium w-8 text-right">{settings.capture?.screenshot_quality ?? 40}</span>
          </div>
        </Field>
      </Section>
    </>
  );
}

function PrivacyTab({ settings, update }) {
  function removeBlockedApp(i) {
    const next = [...settings.privacy.blocked_apps];
    next.splice(i, 1);
    update('privacy', 'blocked_apps', next);
  }
  function addBlockedApp(app) {
    update('privacy', 'blocked_apps', [...(settings.privacy.blocked_apps || []), app]);
  }
  function removeBlockedUrl(i) {
    const next = [...settings.privacy.blocked_url_patterns];
    next.splice(i, 1);
    update('privacy', 'blocked_url_patterns', next);
  }
  function addBlockedUrl(url) {
    update('privacy', 'blocked_url_patterns', [...(settings.privacy.blocked_url_patterns || []), url]);
  }

  return (
    <>
      <Section title="Blocked Apps" description="Apps that will not be tracked. Window titles are replaced with [blocked].">
        <TagList tags={settings.privacy?.blocked_apps} onRemove={removeBlockedApp} onAdd={addBlockedApp} />
      </Section>

      <Section title="Blocked URL Patterns" description="URL patterns to exclude from tracking. Matches against page URLs and window titles.">
        <TagList tags={settings.privacy?.blocked_url_patterns} onRemove={removeBlockedUrl} onAdd={addBlockedUrl} />
      </Section>

      <Section title="Screenshots">
        <Toggle
          checked={settings.privacy?.blur_screenshots ?? false}
          onChange={(v) => update('privacy', 'blur_screenshots', v)}
          label="Blur screenshots for privacy"
        />
      </Section>
    </>
  );
}

function AITab({ settings, update }) {
  return (
    <>
      <Section title="AI Provider" description="Choose which AI generates your daily reflections.">
        <Field label="Provider">
          <SelectInput
            value={settings.ai?.provider}
            onChange={(v) => update('ai', 'provider', v)}
            options={[
              { value: 'claude', label: 'Claude (Anthropic)' },
              { value: 'ollama', label: 'Ollama (Local)' },
            ]}
          />
        </Field>
      </Section>

      {settings.ai?.provider === 'claude' && (
        <Section title="Claude Settings">
          <Field label="Model">
            <SelectInput
              value={settings.ai?.claude_model}
              onChange={(v) => update('ai', 'claude_model', v)}
              options={[
                { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
                { value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (faster, cheaper)' },
              ]}
            />
          </Field>
          <Field label="API Key Status">
            <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
              settings.ai?.has_api_key
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                : 'bg-red-500/10 border-red-500/20 text-red-400'
            }`}>
              {settings.ai?.has_api_key ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
              {settings.ai?.has_api_key ? 'API key configured' : 'No API key — set ANTHROPIC_API_KEY in .env'}
            </div>
          </Field>
        </Section>
      )}

      {settings.ai?.provider === 'ollama' && (
        <Section title="Ollama Settings">
          <Field label="Model" hint="Must be pulled locally via 'ollama pull'.">
            <TextInput value={settings.ai?.ollama_model} onChange={(v) => update('ai', 'ollama_model', v)} placeholder="llama3.1:8b" />
          </Field>
          <Field label="Server URL">
            <TextInput value={settings.ai?.ollama_url} onChange={(v) => update('ai', 'ollama_url', v)} placeholder="http://localhost:11434" />
          </Field>
        </Section>
      )}
    </>
  );
}

function ObsidianTab({ settings, update }) {
  return (
    <>
      <Section title="Vault" description="Where to export your daily journals.">
        <Field label="Vault path" hint="Absolute path to your Obsidian vault root.">
          <TextInput value={settings.obsidian?.vault_path} onChange={(v) => update('obsidian', 'vault_path', v)} placeholder="~/Documents/Obsidian/MyVault" />
        </Field>
        <Field label="Daily notes folder" hint="Subfolder inside the vault for Retra exports.">
          <TextInput value={settings.obsidian?.daily_notes_folder} onChange={(v) => update('obsidian', 'daily_notes_folder', v)} placeholder="Retra" />
        </Field>
      </Section>

      <Section title="Export Features" description="Journals include wikilinks for graph view.">
        <div className="grid grid-cols-2 gap-2">
          {['Apps', 'Projects', 'Categories', 'Domains'].map(type => (
            <div key={type} className="flex items-center gap-2 px-3 py-2 bg-[var(--color-surface)] rounded-lg border border-[var(--color-border)]">
              <Check className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-xs">[[{type}]] links</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-[var(--color-text-muted)]">
          Exports create linked notes for Apps, Projects, Categories, and Domains so Obsidian's graph view shows connections between your days.
        </p>
      </Section>
    </>
  );
}

function AboutTab() {
  return (
    <>
      <Section title="Retra" description="Your Day, In Focus.">
        <div className="space-y-3">
          <div className="flex items-center gap-3 px-4 py-3 bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)]">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-amber-400 to-orange-600 flex items-center justify-center shrink-0">
              <Eye className="w-6 h-6 text-black" />
            </div>
            <div>
              <div className="text-sm font-semibold">Retra v0.1.0</div>
              <div className="text-xs text-[var(--color-text-muted)]">Local-first screen time tracker with AI insights</div>
            </div>
          </div>
        </div>
      </Section>

      <Section title="Built by Aditya Painuli">
        <div className="space-y-2">
          <a
            href="https://www.linkedin.com/in/aditya-painuli-422996218/"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-3 px-4 py-3 bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] hover:border-[#0a66c2]/50 hover:bg-[#0a66c2]/5 transition-all group"
          >
            <div className="w-9 h-9 rounded-lg bg-[#0a66c2]/15 flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-[#0a66c2]" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
              </svg>
            </div>
            <div className="flex-1">
              <div className="text-sm font-medium">LinkedIn</div>
              <div className="text-xs text-[var(--color-text-muted)]">Connect with me</div>
            </div>
            <ExternalLink className="w-4 h-4 text-[var(--color-text-muted)] group-hover:text-[#0a66c2] transition-colors" />
          </a>

          <a
            href="https://x.com/aditya_painuli"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-3 px-4 py-3 bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] hover:border-white/20 hover:bg-white/5 transition-all group"
          >
            <div className="w-9 h-9 rounded-lg bg-white/10 flex items-center justify-center shrink-0">
              <svg className="w-4.5 h-4.5 text-white" viewBox="0 0 24 24" fill="currentColor">
                <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
              </svg>
            </div>
            <div className="flex-1">
              <div className="text-sm font-medium">X (Twitter)</div>
              <div className="text-xs text-[var(--color-text-muted)]">Follow for updates</div>
            </div>
            <ExternalLink className="w-4 h-4 text-[var(--color-text-muted)] group-hover:text-white transition-colors" />
          </a>
        </div>
      </Section>

      <Section title="Philosophy">
        <div className="space-y-2.5 text-xs text-[var(--color-text-muted)] leading-relaxed">
          <p><strong className="text-[var(--color-text)]">Local-first.</strong> All your data stays on your machine. No cloud, no accounts, no tracking.</p>
          <p><strong className="text-[var(--color-text)]">Private.</strong> Block any app or URL pattern. Delete your data anytime.</p>
          <p><strong className="text-[var(--color-text)]">Insightful.</strong> AI-powered reflections help you understand your digital habits.</p>
        </div>
      </Section>

      <div className="mt-6 pt-4 border-t border-[var(--color-border)]">
        <p className="text-[11px] text-[var(--color-text-muted)]/50 text-center">
          Made with focus in India
        </p>
      </div>
    </>
  );
}
