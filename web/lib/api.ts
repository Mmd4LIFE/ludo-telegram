// Tiny REST client. Same-origin (nginx proxies /api to the backend), so no base URL.
// Holds the session JWT in memory + localStorage and attaches it as a Bearer header.

import { getInitData } from "./telegram";

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

export interface ChatMessage {
  id: number;
  user_id: number;
  name: string;
  text: string;
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

// Authenticate via Telegram initData; falls back to dev login in a plain browser.
export async function authenticate(): Promise<Profile> {
  const initData = getInitData();
  let resp: { token: string; user: Profile };
  if (initData) {
    resp = await req("POST", "/api/auth/telegram", { init_data: initData });
  } else {
    // local dev only — the backend refuses this when ENV=production
    resp = await req("POST", "/api/auth/dev", {
      telegram_id: 111111,
      first_name: "Dev",
      username: "dev",
    });
  }
  setToken(resp.token);
  return resp.user;
}

export const api = {
  me: () => req<Profile>("GET", "/api/profile/me"),
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
  startMatch: (code: string) => req<MatchSummary>("POST", `/api/matches/${code}/start`),
  deleteMatch: (code: string) => req<{ ok: boolean }>("DELETE", `/api/matches/${code}`),
  getDice: (code: string) => req<DiceState>("GET", `/api/matches/${code}/dice`),
  rollDice: (code: string) => req<DiceState>("POST", `/api/matches/${code}/dice`),
  getChat: (code: string) => req<ChatMessage[]>("GET", `/api/matches/${code}/chat`),
  sendChat: (code: string, text: string) =>
    req<ChatMessage[]>("POST", `/api/matches/${code}/chat`, { text }),
};
