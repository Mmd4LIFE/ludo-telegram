"use client";

// Admin Insights (AI analyst over a read-only DB view) and the native read-only SQL tab.

import { Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Brain,
  ChartLine,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  Database,
  ExternalLink,
  History,
  KeyRound,
  Link2,
  Loader2,
  MessageSquarePlus,
  Play,
  Plus,
  RotateCw,
  Search,
  Send,
  Sparkles,
  Table2,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";
import {
  api,
  InsightAnswer,
  InsightChart,
  InsightLogRow,
  InsightStatus,
  InsightStep,
  KnowledgeEntry,
  SchemaTable,
  SqlHistoryRow,
  SqlRunResult,
  SqlTable,
} from "@/lib/api";
import { haptic } from "@/lib/telegram";
import { Button } from "@/components/ui/button";
import { Card, SectionLabel } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ shared */

const ISO_TS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function errText(e: unknown): string {
  const s = String(e instanceof Error ? e.message : e);
  const m = s.match(/^\d{3}: (.*)$/s);
  if (!m) return s;
  try {
    const j = JSON.parse(m[1]);
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    /* plain text */
  }
  return m[1];
}

function fmtCell(v: unknown, col: string): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") {
    if (col === "id" || col.endsWith("_id")) return String(v);
    return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof v === "string" && ISO_TS.test(v)) return v.slice(0, 16).replace("T", " ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function toCsv(t: SqlTable): string {
  const esc = (v: unknown) => {
    if (v === null || v === undefined) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [t.columns.map((c) => esc(c.name)).join(","), ...t.rows.map((r) => r.map(esc).join(","))].join("\n");
}

function useCopy(): [string | null, (key: string, text: string) => void] {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(key);
        haptic("light");
        setTimeout(() => setCopied((k) => (k === key ? null : k)), 1400);
      },
      () => {},
    );
  };
  return [copied, copy];
}

function Pill({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ring-1",
        ok === null
          ? "bg-secondary text-muted-foreground ring-white/10"
          : ok
            ? "bg-[#3fa66a]/15 text-[#5fc58a] ring-[#3fa66a]/30"
            : "bg-red/15 text-red ring-red/30",
      )}
    >
      {children}
    </span>
  );
}

export function ResultTable({ table, maxHeight = "55vh" }: { table: SqlTable; maxHeight?: string }) {
  const [copied, copy] = useCopy();
  if (!table.columns.length) {
    return <div className="text-xs text-muted-foreground">Statement returned no columns.</div>;
  }
  const numeric = table.columns.map((c, i) => table.rows.some((r) => typeof r[i] === "number"));
  return (
    <div className="rounded-xl bg-black/20 ring-1 ring-white/5">
      <div className="no-scrollbar overflow-auto" style={{ maxHeight }}>
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 z-10 bg-[#1b2130]">
            <tr>
              {table.columns.map((c, i) => (
                <th
                  key={i}
                  title={c.type}
                  className={cn(
                    "whitespace-nowrap border-b border-white/10 px-2 py-1.5 font-bold text-muted-foreground",
                    numeric[i] ? "text-right" : "text-left",
                  )}
                >
                  {c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, ri) => (
              <tr key={ri} className="border-b border-white/5 odd:bg-white/[0.015]">
                {row.map((v, ci) => {
                  const s = fmtCell(v, table.columns[ci]?.name ?? "");
                  return (
                    <td
                      key={ci}
                      title={s.length > 28 ? s : undefined}
                      className={cn(
                        "max-w-[220px] truncate whitespace-nowrap px-2 py-1.5",
                        numeric[ci] && "text-right tabular-nums",
                        v === null && "text-muted-foreground/60",
                      )}
                    >
                      {s}
                    </td>
                  );
                })}
              </tr>
            ))}
            {table.rows.length === 0 && (
              <tr>
                <td colSpan={table.columns.length} className="px-2 py-3 text-center text-muted-foreground">
                  No rows.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 border-t border-white/5 px-2 py-1.5 text-[10px] text-muted-foreground">
        <span className="tabular-nums">
          {table.row_count.toLocaleString()} row{table.row_count === 1 ? "" : "s"}
          {table.truncated ? " (truncated)" : ""}
          {table.duration_ms ? ` · ${table.duration_ms}ms` : ""}
        </span>
        <button onClick={() => copy("csv", toCsv(table))} className="ml-auto inline-flex items-center gap-1 font-semibold">
          {copied === "csv" ? <Check className="size-3" /> : <Copy className="size-3" />} CSV
        </button>
      </div>
    </div>
  );
}

export function MiniChart({ table, chart }: { table: SqlTable; chart: InsightChart }) {
  const xi = table.columns.findIndex((c) => c.name === chart.x);
  const yi = table.columns.findIndex((c) => c.name === chart.y);
  if (xi < 0 || yi < 0 || !table.rows.length) return null;
  const pts = table.rows.map((r) => ({ label: fmtCell(r[xi], chart.x), v: Number(r[yi] ?? 0) }));
  const max = Math.max(...pts.map((p) => p.v), 0);
  const min = Math.min(...pts.map((p) => p.v), 0);

  if (chart.type === "bar") {
    return (
      <div className="flex flex-col gap-1">
        {pts.map((p, i) => (
          <div key={i} className="flex items-center gap-2 text-[11px]">
            <span className="w-24 shrink-0 truncate text-muted-foreground" title={p.label}>
              {p.label}
            </span>
            <div className="h-4 flex-1 overflow-hidden rounded bg-white/5">
              <div
                className="h-full rounded bg-primary/80"
                style={{ width: `${max > 0 ? Math.max(1.5, (Math.max(p.v, 0) / max) * 100) : 0}%` }}
              />
            </div>
            <span className="w-14 shrink-0 text-right font-semibold tabular-nums">{p.v.toLocaleString()}</span>
          </div>
        ))}
      </div>
    );
  }

  const W = 320;
  const H = 120;
  const span = max - min || 1;
  const xy = pts.map((p, i) => [
    pts.length === 1 ? W / 2 : (i / (pts.length - 1)) * W,
    H - 8 - ((p.v - min) / span) * (H - 20),
  ]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <div>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>max {max.toLocaleString()}</span>
        <span>{chart.y}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 h-32 w-full text-primary" preserveAspectRatio="none">
        <polygon points={`0,${H} ${line} ${W},${H}`} fill="currentColor" opacity={0.12} />
        <polyline points={line} fill="none" stroke="currentColor" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {xy.length <= 40 &&
          xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={2.2} fill="currentColor" />)}
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>{pts[0].label}</span>
        <span>{pts[pts.length - 1].label}</span>
      </div>
    </div>
  );
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded bg-black/30 px-1 font-mono text-[0.9em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*```/.test(ln)) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(
        <pre key={out.length} className="overflow-x-auto rounded-lg bg-black/30 p-2 font-mono text-[11px]">
          {buf.join("\n")}
        </pre>,
      );
      continue;
    }
    if (/^\s*\|/.test(ln)) {
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(
        <div key={out.length} className="no-scrollbar overflow-x-auto">
          <table className="text-[12px]">
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className={ri === 0 ? "font-bold" : "border-t border-white/5"}>
                  {r.map((c, ci) => (
                    <td key={ci} className="whitespace-nowrap px-2 py-1">
                      {inline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*([-*•]|\d+\.)\s+/.test(ln)) {
      const items: string[] = [];
      const ordered = /^\s*\d+\./.test(ln);
      while (i < lines.length && /^\s*([-*•]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*•]|\d+\.)\s+/, ""));
        i++;
      }
      const L = ordered ? "ol" : "ul";
      out.push(
        <L key={out.length} className={cn("ms-4 flex flex-col gap-0.5", ordered ? "list-decimal" : "list-disc")}>
          {items.map((it, k) => (
            <li key={k}>{inline(it)}</li>
          ))}
        </L>,
      );
      continue;
    }
    if (/^#{1,6}\s/.test(ln)) {
      out.push(
        <div key={out.length} className="font-bold">
          {inline(ln.replace(/^#{1,6}\s/, ""))}
        </div>,
      );
    } else if (ln.trim()) {
      out.push(<p key={out.length}>{inline(ln)}</p>);
    }
    i++;
  }
  return (
    <div dir="auto" className="flex flex-col gap-1.5 text-sm leading-relaxed">
      {out}
    </div>
  );
}

function SqlBlock({ sql, onOpen }: { sql: string; onOpen?: (sql: string) => void }) {
  const [open, setOpen] = useState(false);
  const [copied, copy] = useCopy();
  return (
    <div className="rounded-xl bg-black/25 ring-1 ring-white/5">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1.5 text-[11px] font-bold text-muted-foreground">
          <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
          SQL
        </button>
        <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
          <button onClick={() => copy("sql", sql)} className="inline-flex items-center gap-1">
            {copied === "sql" ? <Check className="size-3" /> : <Copy className="size-3" />} Copy
          </button>
          {onOpen && (
            <button onClick={() => onOpen(sql)} className="inline-flex items-center gap-1 text-primary">
              <ExternalLink className="size-3" /> Open in SQL
            </button>
          )}
        </div>
      </div>
      {open && (
        <pre className="max-h-72 overflow-auto border-t border-white/5 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-[#c9d4e8]">
          {sql}
        </pre>
      )}
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = {
  retrieve: "Retrieve knowledge",
  llm: "Model",
  search_knowledge: "Search knowledge",
  describe_table: "Describe table",
  run_sql: "Run SQL",
  answer: "Compose answer",
};

function stepSummary(s: InsightStep): string {
  const d = s.detail as Record<string, unknown>;
  switch (s.stage) {
    case "retrieve":
      return `${(d.hits as unknown[] | undefined)?.length ?? 0} chunks${d.notes ? ` · ${d.notes} notes` : ""}`;
    case "llm":
      return d.error ? String(d.error) : ((d.tools as string[] | undefined)?.join(", ") || "reply");
    case "search_knowledge":
      return String(d.query ?? "");
    case "describe_table":
      return String(d.table ?? "");
    case "run_sql":
      return d.error ? String(d.error) : `${String(d.purpose ?? "")} · ${d.row_count ?? 0} rows`;
    default:
      return "";
  }
}

function StepTrace({ steps }: { steps: InsightStep[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="flex flex-col gap-1">
      {steps.map((s) => (
        <div key={s.seq} className="rounded-lg bg-black/20 ring-1 ring-white/5">
          <button
            onClick={() => setOpen(open === s.seq ? null : s.seq)}
            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
          >
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: s.status === "ok" ? "#3fa66a" : "#d76a6a" }}
            />
            <span className="shrink-0 text-[11px] font-bold">{STAGE_LABEL[s.stage] ?? s.stage}</span>
            <span className="min-w-0 flex-1 truncate text-[10px] text-muted-foreground">{stepSummary(s)}</span>
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{s.duration_ms}ms</span>
          </button>
          {open === s.seq && (
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words border-t border-white/5 px-2.5 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
              {typeof s.detail.sql === "string" ? `${s.detail.sql}\n\n` : ""}
              {JSON.stringify({ ...s.detail, sql: undefined }, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- insights */

type Turn = { question: string; pending?: boolean; resp?: InsightAnswer; error?: string };

const SUGGESTIONS = [
  "How many players signed up each day this week?",
  "Who are the top 10 players by wins and their win rate?",
  "Stars revenue this month vs last month",
  "Which coin sinks are players using most in the last 30 days?",
  "How many matches finished today and how long did they take on average?",
  "Which fantasy cards are picked most?",
];

function AnswerCard({
  turn,
  onAsk,
  onOpenSql,
  onFeedback,
}: {
  turn: Turn;
  onAsk: (q: string) => void;
  onOpenSql?: (sql: string) => void;
  onFeedback: (id: number, v: -1 | 0 | 1) => void;
}) {
  const [showSteps, setShowSteps] = useState(false);
  const [view, setView] = useState<"chart" | "table">("chart");
  const r = turn.resp;
  if (turn.pending) {
    return (
      <Card className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin text-primary" /> Analyzing — querying the database…
      </Card>
    );
  }
  if (!r) {
    return (
      <Card className="flex items-start gap-2 text-sm text-red">
        <CircleAlert className="mt-0.5 size-4 shrink-0" /> {turn.error || "Request failed"}
      </Card>
    );
  }
  const hasChart = !!(r.chart && r.table && r.table.rows.length);
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start gap-2">
        <Sparkles className={cn("mt-0.5 size-4 shrink-0", r.status === "answered" ? "text-primary" : "text-red")} />
        <div className="min-w-0 flex-1">
          <Markdown text={r.answer} />
        </div>
      </div>

      {r.table && r.table.columns.length > 0 && (
        <div className="flex flex-col gap-2">
          {hasChart && (
            <div className="flex gap-1">
              {(["chart", "table"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold uppercase",
                    view === v ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground",
                  )}
                >
                  {v === "chart" ? <ChartLine className="size-3" /> : <Table2 className="size-3" />} {v}
                </button>
              ))}
            </div>
          )}
          {hasChart && view === "chart" ? (
            <div className="rounded-xl bg-black/20 p-2.5 ring-1 ring-white/5">
              <MiniChart table={r.table} chart={r.chart!} />
            </div>
          ) : (
            <ResultTable table={r.table} maxHeight="40vh" />
          )}
        </div>
      )}

      {r.sql && <SqlBlock sql={r.sql} onOpen={onOpenSql} />}

      {r.follow_ups.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {r.follow_ups.map((f) => (
            <button
              key={f}
              dir="auto"
              onClick={() => onAsk(f)}
              className="rounded-full bg-secondary px-2.5 py-1 text-left text-[11px] text-foreground/80 ring-1 ring-white/10 active:scale-95"
            >
              {f}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-white/5 pt-2 text-[10px] text-muted-foreground">
        <button
          onClick={() => onFeedback(r.query_id, r.feedback === 1 ? 0 : 1)}
          disabled={!r.sql}
          title={r.sql ? "Good answer — remember this query as an example" : "No query to learn from"}
          className={cn("rounded-md p-1 disabled:opacity-30", r.feedback === 1 && "bg-[#3fa66a]/20 text-[#5fc58a]")}
        >
          <ThumbsUp className="size-3.5" />
        </button>
        <button
          onClick={() => onFeedback(r.query_id, r.feedback === -1 ? 0 : -1)}
          className={cn("rounded-md p-1", r.feedback === -1 && "bg-red/20 text-red")}
        >
          <ThumbsDown className="size-3.5" />
        </button>
        <button onClick={() => setShowSteps((v) => !v)} className="inline-flex items-center gap-1">
          <ChevronDown className={cn("size-3 transition-transform", !showSteps && "-rotate-90")} />
          {r.steps.length} steps
        </button>
        <span className="ml-auto truncate tabular-nums">
          {(r.latency_ms / 1000).toFixed(1)}s
          {r.usage?.total_tokens ? ` · ${r.usage.total_tokens.toLocaleString()} tok` : ""}
          {r.model ? ` · ${r.model}` : ""}
        </span>
      </div>

      {showSteps && (
        <div className="flex flex-col gap-2">
          {r.sources.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {r.sources.map((s) => (
                <span key={s.key} title={`score ${s.score}`} className="rounded-md bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {s.kind}: {s.title}
                </span>
              ))}
            </div>
          )}
          {r.steps.length > 0 ? <StepTrace steps={r.steps} /> : <div className="text-[11px] text-muted-foreground">Open from history to load steps.</div>}
        </div>
      )}
    </Card>
  );
}

function KnowledgeManager() {
  const [items, setItems] = useState<KnowledgeEntry[] | null>(null);
  const [kind, setKind] = useState<string>("note");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api.adminKnowledge().then(setItems).catch((e) => setMsg(errText(e)));
  useEffect(() => {
    load();
  }, []);

  const shown = (items ?? []).filter((k) => k.kind === kind);
  const counts = (items ?? []).reduce<Record<string, number>>((m, k) => ({ ...m, [k.kind]: (m[k.kind] ?? 0) + 1 }), {});

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <div className="flex items-center gap-2">
          <Brain className="size-4 text-primary" />
          <SectionLabel>Teach the analyst</SectionLabel>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Notes are sent with every question — use them for business definitions, e.g. &ldquo;An active player
          rolled dice in the last 7 days&rdquo; or &ldquo;Ignore user 1, it&rsquo;s a test account&rdquo;.
        </p>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title (optional)"
          className="mt-2.5 h-9 w-full rounded-xl bg-secondary px-3 text-sm outline-none ring-1 ring-white/10"
        />
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          dir="auto"
          rows={3}
          placeholder="Definition or rule…"
          className="mt-2 w-full resize-y rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10"
        />
        <div className="mt-2 flex items-center gap-2">
          <Button
            size="sm"
            disabled={busy || content.trim().length < 3}
            onClick={async () => {
              setBusy(true);
              setMsg(null);
              try {
                await api.adminAddKnowledge(title, content);
                setTitle("");
                setContent("");
                setKind("note");
                await load();
              } catch (e) {
                setMsg(errText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Plus className="size-3.5" /> Add note
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setMsg(null);
              try {
                const r = await api.adminReindexKnowledge();
                setMsg(`Reindexed ${r.chunks} chunks · ${r.changed} changed · ${r.embedded} embedded`);
                await load();
              } catch (e) {
                setMsg(errText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <RotateCw className={cn("size-3.5", busy && "animate-spin")} /> Reindex schema
          </Button>
        </div>
        {msg && <div className="mt-2 text-[11px] text-muted-foreground">{msg}</div>}
      </Card>

      <Card>
        <div className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto px-1">
          {[
            ["note", "Notes"],
            ["example", "Examples"],
            ["doc", "Docs"],
            ["table", "Tables"],
          ].map(([k, label]) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={cn(
                "shrink-0 rounded-lg px-2.5 py-1 text-[11px] font-bold",
                kind === k ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground",
              )}
            >
              {label} {counts[k] ? `· ${counts[k]}` : ""}
            </button>
          ))}
        </div>
        <div className="mt-2.5 flex flex-col gap-1.5">
          {items === null && <div className="text-xs text-muted-foreground">Loading…</div>}
          {items && shown.length === 0 && (
            <div className="text-xs text-muted-foreground">
              {kind === "example" ? "Mark good answers with 👍 to add learned examples." : "Nothing here yet."}
            </div>
          )}
          {shown.map((k) => (
            <div key={k.id} className={cn("rounded-xl bg-secondary/50 ring-1 ring-white/5", !k.enabled && "opacity-50")}>
              <div className="flex items-center gap-2 px-2.5 py-2">
                <button onClick={() => setOpen(open === k.id ? null : k.id)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
                  <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", open === k.id && "rotate-90")} />
                  <span dir="auto" className="truncate text-xs font-semibold">
                    {k.title || k.content.slice(0, 60)}
                  </span>
                </button>
                <span className="shrink-0 rounded bg-black/20 px-1 text-[9px] uppercase text-muted-foreground">{k.source}</span>
                {!k.embedded && k.enabled && (
                  <span title="Not embedded yet (keyword search only)" className="shrink-0 text-[9px] text-[#e0a44a]">
                    kw
                  </span>
                )}
                <button
                  onClick={() =>
                    api.adminPatchKnowledge(k.id, { enabled: !k.enabled }).then(load).catch((e) => setMsg(errText(e)))
                  }
                  className="shrink-0 text-[10px] font-bold text-muted-foreground"
                >
                  {k.enabled ? "ON" : "OFF"}
                </button>
                {k.editable && (
                  <button
                    onClick={() => api.adminDeleteKnowledge(k.id).then(load).catch((e) => setMsg(errText(e)))}
                    className="shrink-0 text-muted-foreground"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </div>
              {open === k.id && (
                <pre dir="auto" className="max-h-72 overflow-auto whitespace-pre-wrap border-t border-white/5 px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-muted-foreground">
                  {k.content}
                </pre>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function InsightsHistory({ onOpen }: { onOpen: (id: number) => void }) {
  const [log, setLog] = useState<InsightLogRow[] | null>(null);
  const load = () => api.adminInsightsLog(50).then(setLog).catch(() => setLog([]));
  useEffect(() => {
    load();
  }, []);
  return (
    <Card>
      <div className="flex items-center justify-between">
        <SectionLabel>Recent questions</SectionLabel>
        <button onClick={load} className="text-[11px] text-muted-foreground">
          Refresh
        </button>
      </div>
      <div className="mt-2 flex flex-col gap-1">
        {log === null && <div className="text-xs text-muted-foreground">Loading…</div>}
        {log?.map((r) => (
          <button
            key={r.id}
            onClick={() => onOpen(r.id)}
            className="flex items-center gap-2 rounded-lg bg-secondary/40 px-2.5 py-2 text-left"
          >
            <span className="size-2 shrink-0 rounded-full" style={{ background: r.status === "answered" ? "#3fa66a" : "#d76a6a" }} />
            <span dir="auto" className="min-w-0 flex-1 truncate text-xs">
              {r.question}
            </span>
            {r.feedback === 1 && <ThumbsUp className="size-3 shrink-0 text-[#5fc58a]" />}
            {r.feedback === -1 && <ThumbsDown className="size-3 shrink-0 text-red" />}
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{(r.latency_ms / 1000).toFixed(1)}s</span>
          </button>
        ))}
        {log && log.length === 0 && <div className="text-xs text-muted-foreground">No questions yet.</div>}
      </div>
    </Card>
  );
}

export function InsightsPanel({ onOpenSql }: { onOpenSql?: (sql: string) => void }) {
  const [view, setView] = useState<"ask" | "knowledge" | "history">("ask");
  const [status, setStatus] = useState<InsightStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const busy = turns.some((t) => t.pending);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.adminInsightsStatus().then(setStatus).catch(() => {});
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    setView("ask");
    setDraft("");
    haptic("light");
    const history = turns
      .filter((t) => t.resp && t.resp.status === "answered")
      .slice(-4)
      .map((t) => ({ question: t.question, answer: t.resp!.answer, sql: t.resp!.sql }));
    setTurns((ts) => [...ts, { question: q, pending: true }]);
    try {
      const resp = await api.adminInsightsAsk(q, history);
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { question: q, resp } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { question: q, error: errText(e) } : t)));
    }
  };

  const feedback = async (id: number, v: -1 | 0 | 1) => {
    try {
      const r = await api.adminInsightsFeedback(id, v);
      setTurns((ts) => ts.map((t) => (t.resp?.query_id === id ? { ...t, resp: { ...t.resp, feedback: r.feedback } } : t)));
      haptic("light");
    } catch {
      /* ignore */
    }
  };

  const openFromHistory = async (id: number) => {
    try {
      const resp = await api.adminInsightsQuery(id);
      setTurns([{ question: resp.question, resp }]);
      setView("ask");
    } catch {
      /* ignore */
    }
  };

  const kn = status?.knowledge ?? {};
  const knTotal = Object.values(kn).reduce((a, b) => a + b.count, 0);
  const knEmbedded = Object.values(kn).reduce((a, b) => a + b.embedded, 0);

  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <Sparkles className="size-5 text-primary" />
          <SectionLabel>Data analyst</SectionLabel>
          {view === "ask" && turns.length > 0 && (
            <button
              onClick={() => setTurns([])}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-muted-foreground disabled:opacity-40"
            >
              <MessageSquarePlus className="size-3.5" /> New chat
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Pill ok={status ? status.openai : null}>
            <Sparkles className="size-3" /> {status ? (status.openai ? status.model : "no OpenAI key") : "…"}
          </Pill>
          <Pill ok={status ? status.readonly.ready : null}>
            <KeyRound className="size-3" /> {status ? (status.readonly.ready ? "read-only DB" : "read-only role down") : "…"}
          </Pill>
          <Pill ok={status ? knEmbedded > 0 : null}>
            <BookOpen className="size-3" /> {knTotal} chunks{knTotal ? ` · ${knEmbedded} embedded` : ""}
          </Pill>
          {status && <Pill ok={null}>UTC{status.tz_offset}</Pill>}
        </div>
        {status && !status.readonly.ready && status.readonly.error && (
          <div className="text-[11px] text-red">{status.readonly.error}</div>
        )}
        <div className="flex gap-1">
          {(
            [
              ["ask", "Ask", Sparkles],
              ["knowledge", "Knowledge", Brain],
              ["history", "History", History],
            ] as const
          ).map(([v, label, Icon]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl py-1.5 text-[11px] font-bold",
                view === v ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground",
              )}
            >
              <Icon className="size-3.5" /> {label}
            </button>
          ))}
        </div>
      </Card>

      {view === "knowledge" && <KnowledgeManager />}
      {view === "history" && <InsightsHistory onOpen={openFromHistory} />}

      {view === "ask" && (
        <>
          {turns.length === 0 && (
            <Card>
              <SectionLabel>Ask anything about the game data</SectionLabel>
              <p className="mt-1 text-xs text-muted-foreground">
                The analyst reads the schema and business docs, writes SQL, runs it on a read-only connection and
                explains the result. Follow-up questions keep the context.
              </p>
              <div className="mt-2.5 flex flex-col gap-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="rounded-xl bg-secondary/60 px-3 py-2 text-left text-xs ring-1 ring-white/5 active:scale-[0.98]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </Card>
          )}

          {turns.map((t, i) => (
            <div key={i} className="flex flex-col gap-2">
              <div className="flex justify-end">
                <div dir="auto" className="max-w-[85%] rounded-2xl rounded-br-md bg-primary/90 px-3 py-2 text-sm font-medium text-primary-foreground">
                  {t.question}
                </div>
              </div>
              <AnswerCard turn={t} onAsk={ask} onOpenSql={onOpenSql} onFeedback={feedback} />
            </div>
          ))}
          <div ref={endRef} />

          <div className="sticky bottom-2 z-20">
            <div className="flex items-end gap-2 rounded-2xl bg-card/95 p-2 ring-1 ring-white/10 backdrop-blur">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    ask(draft);
                  }
                }}
                dir="auto"
                rows={1}
                placeholder={turns.length ? "Ask a follow-up…" : "e.g. how many wins does Nila have last week?"}
                className="max-h-32 min-h-9 min-w-0 flex-1 resize-none rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-primary/50"
              />
              <Button size="icon" onClick={() => ask(draft)} disabled={busy || !draft.trim()}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- sql */

const SQL_STARTER = `-- read-only: SELECT / WITH / EXPLAIN. Ctrl+Enter to run.
SELECT id, first_name, username, coins, games_won, created_at
FROM users
WHERE NOT is_bot
ORDER BY created_at DESC
LIMIT 50`;

export function SqlPanel({ incoming }: { incoming?: { sql: string; n: number } | null }) {
  const [sql, setSql] = useState(SQL_STARTER);
  const [maxRows, setMaxRows] = useState(500);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<SqlRunResult | null>(null);
  const [schema, setSchema] = useState<SchemaTable[] | null>(null);
  const [roError, setRoError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [showSchema, setShowSchema] = useState(false);
  const [hist, setHist] = useState<SqlHistoryRow[]>([]);
  const [chartOn, setChartOn] = useState(false);
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const loadHist = () => api.adminSqlHistory(30).then(setHist).catch(() => {});
  useEffect(() => {
    api
      .adminSqlSchema()
      .then((s) => {
        setSchema(s.tables);
        setRoError(s.readonly.ready ? null : s.readonly.error);
      })
      .catch((e) => setRoError(errText(e)));
    loadHist();
  }, []);

  const run = async (text?: string) => {
    const q = (text ?? sql).trim();
    if (!q || busy) return;
    setBusy(true);
    haptic("light");
    try {
      setRes(await api.adminSqlRun(q, maxRows));
    } catch (e) {
      setRes({ ok: false, error: errText(e), run_id: 0, max_rows: maxRows });
    } finally {
      setBusy(false);
      loadHist();
    }
  };

  useEffect(() => {
    if (incoming?.sql) {
      setSql(incoming.sql);
      run(incoming.sql);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming?.n]);

  const insert = (text: string) => {
    const el = ref.current;
    if (!el) {
      setSql((s) => s + text);
      return;
    }
    const a = el.selectionStart ?? sql.length;
    const b = el.selectionEnd ?? sql.length;
    const next = sql.slice(0, a) + text + sql.slice(b);
    setSql(next);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(a + text.length, a + text.length);
    });
  };

  const tables = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!schema) return [];
    if (!f) return schema;
    return schema.filter((t) => t.name.includes(f) || t.columns.some((c) => c.name.includes(f)));
  }, [schema, filter]);

  const table: SqlTable | null =
    res && res.ok && res.columns
      ? {
          columns: res.columns,
          rows: res.rows ?? [],
          row_count: res.row_count ?? 0,
          truncated: !!res.truncated,
          duration_ms: res.duration_ms ?? 0,
        }
      : null;
  const autoChart: InsightChart | null = useMemo(() => {
    if (!table || table.columns.length < 2 || table.rows.length < 2) return null;
    const yi = table.columns.findIndex((_, i) => i > 0 && table.rows.every((r) => typeof r[i] === "number" || r[i] === null));
    if (yi < 0) return null;
    const x = table.columns[0].name;
    const isTime = table.rows.every((r) => typeof r[0] === "string" && /^\d{4}-\d{2}-\d{2}/.test(r[0] as string));
    if (!isTime && table.rows.length > 30) return null;
    return { type: isTime ? "line" : "bar", x, y: table.columns[yi].name };
  }, [table]);

  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Database className="size-4 text-primary" />
          <SectionLabel>Native SQL</SectionLabel>
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-[#3fa66a]/15 px-2 py-0.5 text-[10px] font-bold text-[#5fc58a] ring-1 ring-[#3fa66a]/30">
            <KeyRound className="size-3" /> READ-ONLY
          </span>
        </div>
        {roError && <div className="rounded-lg bg-red/10 px-2.5 py-1.5 text-[11px] text-red">{roError}</div>}
        <textarea
          ref={ref}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              run();
            } else if (e.key === "Tab") {
              e.preventDefault();
              insert("  ");
            }
          }}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          rows={9}
          dir="ltr"
          className="w-full resize-y rounded-xl bg-[#0d1119] px-3 py-2.5 font-mono text-[12px] leading-relaxed text-[#d6e0f5] outline-none ring-1 ring-white/10 focus:ring-primary/50"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => run()} disabled={busy || !sql.trim()}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />} Run
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy || !sql.trim()}
            onClick={() => run(/^\s*explain\b/i.test(sql) ? sql : `EXPLAIN ${sql.replace(/^\s*(--[^\n]*\n\s*)*/, "")}`)}
          >
            Explain
          </Button>
          <select
            value={maxRows}
            onChange={(e) => setMaxRows(Number(e.target.value))}
            className="h-9 rounded-2xl bg-secondary px-2.5 text-xs ring-1 ring-white/10"
          >
            {[100, 500, 1000].map((n) => (
              <option key={n} value={n}>
                max {n} rows
              </option>
            ))}
          </select>
          <button onClick={() => setShowSchema((v) => !v)} className="ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-muted-foreground">
            <Table2 className="size-3.5" /> Schema
          </button>
        </div>
      </Card>

      {res && !res.ok && (
        <Card className="flex items-start gap-2 bg-red/10 text-red ring-red/20">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words font-mono text-[11px]">{res.error}</pre>
        </Card>
      )}

      {table && (
        <Card className="flex flex-col gap-2 p-2.5">
          {autoChart && (
            <div className="flex items-center gap-2 px-0.5">
              <button
                onClick={() => setChartOn((v) => !v)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold uppercase",
                  chartOn ? "bg-primary/20 text-primary" : "bg-secondary text-muted-foreground",
                )}
              >
                <ChartLine className="size-3" /> Chart
              </button>
              {chartOn && (
                <span className="text-[10px] text-muted-foreground">
                  {autoChart.y} by {autoChart.x}
                </span>
              )}
            </div>
          )}
          {chartOn && autoChart && (
            <div className="rounded-xl bg-black/20 p-2.5 ring-1 ring-white/5">
              <MiniChart table={table} chart={autoChart} />
            </div>
          )}
          <ResultTable table={table} />
        </Card>
      )}

      {showSchema && (
        <Card>
          <div className="flex items-center gap-2 rounded-xl bg-secondary px-2.5 ring-1 ring-white/10">
            <Search className="size-3.5 text-muted-foreground" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter tables or columns…"
              className="h-9 min-w-0 flex-1 bg-transparent text-xs outline-none"
            />
          </div>
          <div className="no-scrollbar mt-2 flex max-h-[55vh] flex-col gap-1 overflow-y-auto">
            {schema === null && <div className="text-xs text-muted-foreground">Loading…</div>}
            {tables.map((t) => (
              <div key={t.name} className="rounded-xl bg-secondary/50 ring-1 ring-white/5">
                <div className="flex items-center gap-2 px-2.5 py-1.5">
                  <button
                    onClick={() => setOpenTable(openTable === t.name ? null : t.name)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", openTable === t.name && "rotate-90")} />
                    <span className={cn("truncate font-mono text-xs font-semibold", t.internal && "text-muted-foreground")}>{t.name}</span>
                  </button>
                  <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                    {t.rows !== null ? t.rows.toLocaleString() : ""}
                  </span>
                  <button
                    onClick={() => {
                      const cols = t.columns.filter((c) => c.name !== "state").map((c) => c.name);
                      const pk = t.columns.find((c) => c.pk)?.name ?? cols[0];
                      setSql(`SELECT ${cols.join(", ")}\nFROM ${t.name}\nORDER BY ${pk} DESC\nLIMIT 50`);
                    }}
                    className="shrink-0 rounded-md bg-primary/15 px-1.5 py-0.5 text-[10px] font-bold text-primary"
                  >
                    SELECT
                  </button>
                </div>
                {openTable === t.name && (
                  <div className="border-t border-white/5 px-2.5 py-1.5">
                    {t.doc && <p className="mb-1.5 text-[10.5px] leading-snug text-muted-foreground">{t.doc}</p>}
                    <div className="flex flex-col">
                      {t.columns.map((c) => (
                        <button
                          key={c.name}
                          onClick={() => insert(c.name)}
                          title={c.note || undefined}
                          className="flex items-center gap-1.5 rounded px-1 py-0.5 text-left hover:bg-white/5"
                        >
                          <span className="font-mono text-[11px]">{c.name}</span>
                          {c.pk && <KeyRound className="size-2.5 text-primary" />}
                          {c.fk && (
                            <span className="inline-flex items-center gap-0.5 text-[9px] text-muted-foreground">
                              <Link2 className="size-2.5" />
                              {c.fk}
                            </span>
                          )}
                          <span className="ml-auto truncate font-mono text-[9.5px] text-muted-foreground">{c.type}</span>
                        </button>
                      ))}
                    </div>
                    {t.hidden_columns.length > 0 && (
                      <div className="mt-1 text-[10px] text-muted-foreground/70">private: {t.hidden_columns.join(", ")}</div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="flex items-center justify-between">
          <SectionLabel>History</SectionLabel>
          <button onClick={loadHist} className="text-[11px] text-muted-foreground">
            Refresh
          </button>
        </div>
        <div className="mt-2 flex flex-col gap-1">
          {hist.map((h) => (
            <button
              key={h.id}
              onClick={() => setSql(h.sql)}
              className="flex items-center gap-2 rounded-lg bg-secondary/40 px-2.5 py-1.5 text-left"
            >
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ background: h.status === "ok" ? "#3fa66a" : h.status === "rejected" ? "#e0a44a" : "#d76a6a" }}
              />
              <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                {h.sql.split("\n").find((l) => l.trim() && !l.trim().startsWith("--")) ?? h.sql}
              </span>
              <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                {h.status === "ok" ? `${h.row_count} · ${h.duration_ms}ms` : h.status}
              </span>
            </button>
          ))}
          {hist.length === 0 && <div className="text-xs text-muted-foreground">No queries yet.</div>}
        </div>
      </Card>
    </div>
  );
}
