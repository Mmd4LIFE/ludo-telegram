// WebSocket client for a live match. Mirrors the server payloads in routes_ws.py.

import { getToken } from "./api";

export interface TokenState {
  color: string;
  tokens: number[]; // progress per token: -1 base, 0..50 ring, 51..56 home column, 56 HOME
  finished_at: number | null;
}

export interface GameState {
  players: TokenState[];
  current: number;
  phase: "roll" | "move" | "finished";
  die: number | null;
  consecutive_sixes: number;
  turn: number;
  ranking: number[];
}

export interface LegalMove {
  token_index: number;
  src: number;
  dst: number;
  releases_from_base: boolean;
  reaches_home: boolean;
  captures: number[][];
}

export interface StatePayload {
  type: "state";
  code: string;
  state: GameState;
  seat_user: Record<string, number | null>;
  seat_names: Record<string, string>;
  legal_moves: LegalMove[];
  deadline: number | null; // unix seconds the current player must act by (or null)
  now: number; // server unix seconds at send time (for clock-skew correction)
  turn_seconds: number; // full turn length, for the countdown bar
}

type Handler = (msg: StatePayload | { type: string; [k: string]: unknown }) => void;

export class MatchSocket {
  private ws: WebSocket | null = null;
  private code: string;
  private onMsg: Handler;
  private closed = false;

  constructor(code: string, onMsg: Handler) {
    this.code = code;
    this.onMsg = onMsg;
  }

  connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/match/${this.code}?token=${getToken()}`;
    this.ws = new WebSocket(url);
    this.ws.onmessage = (e) => this.onMsg(JSON.parse(e.data));
    this.ws.onclose = () => {
      if (!this.closed) setTimeout(() => this.connect(), 1500); // simple reconnect
    };
  }

  private send(obj: unknown): void {
    this.ws?.readyState === WebSocket.OPEN && this.ws.send(JSON.stringify(obj));
  }

  roll(): void {
    this.send({ type: "roll" });
  }
  move(tokenIndex: number): void {
    this.send({ type: "move", token_index: tokenIndex });
  }
  emote(emote: string): void {
    this.send({ type: "emote", emote });
  }
  rematch(): void {
    this.send({ type: "rematch" });
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}
