"use client";

// Ludo Board — Telegram Mini App shell.
// Views: auth splash → lobby → waiting room (with native Telegram invite) → live match.
// The board renders authoritative server state over a per-match WebSocket.

import { Component, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  Dice5,
  Users,
  Share2,
  Copy,
  ArrowLeft,
  Trophy,
  Loader2,
  Bot,
  Plus,
  Coins,
  Home as HomeIcon,
  AlertTriangle,
  Trash2,
  Shield,
  ShoppingBag,
  Gamepad2,
  User,
  Sparkles,
} from "lucide-react";
import {
  api,
  authenticate,
  AdminStats,
  AdminUser,
  ChatMessage,
  DiceState,
  MatchSummary,
  Profile,
} from "@/lib/api";
import {
  getInitData,
  haptic,
  initTelegram,
  notify,
  shareRoom,
  startParam,
} from "@/lib/telegram";
import { GameState, LegalMove, MatchSocket, StatePayload } from "@/lib/ws";
import { Button } from "@/components/ui/button";
import { Card, SectionLabel } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import Board from "@/components/board";

const COLOR_HEX: Record<string, string> = {
  RED: "#e5484d",
  GREEN: "#30a46c",
  YELLOW: "#f2b705",
  BLUE: "#3e63dd",
};

type Room = { code: string; host: boolean };
type Tab = "shop" | "friends" | "home" | "ranks" | "me";
type Clock = { deadline: number | null; now: number; recvAt: number; turnSeconds: number };

// Catch any render error so a single bad frame can never take down the whole webview
// (which Telegram surfaces as "This page couldn't load").
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <Shell>
          <Card className="text-center">
            <AlertTriangle className="mx-auto size-10 text-red" />
            <div className="mt-2 font-bold">Something glitched</div>
            <p className="mt-1 text-sm text-muted-foreground break-words">
              {String(this.state.error.message || this.state.error)}
            </p>
            <Button className="mt-4 w-full" onClick={() => location.reload()}>
              Reload
            </Button>
          </Card>
        </Shell>
      );
    }
    return this.props.children;
  }
}

export default function Page() {
  return (
    <ErrorBoundary>
      <Home />
    </ErrorBoundary>
  );
}

function Home() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [view, setView] = useState<Tab>("home");
  const [showAdmin, setShowAdmin] = useState(false);
  const [room, setRoom] = useState<Room | null>(null); // waiting room
  const [matchCode, setMatchCode] = useState<string>(""); // live match
  const [state, setState] = useState<GameState | null>(null);
  const [legal, setLegal] = useState<LegalMove[]>([]);
  const [seatUser, setSeatUser] = useState<Record<string, number | null>>({});
  const [seatNames, setSeatNames] = useState<Record<string, string>>({});
  const [clock, setClock] = useState<Clock | null>(null);
  const [rematch, setRematch] = useState<{ votes: number[]; humanIds: number[] }>({
    votes: [],
    humanIds: [],
  });
  const sockRef = useRef<MatchSocket | null>(null);

  // ---- live match socket ----
  const enterMatch = useCallback((code: string) => {
    setRoom(null);
    setMatchCode(code);
    setState(null);
    setRematch({ votes: [], humanIds: [] });
    const sock = new MatchSocket(code, (msg) => {
      if (msg.type === "state") {
        const p = msg as StatePayload;
        setState(p.state);
        setLegal(p.legal_moves);
        setSeatUser(p.seat_user);
        setSeatNames(p.seat_names ?? {});
        setClock({
          deadline: p.deadline,
          now: p.now,
          recvAt: Date.now() / 1000,
          turnSeconds: p.turn_seconds,
        });
      } else if (msg.type === "rematch") {
        setRematch({
          votes: (msg.votes as number[]) ?? [],
          humanIds: (msg.human_ids as number[]) ?? [],
        });
      } else if (msg.type === "rematch_ready") {
        enterMatch(String(msg.code));
      }
    });
    sock.connect();
    sockRef.current = sock;
  }, []);

  const leaveMatch = useCallback(() => {
    sockRef.current?.close();
    sockRef.current = null;
    setMatchCode("");
    setState(null);
    setRoom(null);
  }, []);

  // ---- auth + deep-link ----
  useEffect(() => {
    initTelegram();
    authenticate()
      .then((p) => {
        setProfile(p);
        const sp = startParam();
        if (sp && sp.startsWith("rm-")) {
          const code = sp.slice(3).toUpperCase();
          api
            .joinMatch(code)
            .then((m) => {
              if (m.status === "playing") enterMatch(code);
              else setRoom({ code, host: m.created_by === p.id });
            })
            .catch((e) => setError(String(e)));
        }
      })
      .catch((e) => setError(String(e)));
  }, [enterMatch]);

  // ---- lobby actions ----
  const playBots = async () => {
    setBusy(true);
    haptic("medium");
    try {
      const m = await api.createMatch({ max_players: 4, fill_with_bots: true });
      enterMatch(m.code);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const createRoom = async () => {
    setBusy(true);
    haptic("medium");
    try {
      const m = await api.createMatch({ max_players: 4 });
      setRoom({ code: m.code, host: true });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const joinFromList = async (code: string) => {
    setBusy(true);
    haptic("light");
    try {
      const m = await api.joinMatch(code);
      // An unstarted room takes you to its lobby (the host must accept + start);
      // a game already in progress drops you straight onto the board.
      if (m.status === "playing") enterMatch(code);
      else setRoom({ code, host: m.created_by === profile?.id });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- render ----
  if (error)
    return (
      <Shell>
        <Card className="text-center">
          <AlertTriangle className="mx-auto size-10 text-red" />
          <p className="mt-2 text-sm text-muted-foreground break-words">{error}</p>
          <Button className="mt-4 w-full" onClick={() => location.reload()}>
            Reload
          </Button>
        </Card>
      </Shell>
    );

  if (!profile) return <Splash />;

  if (showAdmin) return <AdminPanel onBack={() => setShowAdmin(false)} />;

  if (matchCode && state)
    return (
      <LiveMatch
        code={matchCode}
        state={state}
        legal={legal}
        profile={profile}
        seatUser={seatUser}
        seatNames={seatNames}
        clock={clock}
        rematch={rematch}
        sock={sockRef.current}
        onLeave={leaveMatch}
      />
    );

  if (matchCode && !state) return <Splash label="Joining game…" />;

  if (room)
    return (
      <WaitingRoom
        room={room}
        profile={profile}
        onEnter={enterMatch}
        onBack={() => setRoom(null)}
      />
    );

  return (
    <>
      {view === "home" && (
        <Lobby
          profile={profile}
          busy={busy}
          onPlayBots={playBots}
          onCreateRoom={createRoom}
          onJoin={joinFromList}
        />
      )}
      {view === "me" && <MeScreen profile={profile} onAdmin={() => setShowAdmin(true)} />}
      {view === "shop" && <ComingSoon title="Shop" />}
      {view === "friends" && <ComingSoon title="Friends" />}
      {view === "ranks" && <ComingSoon title="Ranks" />}
      <BottomNav view={view} onChange={setView} />
    </>
  );
}

/* ------------------------------------------------------------------ shells */

function Shell({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main
      className={cn(
        "mx-auto flex min-h-dvh w-full max-w-md flex-col gap-4 px-4 pt-5",
        className ?? "pb-8"
      )}
    >
      {children}
    </main>
  );
}

function Splash({ label = "Loading…" }: { label?: string }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4">
      <Dice5 className="size-12 text-primary lb-pop" />
      <div className="text-lg font-extrabold tracking-widest text-primary">LUDO BOARD</div>
      <Loader2 className="size-5 text-muted-foreground lb-spin" />
      <div className="text-xs text-muted-foreground">{label}</div>
    </main>
  );
}

/* ------------------------------------------------------------------ lobby */

function WalletBar({ profile }: { profile: Profile }) {
  const initial = (profile.first_name || "P").slice(0, 1).toUpperCase();
  const xpPct = Math.min(100, ((profile.xp % 500) / 500) * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-11 place-items-center rounded-2xl bg-gradient-to-br from-secondary to-card text-lg font-bold ring-1 ring-white/10">
        {initial}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-bold">{profile.first_name || "Player"}</span>
          <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-bold text-muted-foreground">
            LVL {profile.level}
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-secondary">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-[#e58e26]"
            style={{ width: `${xpPct}%` }}
          />
        </div>
      </div>
      <div className="flex items-center gap-1.5 rounded-2xl bg-secondary px-3 py-2 ring-1 ring-white/10">
        <Coins className="size-4 text-primary" />
        <span className="text-sm font-bold tabular-nums">
          {profile.coins.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

function Lobby({
  profile,
  busy,
  onPlayBots,
  onCreateRoom,
  onJoin,
}: {
  profile: Profile;
  busy: boolean;
  onPlayBots: () => void;
  onCreateRoom: () => void;
  onJoin: (code: string) => void;
}) {
  const [tables, setTables] = useState<MatchSummary[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listMatches()
        .then((t) => alive && setTables(t))
        .catch(() => {});
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <Shell className="pb-28">
      <WalletBar profile={profile} />

      {/* the two things you open the app to do — no marketing copy */}
      <div className="grid grid-cols-2 gap-2.5">
        <Tile icon={Bot} title="Quick Game" hot wide disabled={busy} onClick={onPlayBots} />
        <Tile icon={Plus} title="Create Room" wide disabled={busy} onClick={onCreateRoom} />
      </div>

      <div className="flex flex-col gap-2">
        <SectionLabel className="px-1">Open rooms</SectionLabel>
        {tables.length === 0 ? (
          <Card className="text-center text-xs text-muted-foreground">
            No open rooms right now.
          </Card>
        ) : (
          tables.map((t) => (
            <button
              key={t.code}
              disabled={busy}
              onClick={() => onJoin(t.code)}
              className="flex items-center justify-between rounded-2xl bg-card px-4 py-3 text-left ring-1 ring-white/10 transition active:scale-[0.98] disabled:opacity-50"
            >
              <div className="flex items-center gap-3">
                <Users className="size-4 text-muted-foreground" />
                <div>
                  <div className="font-bold tracking-wider">{t.code}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {t.seated}/{t.max_players} seated
                  </div>
                </div>
              </div>
              <span className="rounded-full bg-primary/15 px-3 py-1 text-xs font-bold text-primary">
                Join
              </span>
            </button>
          ))
        )}
      </div>
    </Shell>
  );
}

function Tile({
  icon: Icon,
  title,
  onClick,
  hot,
  wide,
  disabled,
}: {
  icon: React.ElementType;
  title: string;
  onClick: () => void;
  hot?: boolean;
  wide?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex flex-col items-center gap-1.5 rounded-2xl border border-white/5 p-5 text-center transition-transform active:scale-[0.97] disabled:opacity-50",
        wide && "col-span-2",
        hot
          ? "bg-gradient-to-br from-[#b8860b] to-[#6b4e00]"
          : "bg-gradient-to-br from-secondary to-card"
      )}
    >
      <Icon className={cn("size-7", hot ? "text-white" : "text-primary")} />
      <span className="font-extrabold">{title}</span>
    </button>
  );
}

/* --------------------------------------------------------------- waiting */

function WaitingRoom({
  room,
  profile,
  onEnter,
  onBack,
}: {
  room: Room;
  profile: Profile;
  onEnter: (code: string) => void;
  onBack: () => void;
}) {
  const [summary, setSummary] = useState<MatchSummary | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dice, setDice] = useState<DiceState | null>(null);
  const [rolled, setRolled] = useState<number | null>(null);
  const [coolUntil, setCoolUntil] = useState(0);
  const [editingColor, setEditingColor] = useState<number | null>(null);

  // cooldown ticker (only while a cooldown is actually running)
  const [, tickCool] = useState(0);
  useEffect(() => {
    if (coolUntil <= Date.now()) return;
    const id = setInterval(() => tickCool((n) => n + 1), 200);
    return () => clearInterval(id);
  }, [coolUntil]);
  const coolLeft = Math.max(0, (coolUntil - Date.now()) / 1000);

  // Poll the room: seats, pending requests, chat, dice — and follow the host in.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await api.getMatch(room.code);
        if (!alive) return;
        setSummary(s);
        if (s.status === "playing") return onEnter(room.code);
        if (s.status === "abandoned") return onBack();
      } catch {
        /* transient */
      }
      try {
        const c = await api.getChat(room.code);
        if (alive) setChat(c);
      } catch {
        /* not in the room yet */
      }
      try {
        const d = await api.getDice(room.code);
        if (alive) setDice(d);
      } catch {
        /* not in the room yet */
      }
    };
    load();
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [room.code]);

  const isHost = summary ? summary.created_by === profile.id : room.host;
  const seats = summary?.seats ?? [];
  const pending = summary?.pending ?? [];
  const iAmSeated = seats.some((s) => s.user_id === profile.id);
  const taken = new Set(seats.map((s) => s.color));
  const freeColors = ["RED", "GREEN", "YELLOW", "BLUE"].filter((c) => !taken.has(c));
  const canStart = seats.length >= 2;

  const guard = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const share = () => {
    haptic("light");
    shareRoom(profile.bot_username, room.code, "Join my Ludo game!");
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        `https://t.me/${profile.bot_username}?start=rm-${room.code}`
      );
      setCopied(true);
      notify("success");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      share();
    }
  };
  const send = () =>
    guard(async () => {
      const t = draft.trim();
      if (!t) return;
      setChat(await api.sendChat(room.code, t));
      setDraft("");
    });
  const roll = () =>
    guard(async () => {
      haptic("medium");
      const d = await api.rollDice(room.code);
      setDice(d);
      setRolled(d.ranking.find((r) => r.user_id === profile.id)?.last_value ?? null);
      setCoolUntil(Date.now() + d.cooldown * 1000);
    });

  return (
    <Shell>
      {/* top bar — the code lives here as a tag rather than eating a whole card */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground"
        >
          <ArrowLeft className="size-4" /> Back
        </button>
        <span className="rounded-full bg-secondary px-3 py-1.5 text-xs font-bold tracking-[0.2em] text-primary ring-1 ring-white/10">
          {room.code}
        </span>
      </div>

      {err && (
        <div className="rounded-xl bg-red/10 px-3 py-2 text-center text-xs text-red">{err}</div>
      )}

      <Card>
        <div className="flex items-baseline justify-between gap-2">
          <SectionLabel>Players</SectionLabel>
          <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <Loader2 className="size-3 lb-spin" />
            {isHost
              ? `${seats.length}/4 seated`
              : iAmSeated
                ? "waiting for the host to start"
                : "waiting to be accepted"}
          </span>
        </div>
        {isHost && seats.length > 0 && (
          <p className="mt-1 text-[10px] text-muted-foreground">
            Tap a colour dot to change it — yours included.
          </p>
        )}
        <div className="mt-2.5 flex flex-col gap-2.5">
          {seats.length === 0 && (
            <div className="text-xs text-muted-foreground">No one seated yet.</div>
          )}
          {seats.map((s) => (
            <div key={s.seat_index} className="flex flex-col gap-2">
              <div className="flex items-center gap-3">
                <button
                  disabled={!isHost || busy}
                  onClick={() =>
                    setEditingColor(editingColor === s.user_id ? null : s.user_id)
                  }
                  className={cn(
                    "size-4 shrink-0 rounded-full transition",
                    isHost && "ring-2 ring-white/20 active:scale-90"
                  )}
                  style={{ background: COLOR_HEX[s.color] }}
                  aria-label={`colour ${s.color}`}
                />
                <span className="flex-1 truncate text-sm font-semibold">{s.name}</span>
                {s.user_id === profile.id && (
                  <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-bold text-primary">
                    YOU
                  </span>
                )}
              </div>
              {isHost && editingColor === s.user_id && s.user_id !== null && (
                <div className="flex items-center gap-2 pl-7">
                  {["RED", "GREEN", "YELLOW", "BLUE"].map((c) => (
                    <button
                      key={c}
                      disabled={busy}
                      aria-label={c}
                      onClick={() =>
                        guard(async () => {
                          haptic("light");
                          setSummary(await api.setColor(room.code, s.user_id!, c));
                          setEditingColor(null);
                        })
                      }
                      className={cn(
                        "size-7 rounded-full transition active:scale-90",
                        s.color === c ? "ring-2 ring-white" : "ring-1 ring-white/20"
                      )}
                      style={{ background: COLOR_HEX[c] }}
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      {isHost && pending.length > 0 && (
        <Card>
          <SectionLabel>Requests to join</SectionLabel>
          <div className="mt-2.5 flex flex-col gap-3">
            {pending.map((p) => (
              <div key={p.user_id} className="flex flex-col gap-2 rounded-xl bg-secondary/50 p-2.5">
                <div className="flex items-center gap-2">
                  <span className="flex-1 truncate text-sm font-semibold">{p.name}</span>
                  <button
                    disabled={busy}
                    onClick={() =>
                      guard(async () =>
                        setSummary(await api.rejectJoiner(room.code, p.user_id))
                      )
                    }
                    className="rounded-lg px-2 py-1 text-xs font-bold text-muted-foreground"
                  >
                    Decline
                  </button>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                    Give colour
                  </span>
                  {freeColors.map((c) => (
                    <button
                      key={c}
                      disabled={busy}
                      aria-label={c}
                      onClick={() =>
                        guard(async () => {
                          haptic("medium");
                          setSummary(await api.acceptJoiner(room.code, p.user_id, c));
                        })
                      }
                      className="size-7 rounded-full ring-2 ring-white/20 transition active:scale-90"
                      style={{ background: COLOR_HEX[c] }}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Button onClick={share}>
          <Share2 className="size-4" /> Invite
        </Button>
        <Button variant="secondary" onClick={copy}>
          <Copy className="size-4" /> {copied ? "Copied" : "Copy link"}
        </Button>
      </div>

      {/* chat — Telegram-style: yours on the right, others on the left */}
      <Card>
        <SectionLabel>Room chat</SectionLabel>
        {/* Fixed frame, bottom-anchored like Telegram: flex-col-reverse makes the first
            DOM child sit on the floor and keeps the view stuck to the newest message,
            so an empty or short chat rests at the bottom instead of the top. */}
        <div className="no-scrollbar mt-2 flex h-44 flex-col-reverse gap-2 overflow-y-auto">
          {[...chat].reverse().map((m) => {
            const mine = m.user_id === profile.id;
            return (
              <div
                key={m.id}
                className={cn("flex items-end gap-2", mine ? "justify-end" : "justify-start")}
              >
                {!mine && (
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-secondary text-[11px] font-bold ring-1 ring-white/10">
                    {(m.name || "P").slice(0, 1).toUpperCase()}
                  </span>
                )}
                <div
                  className={cn(
                    "max-w-[75%] rounded-2xl px-3 py-1.5",
                    mine
                      ? "rounded-br-sm bg-primary text-primary-foreground"
                      : "rounded-bl-sm bg-secondary text-foreground"
                  )}
                >
                  {!mine && (
                    <div className="text-[10px] font-bold text-muted-foreground">{m.name}</div>
                  )}
                  <div className="break-words text-sm">{m.text}</div>
                </div>
                {mine && (
                  <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/20 text-[11px] font-bold text-primary ring-1 ring-primary/30">
                    {(profile.first_name || "Y").slice(0, 1).toUpperCase()}
                  </span>
                )}
              </div>
            );
          })}
          {chat.length === 0 && (
            <div className="text-xs text-muted-foreground">Say hi while you wait.</div>
          )}
        </div>
        <div className="mt-2 flex gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send();
            }}
            placeholder="Message…"
            maxLength={200}
            className="h-10 flex-1 rounded-xl bg-secondary px-3 text-sm outline-none ring-1 ring-white/10"
          />
          <Button size="sm" className="h-10" disabled={busy || !draft.trim()} onClick={send}>
            Send
          </Button>
        </div>
      </Card>

      {/* fun dice while you wait — server-rolled, rate-limited, ranked */}
      <Card>
        <div className="flex items-baseline justify-between">
          <SectionLabel>Lucky dice</SectionLabel>
          <span className="text-[10px] text-muted-foreground">best average wins</span>
        </div>
        <div className="mt-2.5 flex items-center gap-3">
          <div className="grid size-14 shrink-0 place-items-center rounded-2xl bg-white shadow-lg">
            <Pips n={rolled} />
          </div>
          {/* cooldown refills the button; it's ready again when full */}
          <button
            disabled={busy || coolLeft > 0}
            onClick={roll}
            className="relative h-12 flex-1 overflow-hidden rounded-2xl bg-secondary ring-1 ring-white/10 transition active:translate-y-px disabled:cursor-default"
          >
            <div
              className="absolute inset-y-0 left-0 bg-primary transition-[width] duration-200 ease-linear"
              style={{
                width: `${
                  coolLeft > 0 && dice
                    ? Math.max(0, Math.min(100, (1 - coolLeft / dice.cooldown) * 100))
                    : 100
                }%`,
              }}
            />
            <span className="relative z-10 flex h-full w-full items-center justify-center gap-2 font-bold text-white drop-shadow-[0_1px_1px_rgba(0,0,0,0.55)]">
              <Dice5 className="size-5" />
              {coolLeft > 0 ? `${Math.ceil(coolLeft)}s` : "Roll"}
            </span>
          </button>
        </div>
        {dice && dice.ranking.length > 0 && (
          <div className="mt-3 flex flex-col gap-1.5">
            {dice.ranking.map((r, i) => (
              <div
                key={r.user_id}
                className="flex items-center gap-2 rounded-lg bg-secondary/50 px-2.5 py-1.5 text-xs"
              >
                <span className="w-4 text-center font-bold text-muted-foreground">{i + 1}</span>
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-secondary text-[10px] font-bold ring-1 ring-white/10">
                  {(r.name || "P").slice(0, 1).toUpperCase()}
                </span>
                <span
                  className={cn(
                    "flex-1 truncate font-semibold",
                    r.user_id === profile.id && "text-primary"
                  )}
                >
                  {r.name}
                </span>
                <span className="tabular-nums text-muted-foreground">{r.rolls}×</span>
                <span className="tabular-nums text-muted-foreground">sum {r.total}</span>
                <span className="tabular-nums font-bold">avg {r.avg.toFixed(1)}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      {isHost ? (
        <div className="mt-auto flex flex-col gap-2 pt-2">
          <Button
            size="lg"
            variant={canStart ? "win" : "secondary"}
            disabled={!canStart || busy}
            onClick={() =>
              guard(async () => {
                await api.startMatch(room.code);
                onEnter(room.code);
              })
            }
          >
            {canStart ? "Start game" : "Need one more player"}
          </Button>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() =>
              guard(async () => {
                await api.deleteMatch(room.code);
                onBack();
              })
            }
          >
            <Trash2 className="size-4" /> Delete room
          </Button>
        </div>
      ) : (
        <p className="mt-auto pt-2 text-center text-xs text-muted-foreground">
          {iAmSeated
            ? "You're in! The host will start the game."
            : "The host needs to accept you into the room."}
        </p>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- match */

function Pips({ n }: { n: number | null }) {
  if (!n) return <span className="text-2xl font-extrabold text-[#0e1320]">–</span>;
  const layout: Record<number, [number, number][]> = {
    1: [[1, 1]],
    2: [[0, 0], [2, 2]],
    3: [[0, 0], [1, 1], [2, 2]],
    4: [[0, 0], [2, 0], [0, 2], [2, 2]],
    5: [[0, 0], [2, 0], [1, 1], [0, 2], [2, 2]],
    6: [[0, 0], [2, 0], [0, 1], [2, 1], [0, 2], [2, 2]],
  };
  return (
    <div className="grid size-11 grid-cols-3 grid-rows-3 gap-0.5 p-1.5">
      {Array.from({ length: 9 }).map((_, i) => {
        const c = i % 3,
          r = Math.floor(i / 3);
        const on = layout[n].some(([x, y]) => x === c && y === r);
        return (
          <div
            key={i}
            className={cn("rounded-full", on ? "bg-[#0e1320]" : "bg-transparent")}
          />
        );
      })}
    </div>
  );
}

function RollingDie({ die, turn }: { die: number | null; turn: number }) {
  const [shown, setShown] = useState<number | null>(die);
  const [spin, setSpin] = useState(false);
  useEffect(() => {
    if (die == null) {
      setShown(null);
      return;
    }
    setSpin(true);
    let i = 0;
    const iv = setInterval(() => {
      i += 1;
      if (i >= 6) {
        clearInterval(iv);
        setShown(die);
        setSpin(false);
        haptic("rigid");
      } else {
        setShown(1 + Math.floor(Math.random() * 6));
      }
    }, 65);
    return () => clearInterval(iv);
    // re-run the tumble whenever a fresh die lands (keyed also by turn)
  }, [die, turn]);
  return (
    <div
      className={cn(
        "grid size-14 place-items-center rounded-2xl bg-white shadow-lg transition-transform",
        spin && "lb-pop"
      )}
    >
      <Pips n={shown} />
    </div>
  );
}

function RollButton({
  active,
  clock,
  onRoll,
}: {
  active: boolean;
  clock: Clock | null;
  onRoll: () => void;
}) {
  // Own the countdown here so only THIS button re-renders each tick — never the board.
  const [, tick] = useState(0);
  useEffect(() => {
    if (!active || !clock?.deadline) return;
    const id = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [active, clock?.deadline, clock?.recvAt]);
  let pct = 100;
  let seconds = 0;
  if (active && clock?.deadline) {
    const serverNow = clock.now + (Date.now() / 1000 - clock.recvAt);
    seconds = Math.max(0, clock.deadline - serverNow);
    pct = Math.max(0, Math.min(100, (seconds / clock.turnSeconds) * 100));
  }
  return (
    <button
      disabled={!active}
      onClick={onRoll}
      className="relative h-14 flex-1 overflow-hidden rounded-2xl bg-secondary ring-1 ring-white/10 transition active:translate-y-px disabled:opacity-55"
    >
      {/* gold fill drains left→right as the clock runs out */}
      {active && (
        <div
          className="absolute inset-y-0 left-0 bg-primary transition-[width] duration-200 ease-linear"
          style={{ width: `${pct}%` }}
        />
      )}
      <span className="relative z-10 flex h-full w-full items-center justify-center gap-2 font-bold text-white drop-shadow-[0_1px_1px_rgba(0,0,0,0.55)]">
        <Dice5 className="size-5" />
        {active && seconds <= 5 ? `Roll · ${Math.ceil(seconds)}s` : "Roll"}
      </span>
    </button>
  );
}

function LiveMatch({
  code,
  state,
  legal,
  profile,
  seatUser,
  seatNames,
  clock,
  rematch,
  sock,
  onLeave,
}: {
  code: string;
  state: GameState;
  legal: LegalMove[];
  profile: Profile;
  seatUser: Record<string, number | null>;
  seatNames: Record<string, string>;
  clock: Clock | null;
  rematch: { votes: number[]; humanIds: number[] };
  sock: MatchSocket | null;
  onLeave: () => void;
}) {
  const mySeat = (() => {
    for (const [seat, uid] of Object.entries(seatUser)) {
      if (uid === profile.id) return Number(seat);
    }
    return null;
  })();

  const myTurn = mySeat !== null && state.current === mySeat;
  const finished = state.phase === "finished";
  const currentColor = state.players[state.current]?.color ?? "";
  const noMoves = !finished && state.phase === "move" && legal.length === 0;

  const nameFor = (seat: number) =>
    seat === mySeat ? "You" : seatNames[String(seat)] || (seatUser[String(seat)] ? "Player" : "Bot");
  const iVoted = rematch.votes.includes(profile.id);
  const rematchNeeded = rematch.humanIds.length || Object.values(seatUser).filter(Boolean).length;

  return (
    <Shell>
      <div className="flex items-center justify-between">
        <span className="rounded-full bg-secondary px-3 py-1.5 text-xs font-bold tracking-wider ring-1 ring-white/10">
          ROOM {code}
        </span>
        <Button variant="ghost" size="sm" onClick={onLeave}>
          <ArrowLeft className="size-4" /> Leave
        </Button>
      </div>

      {/* players */}
      <div className="flex gap-2">
        {state.players.map((p, seat) => {
          const home = p.tokens.filter((t) => t >= 56).length;
          const isMe = seat === mySeat;
          const active = seat === state.current && !finished;
          return (
            <div
              key={seat}
              className={cn(
                "flex-1 rounded-2xl bg-card px-2 py-2 text-center ring-1 transition",
                active ? "ring-2 scale-[1.03]" : "ring-white/10"
              )}
              style={active ? { boxShadow: `0 0 0 2px ${COLOR_HEX[p.color]}` } : undefined}
            >
              <div className="mx-auto size-4 rounded-full" style={{ background: COLOR_HEX[p.color] }} />
              <div className="mt-1 truncate text-[10px] font-bold text-muted-foreground">
                {isMe ? "YOU" : seatNames[String(seat)] || (seatUser[String(seat)] ? "Player" : "BOT")}
              </div>
              <div className="flex items-center justify-center gap-1 text-[11px] font-bold tabular-nums">
                <HomeIcon className="size-3 text-muted-foreground" /> {home}/4
              </div>
            </div>
          );
        })}
      </div>

      {finished ? (
        /* end-of-game broadcast — winner, standings, rematch */
        <Card className="text-center lb-pop">
          <Trophy className="mx-auto size-8 text-primary" />
          <div className="mt-1 text-lg font-extrabold">{nameFor(state.ranking[0])} won!</div>
          <div className="mt-3 flex flex-col gap-1.5">
            {state.ranking.map((seat, i) => (
              <div
                key={seat}
                className="flex items-center gap-2.5 rounded-xl bg-secondary/60 px-3 py-1.5 text-sm"
              >
                <span className="w-4 text-center font-bold text-muted-foreground">{i + 1}</span>
                <span className="size-3 shrink-0 rounded-full" style={{ background: COLOR_HEX[state.players[seat].color] }} />
                <span className="flex-1 truncate text-left font-semibold">{nameFor(seat)}</span>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Button
              variant="win"
              disabled={iVoted}
              onClick={() => {
                haptic("medium");
                sock?.rematch();
              }}
            >
              {iVoted ? `Waiting ${rematch.votes.length}/${rematchNeeded}` : "Rematch"}
            </Button>
            <Button variant="secondary" onClick={onLeave}>
              Back to lobby
            </Button>
          </div>
          {rematch.votes.length > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              {rematch.votes.length}/{rematchNeeded} want a rematch
            </div>
          )}
        </Card>
      ) : (
        /* turn banner — fixed height so the board never shifts */
        <div
          className="rounded-2xl py-2.5 text-center text-sm font-bold ring-1 ring-white/10"
          style={{ background: `${COLOR_HEX[currentColor]}22` }}
        >
          {noMoves
            ? `No moves for ${currentColor} — passing…`
            : myTurn
              ? state.phase === "roll"
                ? "Your turn — roll the die!"
                : "Your turn — tap a glowing token"
              : `${currentColor}'s turn…`}
        </div>
      )}

      <Card className="p-2">
        <Board
          state={state}
          legal={legal}
          mySeat={mySeat}
          myColor={mySeat !== null ? state.players[mySeat]?.color ?? null : null}
          onMove={(ti) => {
            haptic("light");
            sock?.move(ti);
          }}
        />
      </Card>

      {/* dice + roll (the button's gold fill drains as your turn clock runs out) */}
      {!finished && (
        <div className="flex items-center justify-between gap-3">
          <RollingDie die={state.die} turn={state.turn} />
          <RollButton
            active={myTurn && state.phase === "roll"}
            clock={clock}
            onRoll={() => {
              haptic("medium");
              sock?.roll();
            }}
          />
        </div>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- admin */

function AdminPanel({ onBack }: { onBack: () => void }) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.adminStats().then(setStats).catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => {
    const id = setTimeout(
      () => api.adminUsers(q || undefined).then(setUsers).catch((e) => setErr(String(e))),
      q ? 300 : 0
    );
    return () => clearTimeout(id);
  }, [q]);

  const ago = (iso: string | null) => {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  };

  const tiles: [string, string | number][] = stats
    ? [
        ["Players", stats.users],
        ["Started bot", stats.users_started],
        ["Games played", stats.games_played],
        ["Coins out", stats.coins_in_circulation.toLocaleString()],
        ["Playing now", stats.matches_playing],
        ["Open rooms", stats.matches_waiting],
        ["Finished", stats.matches_finished],
        ["Abandoned", stats.matches_abandoned],
      ]
    : [];

  return (
    <Shell>
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <ArrowLeft className="size-4" /> Back
        </button>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-3 py-1.5 text-xs font-bold ring-1 ring-white/10">
          <Shield className="size-3.5 text-primary" /> ADMIN
        </span>
      </div>

      {err && (
        <div className="rounded-xl bg-red/10 px-3 py-2 text-center text-xs text-red">{err}</div>
      )}

      <Card>
        <SectionLabel>Overview</SectionLabel>
        <div className="mt-2.5 grid grid-cols-2 gap-2">
          {tiles.map(([label, value]) => (
            <div key={label} className="rounded-xl bg-secondary/60 px-3 py-2">
              <div className="text-lg font-extrabold tabular-nums">{value}</div>
              <div className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                {label}
              </div>
            </div>
          ))}
          {!stats && <div className="text-xs text-muted-foreground">Loading…</div>}
        </div>
      </Card>

      <Card>
        <div className="flex items-baseline justify-between">
          <SectionLabel>Players</SectionLabel>
          <span className="text-[10px] text-muted-foreground">{users.length} shown</span>
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or username…"
          className="mt-2 h-10 w-full rounded-xl bg-secondary px-3 text-sm outline-none ring-1 ring-white/10"
        />
        <div className="no-scrollbar mt-2 flex max-h-[50vh] flex-col gap-1.5 overflow-y-auto">
          {users.length === 0 && (
            <div className="py-3 text-center text-xs text-muted-foreground">No players found.</div>
          )}
          {users.map((u) => (
            <div key={u.id} className="flex items-center gap-2.5 rounded-xl bg-secondary/50 px-2.5 py-2">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-secondary text-xs font-bold ring-1 ring-white/10">
                {(u.first_name || "P").slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold">{u.first_name || "Player"}</span>
                  {u.is_banned && (
                    <span className="rounded-full bg-red/20 px-1.5 text-[9px] font-bold text-red">
                      BANNED
                    </span>
                  )}
                  {!u.bot_started && (
                    <span className="rounded-full bg-white/10 px-1.5 text-[9px] font-bold text-muted-foreground">
                      NO /START
                    </span>
                  )}
                </div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {u.username ? `@${u.username} · ` : ""}lvl {u.level} · {u.games_won}/
                  {u.games_played} won · seen {ago(u.last_seen_at)}
                </div>
              </div>
              <span className="shrink-0 text-xs font-bold tabular-nums text-primary">
                {u.coins.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      </Card>
    </Shell>
  );
}

/* ------------------------------------------------------------------- nav */

const TABS: { view: Tab; label: string; icon: React.ElementType }[] = [
  { view: "shop", label: "Shop", icon: ShoppingBag },
  { view: "friends", label: "Friends", icon: Users },
  { view: "home", label: "Play", icon: Gamepad2 },
  { view: "ranks", label: "Ranks", icon: Trophy },
  { view: "me", label: "Me", icon: User },
];

// Play sits dead centre — the thumb's home position and the thing you open the app for.
function BottomNav({ view, onChange }: { view: Tab; onChange: (v: Tab) => void }) {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 mx-auto flex w-full max-w-md border-t border-white/10 bg-background/95 backdrop-blur"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      {TABS.map((t) => {
        const active = view === t.view;
        const Icon = t.icon;
        const primary = t.view === "home";
        return (
          <button
            key={t.view}
            aria-label={t.label}
            onClick={() => {
              haptic("light");
              onChange(t.view);
            }}
            className={cn(
              "relative flex flex-1 flex-col items-center py-4 transition-colors",
              active ? "text-primary" : "text-muted-foreground"
            )}
          >
            {primary ? (
              <>
                {/* raised into a notch: the ring is painted in the page background so it
                    punches a clean curve through the nav's top border */}
                <span className="absolute -top-4 left-1/2 grid size-14 -translate-x-1/2 place-items-center rounded-full bg-gradient-to-br from-secondary to-card ring-4 ring-background transition-transform active:scale-95">
                  <Icon className="size-7" />
                </span>
                <span className="size-6" aria-hidden />
              </>
            ) : (
              <Icon className="size-6" />
            )}
          </button>
        );
      })}
    </nav>
  );
}

function ComingSoon({ title }: { title: string }) {
  return (
    <Shell className="pb-28">
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
        <Sparkles className="size-10 text-primary" />
        <div className="text-lg font-extrabold">{title}</div>
        <p className="max-w-[240px] text-sm text-muted-foreground">
          Coming in a future update.
        </p>
      </div>
    </Shell>
  );
}

function MeScreen({ profile, onAdmin }: { profile: Profile; onAdmin: () => void }) {
  const initial = (profile.first_name || "P").slice(0, 1).toUpperCase();
  const winRate = profile.games_played
    ? Math.round((profile.games_won / profile.games_played) * 100)
    : 0;
  const stats: [string, string | number][] = [
    ["Games", profile.games_played],
    ["Wins", profile.games_won],
    ["Win rate", `${winRate}%`],
  ];
  return (
    <Shell className="pb-28">
      <Card className="text-center">
        <div className="mx-auto grid size-16 place-items-center rounded-2xl bg-gradient-to-br from-secondary to-card text-2xl font-bold ring-1 ring-white/10">
          {initial}
        </div>
        <div className="mt-2 text-lg font-extrabold">{profile.first_name || "Player"}</div>
        {profile.username && (
          <div className="text-xs text-muted-foreground">@{profile.username}</div>
        )}
        <div className="mt-3 flex items-center justify-center gap-2">
          <span className="rounded-full bg-secondary px-3 py-1 text-[11px] font-bold text-muted-foreground ring-1 ring-white/10">
            LVL {profile.level}
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-3 py-1 text-[11px] font-bold ring-1 ring-white/10">
            <Coins className="size-3.5 text-primary" />
            <span className="tabular-nums">{profile.coins.toLocaleString()}</span>
          </span>
        </div>
      </Card>

      <div className="grid grid-cols-3 gap-2">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-2xl bg-card px-3 py-3 text-center ring-1 ring-white/10">
            <div className="text-lg font-extrabold tabular-nums">{value}</div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
              {label}
            </div>
          </div>
        ))}
      </div>

      {profile.is_admin && (
        <Button variant="secondary" className="w-full" onClick={onAdmin}>
          <Shield className="size-4" /> Admin panel
        </Button>
      )}
    </Shell>
  );
}
