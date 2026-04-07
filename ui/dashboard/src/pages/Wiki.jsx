import { useState, useEffect } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  BookOpen,
  Search,
  MessageSquare,
  RefreshCw,
  ChevronRight,
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Send,
  Loader2,
  FolderOpen,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../api';
import { useApi, todayStr } from '../hooks';

// Shared markdown styling for wiki content — handles tables, lists, headings, code, links
const PROSE_CLASSES = `
  prose prose-sm prose-invert max-w-none
  [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mb-3 [&_h1]:mt-4
  [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-[var(--color-text)]
  [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mb-1.5 [&_h3]:mt-3
  [&_p]:my-1.5 [&_p]:text-sm [&_p]:leading-relaxed
  [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-1.5 [&_ul]:text-sm
  [&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-1.5 [&_ol]:text-sm
  [&_li]:my-0.5
  [&_strong]:text-[var(--color-amber)] [&_strong]:font-semibold
  [&_a]:text-[var(--color-amber)] [&_a]:cursor-pointer [&_a:hover]:underline
  [&_code]:bg-[var(--color-surface-2)] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono
  [&_pre]:bg-[var(--color-surface-2)] [&_pre]:p-3 [&_pre]:rounded-lg [&_pre]:overflow-x-auto [&_pre]:text-xs
  [&_blockquote]:border-l-2 [&_blockquote]:border-[var(--color-amber)]/30 [&_blockquote]:pl-3 [&_blockquote]:text-[var(--color-text-muted)] [&_blockquote]:italic
  [&_hr]:border-[var(--color-border)] [&_hr]:my-4
  [&_table]:w-full [&_table]:text-xs [&_table]:border-collapse
  [&_thead]:border-b [&_thead]:border-[var(--color-border)]
  [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-medium [&_th]:text-[var(--color-text-muted)]
  [&_td]:px-3 [&_td]:py-1.5 [&_td]:border-b [&_td]:border-[var(--color-border)]/50
  [&_tr:hover]:bg-[var(--color-surface-2)]/50
`.trim();
import Loading, { ErrorMessage } from '../components/Loading';

const TYPE_COLORS = {
  project: '#f59e0b',
  pattern: '#8b5cf6',
  learning: '#06b6d4',
  person: '#3b82f6',
  rollup: '#10b981',
  insight: '#ec4899',
  meta: '#6b7280',
};

const TYPE_LABELS = {
  project: 'Projects',
  pattern: 'Patterns',
  learning: 'Learning',
  person: 'People',
  rollup: 'Rollups',
  insight: 'Insights',
};

export default function Wiki() {
  const [tab, setTab] = useState('browse'); // browse | search | ask
  const [currentPage, setCurrentPage] = useState(null);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {currentPage && (
            <button
              onClick={() => setCurrentPage(null)}
              className="p-1.5 rounded-lg hover:bg-[var(--color-surface-2)] transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
          )}
          <h1 className="text-xl font-semibold">Wiki</h1>
        </div>
        <div className="flex items-center gap-1 bg-[var(--color-surface)] rounded-lg p-1 border border-[var(--color-border)]">
          {[
            { id: 'browse', icon: BookOpen, label: 'Browse' },
            { id: 'search', icon: Search, label: 'Search' },
            { id: 'ask', icon: MessageSquare, label: 'Ask' },
          ].map(({ id, icon: Icon, label }) => (
            <button
              key={id}
              onClick={() => { setTab(id); setCurrentPage(null); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors ${
                tab === id
                  ? 'bg-[var(--color-amber)]/15 text-[var(--color-amber)]'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>
      </header>

      {/* Compile Status + Focus Chart (always visible at top) */}
      {!currentPage && <CompileStatus />}
      {!currentPage && tab === 'browse' && <FocusTrendChart />}

      {/* Page Viewer */}
      {currentPage ? (
        <PageViewer path={currentPage} onNavigate={setCurrentPage} />
      ) : tab === 'browse' ? (
        <WikiBrowser onOpen={setCurrentPage} />
      ) : tab === 'search' ? (
        <WikiSearch onOpen={setCurrentPage} />
      ) : (
        <AskPanel />
      )}
    </div>
  );
}


function CompileStatus() {
  const { data, loading, refetch } = useApi(() => api.getWikiCompileStatus(), []);
  const [compiling, setCompiling] = useState(false);

  if (loading || !data) return null;

  const handleCompile = async () => {
    setCompiling(true);
    try {
      await api.compileWiki(todayStr());
      refetch();
    } catch (e) {
      console.error(e);
    } finally {
      setCompiling(false);
    }
  };

  return (
    <div className="flex items-center gap-4 bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <div className="flex-1 flex items-center gap-3">
        {data.today_compiled ? (
          <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
        ) : (
          <XCircle className="w-5 h-5 text-[var(--color-text-muted)] shrink-0" />
        )}
        <div>
          <div className="text-sm font-medium">
            {data.today_compiled ? "Today's note compiled" : "Today not compiled yet"}
          </div>
          <div className="text-xs text-[var(--color-text-muted)]">
            {data.compiled_dates?.length || 0} days in wiki
            {data.last_compiled && ` \u00b7 Last: ${data.last_compiled}`}
          </div>
        </div>
      </div>
      <button
        onClick={handleCompile}
        disabled={compiling}
        className="flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-lg bg-[var(--color-amber)]/15 text-[var(--color-amber)] hover:bg-[var(--color-amber)]/25 transition-colors disabled:opacity-50"
      >
        {compiling ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <RefreshCw className="w-3.5 h-3.5" />
        )}
        {compiling ? 'Compiling...' : 'Compile Now'}
      </button>
    </div>
  );
}


function FocusTrendChart() {
  const { data, loading } = useApi(() => api.getWikiFocusTrends(), []);

  if (loading || !data?.trends?.length) return null;

  return (
    <section className="bg-[var(--color-surface)] rounded-xl p-4 border border-[var(--color-border)]">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] mb-3">
        Focus Score Trend (Wiki)
      </h2>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data.trends}>
          <XAxis
            dataKey="date"
            tick={{ fill: '#888', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(d) =>
              new Date(d + 'T12:00:00').toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
              })
            }
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: '#888', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={30}
          />
          <Tooltip
            content={({ payload }) => {
              if (!payload?.[0]) return null;
              const d = payload[0].payload;
              return (
                <div className="bg-[var(--color-surface-3)] px-3 py-2 rounded-lg text-xs shadow-lg border border-[var(--color-border)]">
                  <div className="font-medium">{d.date}</div>
                  <div>Score: {d.score}/100</div>
                  <div>Deep Work: {d.deep_work}</div>
                  <div>Streak: {d.longest_streak}</div>
                </div>
              );
            }}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#f59e0b"
            strokeWidth={2}
            dot={{ r: 3, fill: '#f59e0b' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
}


function WikiBrowser({ onOpen }) {
  const { data, loading, error, refetch } = useApi(() => api.getWikiIndex(), []);

  if (loading) return <Loading message="Loading wiki..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;
  if (!data?.initialized) {
    return (
      <div className="text-center py-12 text-[var(--color-text-muted)]">
        <BookOpen className="w-10 h-10 mx-auto mb-3 opacity-40" />
        <p className="text-sm">Wiki not initialized. Run <code className="bg-[var(--color-surface-2)] px-1.5 py-0.5 rounded text-xs">python main.py compile</code> first.</p>
      </div>
    );
  }

  // Group by type
  const grouped = {};
  for (const page of data.pages) {
    if (!grouped[page.type]) grouped[page.type] = [];
    grouped[page.type].push(page);
  }

  const typeOrder = ['project', 'pattern', 'learning', 'person', 'rollup', 'insight'];

  return (
    <div className="space-y-4">
      {typeOrder.map((type) => {
        const pages = grouped[type];
        if (!pages?.length) return null;
        return (
          <section key={type} className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)]">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)]">
              <div
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: TYPE_COLORS[type] }}
              />
              <h3 className="text-sm font-medium">{TYPE_LABELS[type] || type}</h3>
              <span className="text-xs text-[var(--color-text-muted)]">{pages.length}</span>
            </div>
            <div className="divide-y divide-[var(--color-border)]">
              {pages.map((page) => (
                <button
                  key={page.path}
                  onClick={() => onOpen(page.path)}
                  className="w-full flex items-center justify-between px-4 py-2.5 text-sm text-left hover:bg-[var(--color-surface-2)] transition-colors"
                >
                  <span>{page.title}</span>
                  <ChevronRight className="w-3.5 h-3.5 text-[var(--color-text-muted)]" />
                </button>
              ))}
            </div>
          </section>
        );
      })}

      {data.pages.length === 0 && (
        <div className="text-center py-12 text-[var(--color-text-muted)]">
          <FolderOpen className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No wiki pages yet. Compile a daily note to get started.</p>
        </div>
      )}
    </div>
  );
}


function WikiSearch({ onOpen }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const data = await api.searchWiki(query);
      setResults(data.results);
    } catch (e) {
      console.error(e);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-text-muted)]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            placeholder="Search wiki pages..."
            className="w-full pl-10 pr-4 py-2.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-amber)]"
          />
        </div>
        <button
          onClick={doSearch}
          disabled={searching || !query.trim()}
          className="px-4 py-2.5 bg-[var(--color-amber)]/15 text-[var(--color-amber)] rounded-lg text-sm font-medium hover:bg-[var(--color-amber)]/25 transition-colors disabled:opacity-50"
        >
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
        </button>
      </div>

      {results !== null && (
        <div className="space-y-2">
          {results.length === 0 ? (
            <div className="text-center py-8 text-[var(--color-text-muted)] text-sm">
              No results found for "{query}"
            </div>
          ) : (
            results.map((r) => (
              <button
                key={r.path}
                onClick={() => onOpen(r.path)}
                className="w-full text-left bg-[var(--color-surface)] rounded-lg p-3 border border-[var(--color-border)] hover:bg-[var(--color-surface-2)] transition-colors"
              >
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ backgroundColor: TYPE_COLORS[r.type] || '#6b7280' }}
                  />
                  <span className="text-sm font-medium">{r.title}</span>
                  <span className="text-xs text-[var(--color-text-muted)]">{r.type}</span>
                </div>
                {r.matches?.length > 0 && (
                  <div className="text-xs text-[var(--color-text-muted)] mt-1 space-y-0.5">
                    {r.matches.map((m, i) => (
                      <div key={i} className="truncate">{m}</div>
                    ))}
                  </div>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}


function AskPanel() {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]);
  const [asking, setAsking] = useState(false);

  const handleAsk = async () => {
    if (!question.trim() || asking) return;
    const q = question.trim();
    setQuestion('');
    setMessages((prev) => [...prev, { role: 'user', text: q }]);
    setAsking(true);

    try {
      const data = await api.askWiki(q);
      setMessages((prev) => [...prev, { role: 'assistant', text: data.answer || 'No answer returned.' }]);
    } catch (e) {
      setMessages((prev) => [...prev, { role: 'assistant', text: `Error: ${e.message}` }]);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Messages */}
      <div className="space-y-3 min-h-[200px]">
        {messages.length === 0 && (
          <div className="text-center py-12 text-[var(--color-text-muted)]">
            <MessageSquare className="w-10 h-10 mx-auto mb-3 opacity-40" />
            <p className="text-sm">Ask anything about your activity data.</p>
            <p className="text-xs mt-1 opacity-70">
              e.g. "How much time did I spend on Pearson?" or "What are my distraction patterns?"
            </p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`rounded-lg p-3 text-sm ${
              msg.role === 'user'
                ? 'bg-[var(--color-amber)]/10 border border-[var(--color-amber)]/20 ml-12'
                : 'bg-[var(--color-surface)] border border-[var(--color-border)] mr-12'
            }`}
          >
            {msg.role === 'assistant' ? (
              <div className={PROSE_CLASSES}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
              </div>
            ) : (
              msg.text
            )}
          </div>
        ))}
        {asking && (
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3 mr-12 flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <Loader2 className="w-4 h-4 animate-spin" />
            Thinking...
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
          placeholder="Ask about your activity..."
          disabled={asking}
          className="flex-1 px-4 py-2.5 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg text-sm focus:outline-none focus:border-[var(--color-amber)] disabled:opacity-50"
        />
        <button
          onClick={handleAsk}
          disabled={asking || !question.trim()}
          className="px-4 py-2.5 bg-[var(--color-amber)]/15 text-[var(--color-amber)] rounded-lg hover:bg-[var(--color-amber)]/25 transition-colors disabled:opacity-50"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}


function PageViewer({ path, onNavigate }) {
  const { data, loading, error, refetch } = useApi(() => api.getWikiPage(path), [path]);

  if (loading) return <Loading message="Loading page..." />;
  if (error) return <ErrorMessage message={error} onRetry={refetch} />;

  // Transform [[wikilinks]] into clickable links and strip frontmatter
  const processContent = (content) => {
    // Remove YAML frontmatter
    const stripped = content.replace(/^---[\s\S]*?---\s*/, '');
    // Convert [[path|label]] and [[path]] to markdown links
    return stripped
      .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '[$2](#$1)')
      .replace(/\[\[([^\]]+)\]\]/g, '[$1](#$1)');
  };

  const handleClick = (e) => {
    const link = e.target.closest('a');
    if (!link) return;
    const href = link.getAttribute('href');
    if (href?.startsWith('#')) {
      e.preventDefault();
      const target = href.slice(1);
      const resolved = resolveWikiLink(target);
      if (resolved) onNavigate(resolved);
    }
  };

  // Extract frontmatter tags for display
  const frontmatter = parseFrontmatter(data.content);

  return (
    <div
      className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)]"
      onClick={handleClick}
    >
      {/* Page header with metadata */}
      <div className="px-6 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-muted)] font-mono">{path}</span>
        <div className="flex items-center gap-2">
          {frontmatter.type && (
            <span
              className="text-[10px] font-medium px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: (TYPE_COLORS[frontmatter.type] || '#6b7280') + '22',
                color: TYPE_COLORS[frontmatter.type] || '#6b7280',
              }}
            >
              {frontmatter.type}
            </span>
          )}
          {frontmatter.updated && (
            <span className="text-[10px] text-[var(--color-text-muted)]">
              Updated {frontmatter.updated}
            </span>
          )}
        </div>
      </div>

      {/* Tags */}
      {frontmatter.tags?.length > 0 && (
        <div className="px-6 pt-3 flex flex-wrap gap-1.5">
          {frontmatter.tags.map((tag) => (
            <span
              key={tag}
              className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-surface-2)] text-[var(--color-text-muted)]"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Markdown content */}
      <div className={`px-6 py-4 ${PROSE_CLASSES}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {processContent(data.content)}
        </ReactMarkdown>
      </div>
    </div>
  );
}


/** Parse YAML frontmatter from markdown content */
function parseFrontmatter(content) {
  const match = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!match) return {};
  const fm = {};
  for (const line of match[1].split('\n')) {
    const m = line.match(/^(\w+):\s*(.+)/);
    if (!m) continue;
    let [, key, val] = m;
    val = val.trim();
    // Parse arrays like [tag1, tag2]
    if (val.startsWith('[') && val.endsWith(']')) {
      fm[key] = val.slice(1, -1).split(',').map((s) => s.trim());
    } else {
      fm[key] = val;
    }
  }
  return fm;
}


function resolveWikiLink(link) {
  // Handle links like "projects/pearson", "patterns/focus-trends", etc.
  const clean = link.replace(/^(\.\.\/|\.\/|\/)/, '');

  // If it already looks like a path with a subdirectory
  if (clean.includes('/')) {
    return clean.endsWith('.md') ? clean : clean + '.md';
  }

  // Try common directories
  return null;
}
