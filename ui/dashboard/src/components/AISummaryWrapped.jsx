import { useState, useEffect, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, ChevronLeft, ChevronRight, Sparkles, Clock, Target, Flame, Zap, Trophy, Monitor, Pause, Play } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Spotify Wrapped–style immersive AI summary viewer.
 * Opens as a fullscreen overlay with animated slides.
 * Tap center to pause/resume. Long text slides auto-pause and scroll.
 */

const GRADIENTS = [
  'linear-gradient(135deg, #0f0f0f 0%, #1a0a2e 50%, #16213e 100%)',
  'linear-gradient(135deg, #0a1628 0%, #1a0a2e 50%, #2d1b4e 100%)',
  'linear-gradient(135deg, #1a0a00 0%, #2d1200 50%, #0f0f0f 100%)',
  'linear-gradient(135deg, #001a0a 0%, #0a2e1a 50%, #0f0f0f 100%)',
  'linear-gradient(135deg, #1a0022 0%, #0f0f0f 50%, #001a2e 100%)',
  'linear-gradient(135deg, #0f0f0f 0%, #1a1a2e 50%, #0f0f0f 100%)',
];

const ACCENT_COLORS = ['#f59e0b', '#8b5cf6', '#ef4444', '#10b981', '#3b82f6', '#f59e0b'];

function parseAISummary(text) {
  if (!text) return { paragraphs: [], highlight: '', tomorrow: '' };

  const lines = text.split('\n').filter(Boolean);
  const paragraphs = [];
  let highlight = '';
  let tomorrow = '';

  for (const line of lines) {
    const hlMatch = line.match(/\*\*Highlight:\*\*\s*(.*)/);
    const tmMatch = line.match(/\*\*Tomorrow:\*\*\s*(.*)/);
    if (hlMatch) {
      highlight = hlMatch[1].trim();
    } else if (tmMatch) {
      tomorrow = tmMatch[1].trim();
    } else {
      paragraphs.push(line);
    }
  }

  return { paragraphs, highlight, tomorrow };
}

/**
 * Split paragraphs into chunks that fit on screen.
 * Each chunk becomes its own reflection slide.
 */
function chunkParagraphs(paragraphs, maxChars = 600) {
  const chunks = [];
  let current = [];
  let currentLen = 0;

  for (const p of paragraphs) {
    if (currentLen + p.length > maxChars && current.length > 0) {
      chunks.push(current.join('\n\n'));
      current = [p];
      currentLen = p.length;
    } else {
      current.push(p);
      currentLen += p.length;
    }
  }
  if (current.length > 0) {
    chunks.push(current.join('\n\n'));
  }
  return chunks;
}

function buildSlides(summary, sessions, categories) {
  const slides = [];
  const parsed = parseAISummary(summary.ai_summary);

  // Slide 1: Intro
  slides.push({ type: 'intro' });

  // Slide 2: Big number — total screen time
  const totalH = Math.floor((summary.total_tracked_minutes || 0) / 60);
  const totalM = (summary.total_tracked_minutes || 0) % 60;
  slides.push({
    type: 'big-number',
    icon: Clock,
    label: 'Total Screen Time',
    value: totalH > 0 ? `${totalH}h ${totalM}m` : `${totalM}m`,
    subtitle: `across ${sessions.length} app sessions`,
  });

  // Slide 3: Focus score
  slides.push({
    type: 'score-ring',
    icon: Target,
    label: 'Focus Score',
    score: summary.focus_score || 0,
    subtitle: scoreVerdict(summary.focus_score),
  });

  // Slide 4: Top categories breakdown
  if (categories.length > 0) {
    slides.push({
      type: 'categories',
      icon: Monitor,
      label: 'Where Your Time Went',
      categories: categories.slice(0, 5),
      totalMinutes: summary.total_tracked_minutes || 1,
    });
  }

  // Slide 5: Top app
  if (sessions.length > 0) {
    const topApp = sessions[0];
    slides.push({
      type: 'top-app',
      icon: Trophy,
      label: 'Most Used App',
      appName: topApp.app_name,
      duration: topApp.duration_display,
      category: topApp.category,
    });
  }

  // Slide 6: Longest streak
  if (summary.longest_focus_streak_minutes > 0) {
    slides.push({
      type: 'big-number',
      icon: Flame,
      label: 'Longest Focus Streak',
      value: `${summary.longest_focus_streak_minutes}m`,
      subtitle: 'of uninterrupted deep work',
    });
  }

  // Reflection slides — split long text into multiple slides
  if (parsed.paragraphs.length > 0) {
    const chunks = chunkParagraphs(parsed.paragraphs);
    chunks.forEach((text, i) => {
      slides.push({
        type: 'reflection',
        icon: Sparkles,
        label: chunks.length > 1 ? `AI Reflection (${i + 1}/${chunks.length})` : 'AI Reflection',
        text,
        autoPause: true,
      });
    });
  }

  // Highlight & Tomorrow
  if (parsed.highlight || parsed.tomorrow) {
    slides.push({
      type: 'takeaway',
      icon: Zap,
      highlight: parsed.highlight,
      tomorrow: parsed.tomorrow,
    });
  }

  return slides;
}

function scoreVerdict(score) {
  if (score >= 80) return 'Exceptional focus. You were in the zone.';
  if (score >= 60) return 'Strong focus day. Nice work.';
  if (score >= 40) return 'Decent focus, room to improve.';
  return 'Scattered day. Tomorrow\'s a fresh start.';
}

// ── Progress bar ──
function ProgressBars({ total, current, progress, paused }) {
  return (
    <div className="flex gap-1.5 px-6 pt-5">
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} className="h-[3px] flex-1 rounded-full bg-white/15 overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: i <= current ? '#f59e0b' : 'transparent' }}
            initial={{ width: i < current ? '100%' : '0%' }}
            animate={{ width: i < current ? '100%' : i === current ? `${progress}%` : '0%' }}
            transition={{ duration: 0.1 }}
          />
        </div>
      ))}
    </div>
  );
}

// ── Individual slide renderers ──

function IntroSlide() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <motion.div
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ type: 'spring', delay: 0.2 }}
        className="w-20 h-20 rounded-full bg-gradient-to-br from-amber-400 to-orange-600 flex items-center justify-center mb-8"
      >
        <Sparkles className="w-10 h-10 text-black" />
      </motion.div>
      <motion.h1
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="text-5xl font-bold whitespace-pre-line leading-tight bg-gradient-to-r from-amber-300 via-orange-400 to-amber-300 bg-clip-text text-transparent"
      >
        Your Day{'\n'}In Focus
      </motion.h1>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
        className="text-white/50 mt-4 text-lg"
      >
        Here's how your day went
      </motion.p>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.2 }}
        className="text-white/30 mt-12 text-sm flex items-center gap-2"
      >
        Tap to continue <ChevronRight className="w-4 h-4" />
      </motion.div>
    </div>
  );
}

function BigNumberSlide({ slide, accentColor }) {
  const Icon = slide.icon;
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <motion.div
        initial={{ scale: 0, rotate: -180 }}
        animate={{ scale: 1, rotate: 0 }}
        transition={{ type: 'spring', delay: 0.2, stiffness: 120 }}
      >
        <Icon className="w-12 h-12 mb-6" style={{ color: accentColor }} />
      </motion.div>
      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="text-white/50 text-sm uppercase tracking-widest mb-4"
      >
        {slide.label}
      </motion.p>
      <motion.div
        initial={{ opacity: 0, scale: 0.5 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: 'spring', delay: 0.5, stiffness: 80 }}
        className="text-7xl font-bold"
        style={{ color: accentColor }}
      >
        {slide.value}
      </motion.div>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
        className="text-white/40 mt-4 text-base"
      >
        {slide.subtitle}
      </motion.p>
    </div>
  );
}

function ScoreRingSlide({ slide, accentColor }) {
  const circumference = 2 * Math.PI * 60;
  const dashOffset = circumference - (slide.score / 100) * circumference;

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="text-white/50 text-sm uppercase tracking-widest mb-8"
      >
        {slide.label}
      </motion.p>
      <div className="relative w-44 h-44">
        <svg viewBox="0 0 140 140" className="w-full h-full -rotate-90">
          <circle
            cx="70" cy="70" r="60"
            fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="8"
          />
          <motion.circle
            cx="70" cy="70" r="60"
            fill="none"
            stroke={accentColor}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: dashOffset }}
            transition={{ duration: 1.5, delay: 0.4, ease: 'easeOut' }}
          />
        </svg>
        <motion.div
          className="absolute inset-0 flex items-center justify-center"
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.8, type: 'spring' }}
        >
          <span className="text-5xl font-bold" style={{ color: accentColor }}>
            {slide.score}
          </span>
        </motion.div>
      </div>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.2 }}
        className="text-white/50 mt-6 text-base max-w-xs"
      >
        {slide.subtitle}
      </motion.p>
    </div>
  );
}

const CAT_COLORS = {
  'Deep Work': '#f59e0b',
  'Communication': '#3b82f6',
  'Browsing': '#8b5cf6',
  'Entertainment': '#ef4444',
  'Writing': '#10b981',
  'Learning': '#06b6d4',
  'Other': '#6b7280',
};

function CategoriesSlide({ slide }) {
  const maxMins = Math.max(...slide.categories.map(c => c.minutes));
  return (
    <div className="flex flex-col items-center justify-center h-full px-10">
      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-white/50 text-sm uppercase tracking-widest mb-8"
      >
        {slide.label}
      </motion.p>
      <div className="w-full max-w-sm space-y-4">
        {slide.categories.map((c, i) => {
          const pct = (c.minutes / maxMins) * 100;
          const color = CAT_COLORS[c.name] || '#6b7280';
          const h = Math.floor(c.minutes / 60);
          const m = c.minutes % 60;
          return (
            <motion.div
              key={c.name}
              initial={{ opacity: 0, x: -40 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.2 + i * 0.12 }}
            >
              <div className="flex justify-between items-center mb-1.5">
                <span className="text-sm font-medium text-white/80">{c.name}</span>
                <span className="text-sm font-semibold" style={{ color }}>
                  {h > 0 ? `${h}h ${m}m` : `${m}m`}
                </span>
              </div>
              <div className="h-3 rounded-full bg-white/8 overflow-hidden">
                <motion.div
                  className="h-full rounded-full"
                  style={{ backgroundColor: color }}
                  initial={{ width: 0 }}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.8, delay: 0.4 + i * 0.12, ease: 'easeOut' }}
                />
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function TopAppSlide({ slide, accentColor }) {
  const Icon = slide.icon;
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <motion.div
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ type: 'spring', delay: 0.2 }}
        className="w-24 h-24 rounded-2xl flex items-center justify-center mb-6"
        style={{ backgroundColor: `${accentColor}20`, border: `2px solid ${accentColor}40` }}
      >
        <Icon className="w-12 h-12" style={{ color: accentColor }} />
      </motion.div>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="text-white/50 text-sm uppercase tracking-widest mb-2"
      >
        {slide.label}
      </motion.p>
      <motion.h2
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="text-4xl font-bold text-white mb-2"
      >
        {slide.appName}
      </motion.h2>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.7 }}
        className="text-2xl font-semibold"
        style={{ color: accentColor }}
      >
        {slide.duration}
      </motion.p>
    </div>
  );
}

function ReflectionSlide({ slide, accentColor }) {
  const Icon = slide.icon;
  const scrollRef = useRef(null);

  return (
    <div className="flex flex-col h-full px-8 pt-6 pb-2">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="flex items-center gap-3 mb-4 shrink-0"
      >
        <div className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{ backgroundColor: `${accentColor}20` }}>
          <Icon className="w-5 h-5" style={{ color: accentColor }} />
        </div>
        <span className="text-white/50 text-sm uppercase tracking-widest">{slide.label}</span>
      </motion.div>
      <motion.div
        ref={scrollRef}
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
        className="flex-1 min-h-0 overflow-y-auto overscroll-contain pr-2 max-w-2xl mx-auto w-full"
        style={{
          maskImage: 'linear-gradient(to bottom, black 85%, transparent 100%)',
          WebkitMaskImage: 'linear-gradient(to bottom, black 85%, transparent 100%)',
        }}
      >
        <div className="text-[15px] leading-[1.75] text-white/80 [&_strong]:text-amber-400 [&_strong]:font-semibold [&_p]:mb-4 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mb-4 [&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:mb-4 [&_li]:mb-1.5 pb-8">
          <Markdown remarkPlugins={[remarkGfm]}>{slide.text}</Markdown>
        </div>
      </motion.div>
    </div>
  );
}

function TakeawaySlide({ slide, accentColor }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      {slide.highlight && (
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="mb-10 max-w-md"
        >
          <div className="text-xs uppercase tracking-widest text-white/40 mb-3 flex items-center justify-center gap-2">
            <Zap className="w-3.5 h-3.5" style={{ color: accentColor }} />
            Today's Highlight
          </div>
          <p className="text-2xl font-semibold leading-snug" style={{ color: accentColor }}>
            {slide.highlight}
          </p>
        </motion.div>
      )}
      {slide.tomorrow && (
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.7 }}
          className="max-w-md"
        >
          <div className="text-xs uppercase tracking-widest text-white/40 mb-3">
            Tomorrow's Focus
          </div>
          <p className="text-lg text-white/70 leading-relaxed">
            {slide.tomorrow}
          </p>
        </motion.div>
      )}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.2 }}
        className="mt-12 text-white/30 text-sm"
      >
        That's a wrap for today
      </motion.div>
    </div>
  );
}

// ── Floating particles background ──
function Particles({ color }) {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {Array.from({ length: 20 }).map((_, i) => (
        <motion.div
          key={i}
          className="absolute rounded-full"
          style={{
            width: Math.random() * 4 + 2,
            height: Math.random() * 4 + 2,
            backgroundColor: color,
            opacity: 0.15,
            left: `${Math.random() * 100}%`,
            top: `${Math.random() * 100}%`,
          }}
          animate={{
            y: [0, -30 - Math.random() * 40, 0],
            x: [0, (Math.random() - 0.5) * 30, 0],
            opacity: [0.05, 0.2, 0.05],
          }}
          transition={{
            duration: 4 + Math.random() * 4,
            repeat: Infinity,
            delay: Math.random() * 3,
            ease: 'easeInOut',
          }}
        />
      ))}
    </div>
  );
}

// ── Pause indicator ──
function PauseIndicator({ paused }) {
  return (
    <AnimatePresence>
      {paused && (
        <motion.div
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.5 }}
          transition={{ duration: 0.2 }}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-20 pointer-events-none"
        >
          <div className="bg-black/60 backdrop-blur-sm rounded-full p-5 border border-white/10">
            <Pause className="w-8 h-8 text-white/80" />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ── Main component ──

export default function AISummaryWrapped({ summary, sessions, categories, onClose }) {
  const slides = buildSlides(summary, sessions, categories);
  const [current, setCurrent] = useState(0);
  const [progress, setProgress] = useState(0);
  const [paused, setPaused] = useState(false);
  const [showPauseFlash, setShowPauseFlash] = useState(false);

  const SLIDE_DURATION = 6000;

  const slide = slides[current];

  // Auto-pause on reflection slides
  useEffect(() => {
    if (slide?.autoPause) {
      setPaused(true);
    }
  }, [current]);

  const goNext = useCallback(() => {
    if (current < slides.length - 1) {
      setCurrent(c => c + 1);
      setProgress(0);
    } else {
      onClose();
    }
  }, [current, slides.length, onClose]);

  const goPrev = useCallback(() => {
    if (current > 0) {
      setCurrent(c => c - 1);
      setProgress(0);
    }
  }, [current]);

  const togglePause = useCallback(() => {
    setPaused(p => !p);
    setShowPauseFlash(true);
    setTimeout(() => setShowPauseFlash(false), 800);
  }, []);

  // Auto-advance timer — stops when paused
  useEffect(() => {
    if (paused) return;
    const interval = setInterval(() => {
      setProgress(p => {
        const next = p + (100 / (SLIDE_DURATION / 50));
        if (next >= 100) {
          goNext();
          return 0;
        }
        return next;
      });
    }, 50);
    return () => clearInterval(interval);
  }, [current, goNext, paused]);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'ArrowRight') { setPaused(false); goNext(); }
      else if (e.key === 'ArrowLeft') { setPaused(false); goPrev(); }
      else if (e.key === ' ') { e.preventDefault(); togglePause(); }
      else if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [goNext, goPrev, togglePause, onClose]);

  const gradient = GRADIENTS[current % GRADIENTS.length];
  const accent = ACCENT_COLORS[current % ACCENT_COLORS.length];

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex flex-col"
      style={{ background: gradient }}
    >
      <Particles color={accent} />

      {/* Progress bars */}
      <ProgressBars total={slides.length} current={current} progress={progress} paused={paused} />

      {/* Close button */}
      <button
        onClick={onClose}
        className="absolute top-5 right-6 z-10 p-2 rounded-full hover:bg-white/10 transition-colors"
      >
        <X className="w-5 h-5 text-white/60" />
      </button>

      {/* Pause flash indicator */}
      <PauseIndicator paused={showPauseFlash} />

      {/* Click areas for navigation + pause */}
      <div className="absolute inset-0 flex z-[5]" style={{ top: 48, bottom: 40 }}>
        <div className="w-1/3 h-full cursor-pointer" onClick={() => { setPaused(false); goPrev(); }} />
        <div className="w-1/3 h-full cursor-pointer" onClick={togglePause} />
        <div className="w-1/3 h-full cursor-pointer" onClick={() => { setPaused(false); goNext(); }} />
      </div>

      {/* Slide content */}
      <div className="flex-1 relative z-[1] min-h-0">
        <AnimatePresence mode="wait">
          <motion.div
            key={current}
            initial={{ opacity: 0, x: 60 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -60 }}
            transition={{ duration: 0.35, ease: 'easeOut' }}
            className="absolute inset-0"
          >
            {slide.type === 'intro' && <IntroSlide />}
            {slide.type === 'big-number' && <BigNumberSlide slide={slide} accentColor={accent} />}
            {slide.type === 'score-ring' && <ScoreRingSlide slide={slide} accentColor={accent} />}
            {slide.type === 'categories' && <CategoriesSlide slide={slide} />}
            {slide.type === 'top-app' && <TopAppSlide slide={slide} accentColor={accent} />}
            {slide.type === 'reflection' && <ReflectionSlide slide={slide} accentColor={accent} />}
            {slide.type === 'takeaway' && <TakeawaySlide slide={slide} accentColor={accent} />}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Nav hint */}
      <div className="pb-5 text-center relative z-[1] shrink-0">
        <div className="flex items-center justify-center gap-3 text-white/30 text-xs">
          <span>{current + 1} / {slides.length}</span>
          <span>·</span>
          {paused ? (
            <button onClick={togglePause} className="flex items-center gap-1 text-amber-400/70 hover:text-amber-400 transition-colors">
              <Play className="w-3 h-3" /> Paused — tap to resume
            </button>
          ) : (
            <span>tap center to pause</span>
          )}
        </div>
      </div>
    </motion.div>
  );
}
