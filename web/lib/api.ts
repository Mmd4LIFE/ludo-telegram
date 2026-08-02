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

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text || res.statusText}`);
  }
  return (await res.json()) as T;
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
  bot_username: string;
  is_admin: boolean;
  dice_skin: string;
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
  adminReactions: () => req<AdminReaction[]>("GET", "/api/admin/reactions"),
  adminAddReaction: (emoji: string) =>
    req<AdminReaction[]>("POST", "/api/admin/reactions", { emoji }),
  adminRemoveReaction: (id: number) =>
    req<AdminReaction[]>("DELETE", `/api/admin/reactions/${id}`),
  adminMatchChat: (ref: string) =>
    req<AdminChatView>("GET", `/api/admin/matches/${encodeURIComponent(ref)}/chat`),
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
