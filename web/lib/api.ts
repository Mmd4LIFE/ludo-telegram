// Tiny REST client. Same-origin (nginx proxies /api to the backend), so no base URL.
// Holds the session JWT in memory + localStorage and attaches it as a Bearer header.

import { getInitData } from "./telegram";
import type { Card } from "./cards";

const TOKEN_KEY = "ludo_token";

let token: string | null =
  typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;

export function getToken(): string | null {
  return token;
}

function setToken(t: string): void {
  token = t;
  if (typeof window !== "undefined") localStorage.setItem(TOKEN_KEY, t);
}

const FETCH_TIMEOUT_MS = 8000;

async function req<T>(method: string, path: string, body?: unknown, timeoutMs = FETCH_TIMEOUT_MS): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status}: ${text || res.statusText}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export interface Profile {
  id: number;
  first_name: string;
  username: string | null;
  coins: number;
  level: number;
  xp: number;
  games_played: number;
  games_won: number;
  wallet_code: string;
  bot_username: string;
  is_admin: boolean;
  dice_skin: string;
  unlocked_skins?: string[];
}

export interface AdminUser {
  id: number;
  first_name: string;
  username: string | null;
  coins: number;
  level: number;
  xp: number;
  games_played: number;
  games_won: number;
  bot_started: boolean;
  is_banned: boolean;
  last_seen_at: string | null;
  created_at: string | null;
}

export interface AdminStats {
  users: number;
  users_started: number;
  games_played: number;
  coins_in_circulation: number;
  matches_playing: number;
  matches_waiting: number;
  matches_finished: number;
  matches_abandoned: number;
}

export interface SeatInfo {
  seat_index: number;
  color: string;
  name: string;
  is_bot: boolean;
  user_id: number | null;
}

export interface PendingJoiner {
  user_id: number;
  name: string;
}

export interface MatchSummary {
  code: string;
  status: string;
  max_players: number;
  seated: number;
  is_public: boolean;
  entry_fee: number;
  created_by: number | null;
  created_at?: string | null;
  seats: SeatInfo[];
  pending: PendingJoiner[];
}

export interface PollOption {
  id: number;
  text: string;
  position: number;
  votes: number;
}

export interface Poll {
  id: number;
  question: string;
  kind: string;
  status: string;
  total_votes: number;
  my_vote: number | null;
  options: PollOption[];
}

export interface PollTemplate {
  id: number;
  question: string;
  options: string[];
  trigger: string; // "knock" | "any"
  enabled: boolean;
}

export interface ChatMessage {
  id: number;
  user_id: number;
  name: string;
  text: string;
  edited: boolean;
  reply_to: number | null;
  reply_name: string | null;
  reply_text: string | null;
  reactions: Record<string, number>; // emoji -> count
  my_reaction: string | null;
  poll: Poll | null;
}

// Keep in step with ALLOWED_REACTIONS on the backend.
export const REACTIONS = ["👍", "❤️", "😂", "🔥"] as const;

export interface AdminReaction {
  id: number;
  emoji: string;
  position: number;
}

export interface InsightStep {
  seq: number;
  stage: string; // retrieve | llm | search_knowledge | describe_table | run_sql | answer
  status: string; // ok | error
  duration_ms: number;
  detail: Record<string, unknown>;
}

export interface SqlColumn {
  name: string;
  type: string;
}

export interface SqlTable {
  columns: SqlColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  duration_ms: number;
}

export interface InsightChart {
  type: "bar" | "line";
  x: string;
  y: string;
}

export interface InsightSource {
  key: string;
  title: string;
  kind: string;
  score: number;
}

export interface InsightAnswer {
  query_id: number;
  status: string; // answered | error
  question: string;
  answer: string;
  sql: string | null;
  table: SqlTable | null;
  chart: InsightChart | null;
  follow_ups: string[];
  sources: InsightSource[];
  steps: InsightStep[];
  model: string;
  latency_ms: number;
  usage: Record<string, number>;
  feedback: number | null;
  error?: string | null;
  created_at?: string | null;
}

export interface InsightLogRow {
  id: number;
  question: string;
  status: string;
  answer: string;
  has_sql: boolean;
  feedback: number | null;
  latency_ms: number;
  model: string;
  created_at: string | null;
}

export interface InsightStatus {
  openai: boolean;
  model: string;
  embed_model: string;
  tz_offset: string;
  readonly: { role: string; ready: boolean; error: string | null };
  knowledge: Record<string, { count: number; embedded: number }>;
}

export interface KnowledgeEntry {
  id: number;
  key: string;
  kind: string; // table | doc | example | note
  source: string; // auto | curated | learned | admin
  title: string;
  content: string;
  enabled: boolean;
  embedded: boolean;
  editable: boolean;
  updated_at: string | null;
}

export interface SqlRunResult extends Partial<SqlTable> {
  ok: boolean;
  error: string | null;
  run_id: number;
  max_rows: number;
}

export interface SqlHistoryRow {
  id: number;
  sql: string;
  status: string; // ok | error | rejected
  row_count: number;
  duration_ms: number;
  error: string | null;
  mine: boolean;
  created_at: string | null;
}

export interface SchemaTable {
  name: string;
  doc: string;
  internal: boolean;
  columns: { name: string; type: string; nullable: boolean; pk: boolean; fk: string | null; note: string }[];
  hidden_columns: string[];
  referenced_by: string[];
  rows: number | null;
}

export interface AppConfig {
  key: string;
  label: string;
  help: string;
  value: number;
  default: number;
  min: number;
  max: number;
  is_set: boolean;
}

export interface AdminChatSeat {
  seat_index: number;
  color: string;
  name: string;
  user_id: number | null;
  is_bot: boolean;
}

export interface AdminChatEntry {
  id: number;
  user_id: number;
  name: string;
  text: string;
  edited: boolean;
  deleted: boolean;
  created_at: string | null;
  reply_name: string | null;
  reply_text: string | null;
  reactions: Record<string, number>;
}

export interface AdminChatView {
  id: number;
  code: string;
  status: string;
  created_at: string | null;
  seats: AdminChatSeat[];
  messages: AdminChatEntry[];
}

export interface AdminTable {
  name: string;
  rows: number;
  columns: string[];
}

export interface AdminRows {
  table: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}

export interface DiceEntry {
  user_id: number;
  name: string;
  rolls: number;
  total: number;
  avg: number;
  best: number;
  last_value: number;
}

export interface DiceState {
  cooldown: number;
  ranking: DiceEntry[];
}

export interface PlayerStats {
  id: number;
  first_name: string;
  level: number;
  games_played: number;
  games_won: number;
  dice: Record<string, number>; // {"1": n, ..., "6": n}
  dice_rolls: number;
  dice_avg: number;
  captures_dealt: number;
  captures_taken: number;
  potential_knocks: number;
}

export interface KnockEvent {
  id: number;
  turn: number;
  taken: boolean;
  attacker_user_id: number;
  attacker_seat: number;
  attacker_name: string;
  victim_user_id: number | null;
  victim_seat: number;
  victim_name: string;
}

export interface AdminKnockRow {
  id: number;
  first_name: string;
  knocks: number;
  knocked: number;
  potential: number;
}

export interface BalanceHistoryRow {
  id: number;
  delta: number;
  balance_after: number;
  reason: string;
  ref_type: string | null;
  ref_id: number | null;
  meta: Record<string, unknown> | null;
  created_at: string | null;
}

export interface TransferResult {
  id: number;
  amount: number;
  status: string;
  note: string | null;
  claim_token: string | null;
  share_url: string | null;
  created_at: string | null;
  claimed_at: string | null;
  coins: number;
}

// Authenticate via Telegram initData; falls back to dev login in a plain browser.
export async function authenticate(): Promise<Profile> {
  const initData = getInitData();
  let resp: { token: string; user: Profile };
  if (initData) {
    resp = await req("POST", "/api/auth/telegram", { init_data: initData });
  } else {
    // No Telegram context (SDK blocked, or opened in a plain browser). Dev login is
    // refused in production, so surface something a player can act on.
    try {
      resp = await req("POST", "/api/auth/dev", {
        telegram_id: 111111,
        first_name: "Dev",
        username: "dev",
      });
    } catch {
      throw new Error("Please open Ludo from the Telegram bot (tap Play in @ludoboard_bot).");
    }
  }
  setToken(resp.token);
  return resp.user;
}

export const api = {
  me: () => req<Profile>("GET", "/api/profile/me"),
  setDiceSkin: (skin: string) => req<Profile>("POST", "/api/profile/dice-skin", { skin }),
  buyDiceSkin: (skin: string) =>
    req<Profile>("POST", "/api/profile/dice-skin/buy", { skin }),
  diceSkinCatalog: () =>
    req<{ id: string; price: number }[]>("GET", "/api/profile/dice-skins"),
  listMatches: () => req<MatchSummary[]>("GET", "/api/matches"),
  getMatch: (code: string) => req<MatchSummary>("GET", `/api/matches/${code}`),
  createMatch: (opts: {
    max_players?: number;
    is_public?: boolean;
    entry_fee?: number;
    fill_with_bots?: boolean;
  }) => req<MatchSummary>("POST", "/api/matches", opts),
  joinMatch: (code: string) =>
    req<MatchSummary>("POST", "/api/matches/join", { code }),
  acceptJoiner: (code: string, user_id: number, color: string) =>
    req<MatchSummary>("POST", `/api/matches/${code}/accept`, { user_id, color }),
  rejectJoiner: (code: string, user_id: number) =>
    req<MatchSummary>("POST", `/api/matches/${code}/reject`, { user_id }),
  setColor: (code: string, user_id: number, color: string) =>
    req<MatchSummary>("POST", `/api/matches/${code}/color`, { user_id, color }),
  startMatch: (code: string) => req<MatchSummary>("POST", `/api/matches/${code}/start`),
  deleteMatch: (code: string) => req<{ ok: boolean }>("DELETE", `/api/matches/${code}`),
  getDice: (code: string) => req<DiceState>("GET", `/api/matches/${code}/dice`),
  rollDice: (code: string) => req<DiceState>("POST", `/api/matches/${code}/dice`),
  getChat: (code: string) => req<ChatMessage[]>("GET", `/api/matches/${code}/chat`),
  scoreboard: (limit = 50) => req<PlayerStats[]>("GET", `/api/scoreboard?limit=${limit}`),
  userProfile: (id: number) => req<PlayerStats>("GET", `/api/users/${id}/profile`),
  getReactions: () => req<string[]>("GET", "/api/reactions"),
  getCards: () => req<Card[]>("GET", "/api/cards"),
  getPollTemplates: () => req<PollTemplate[]>("GET", "/api/poll-templates"),
  createPoll: (code: string, template_id: number) =>
    req<ChatMessage[]>("POST", `/api/matches/${code}/polls`, { template_id }),
  votePoll: (code: string, poll_id: number, option_id: number) =>
    req<ChatMessage[]>("POST", `/api/matches/${code}/polls/${poll_id}/vote`, { option_id }),
  matchKnocks: (code: string) => req<KnockEvent[]>("GET", `/api/matches/${code}/knocks`),
  adminStats: () => req<AdminStats>("GET", "/api/admin/stats"),
  adminKnocks: () => req<AdminKnockRow[]>("GET", "/api/admin/knocks"),
  adminUsers: (q?: string) =>
    req<AdminUser[]>("GET", `/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  adminCreditUser: (userId: number, amount: number, note?: string) =>
    req<{ user_id: number; coins: number; amount: number }>(
      "POST",
      `/api/admin/users/${userId}/credit`,
      { amount, note: note ?? null },
    ),
  walletHistory: (limit = 40, offset = 0) =>
    req<BalanceHistoryRow[]>(
      "GET",
      `/api/wallet/history?limit=${limit}&offset=${offset}`,
    ),
  walletLookup: (code: string) =>
    req<{ wallet_code: string; first_name: string }>(
      "GET",
      `/api/wallet/lookup?code=${encodeURIComponent(code)}`,
    ),
  walletTransfer: (to_wallet: string, amount: number, note?: string) =>
    req<TransferResult>("POST", "/api/wallet/transfer", {
      to_wallet,
      amount,
      note: note ?? null,
    }),
  walletCreateGift: (amount: number, note?: string) =>
    req<TransferResult>("POST", "/api/wallet/gifts", {
      amount,
      note: note ?? null,
    }),
  walletClaimGift: (token: string) =>
    req<TransferResult>("POST", "/api/wallet/gifts/claim", { token }),
  starsPacks: () =>
    req<{ coins_per_star: number; packs: { stars: number; coins: number }[] }>(
      "GET",
      "/api/shop/stars/packs",
    ),
  starsInvoice: (pack_stars: number) =>
    req<{ invoice_link: string; payment_id: number; stars: number; coins: number }>(
      "POST",
      "/api/shop/stars/invoice",
      { pack_stars },
    ),
  adminReactions: () => req<AdminReaction[]>("GET", "/api/admin/reactions"),
  adminAddReaction: (emoji: string) =>
    req<AdminReaction[]>("POST", "/api/admin/reactions", { emoji }),
  adminRemoveReaction: (id: number) =>
    req<AdminReaction[]>("DELETE", `/api/admin/reactions/${id}`),
  adminMatchChat: (ref: string) =>
    req<AdminChatView>("GET", `/api/admin/matches/${encodeURIComponent(ref)}/chat`),
  adminAiChat: (messages: { role: string; content: string }[]) =>
    req<{ reply: string; model: string }>("POST", "/api/admin/ai/chat", { messages }),
  adminInsightsAsk: (
    question: string,
    history: { question: string; answer: string; sql: string | null }[] = [],
  ) => req<InsightAnswer>("POST", "/api/admin/insights/ask", { question, history }, 120_000),
  adminInsightsStatus: () => req<InsightStatus>("GET", "/api/admin/insights/status"),
  adminInsightsLog: (limit = 30) =>
    req<InsightLogRow[]>("GET", `/api/admin/insights/log?limit=${limit}`),
  adminInsightsQuery: (id: number) =>
    req<InsightAnswer>("GET", `/api/admin/insights/queries/${id}`),
  adminInsightsFeedback: (id: number, value: -1 | 0 | 1) =>
    req<{ ok: boolean; feedback: number | null }>(
      "POST",
      `/api/admin/insights/queries/${id}/feedback`,
      { value },
      30_000,
    ),
  adminKnowledge: (kind?: string) =>
    req<KnowledgeEntry[]>("GET", `/api/admin/insights/knowledge${kind ? `?kind=${kind}` : ""}`),
  adminAddKnowledge: (title: string, content: string) =>
    req<KnowledgeEntry>("POST", "/api/admin/insights/knowledge", { title, content }, 30_000),
  adminPatchKnowledge: (id: number, patch: { enabled?: boolean; title?: string; content?: string }) =>
    req<KnowledgeEntry>("PATCH", `/api/admin/insights/knowledge/${id}`, patch, 30_000),
  adminDeleteKnowledge: (id: number) =>
    req<{ ok: boolean }>("DELETE", `/api/admin/insights/knowledge/${id}`),
  adminReindexKnowledge: () =>
    req<{ chunks: number; changed: number; removed: number; embedded: number }>(
      "POST",
      "/api/admin/insights/reindex",
      undefined,
      90_000,
    ),
  adminSqlRun: (sql: string, max_rows = 500) =>
    req<SqlRunResult>("POST", "/api/admin/sql/run", { sql, max_rows }, 60_000),
  adminSqlHistory: (limit = 30) => req<SqlHistoryRow[]>("GET", `/api/admin/sql/history?limit=${limit}`),
  adminSqlSchema: () =>
    req<{ tables: SchemaTable[]; readonly: { ready: boolean; error: string | null }; max_rows: number }>(
      "GET",
      "/api/admin/sql/schema",
    ),
  adminConfigs: () => req<AppConfig[]>("GET", "/api/admin/configs"),
  adminSetConfig: (key: string, value: number) =>
    req<AppConfig[]>("POST", `/api/admin/configs/${key}`, { value }),
  adminResetConfig: (key: string) =>
    req<AppConfig[]>("DELETE", `/api/admin/configs/${key}`),
  adminPollTemplates: () => req<PollTemplate[]>("GET", "/api/admin/poll-templates"),
  adminAddPollTemplate: (question: string, options: string[], trigger: string) =>
    req<PollTemplate[]>("POST", "/api/admin/poll-templates", { question, options, trigger }),
  adminRemovePollTemplate: (id: number) =>
    req<PollTemplate[]>("DELETE", `/api/admin/poll-templates/${id}`),
  adminTables: () => req<AdminTable[]>("GET", "/api/admin/data/tables"),
  adminRows: (table: string, limit = 25, offset = 0) =>
    req<AdminRows>("GET", `/api/admin/data/rows/${table}?limit=${limit}&offset=${offset}`),
  sendChat: (code: string, text: string, reply_to?: number | null) =>
    req<ChatMessage[]>("POST", `/api/matches/${code}/chat`, { text, reply_to: reply_to ?? null }),
  editChat: (code: string, id: number, text: string) =>
    req<ChatMessage[]>("PATCH", `/api/matches/${code}/chat/${id}`, { text }),
  deleteChat: (code: string, id: number) =>
    req<ChatMessage[]>("DELETE", `/api/matches/${code}/chat/${id}`),
  reactChat: (code: string, id: number, emoji: string) =>
    req<ChatMessage[]>("POST", `/api/matches/${code}/chat/${id}/react`, { emoji }),
};
