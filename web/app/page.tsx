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
  Send,
  Pencil,
  Check,
  Reply,
  Database,
  X,
  Layers,
  RotateCw,
  Star,
  ShieldCheck,
  Dices,
  Snowflake,
  Lock,
  ArrowLeftRight,
  Undo2,
  Zap,
  Rocket,
  Crown,
  HeartPulse,
  Ban,
  Target,
  Layers3,
  ChevronDown,
  BarChart2,
  type LucideIcon,
} from "lucide-react";
import {
  api,
  authenticate,
  AdminStats,
  AdminTable,
  AdminRows,
  AdminUser,
  AdminReaction,
  AdminChatView,
  AdminChatSeat,
  AdminKnockRow,
  KnockEvent,
  ChatMessage,
  REACTIONS,
  DiceState,
  MatchSummary,
  PlayerStats,
  Poll,
  PollTemplate,
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
import { GameState, LegalMove, MatchSocket, StatePayload, CardDraw } from "@/lib/ws";
import { Card as CardDef, rarityColor } from "@/lib/cards";
import { Button } from "@/components/ui/button";
import { Card, SectionLabel } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import Board from "@/components/board";
import { DICE_SKINS, PIPS, skinOf } from "@/lib/skins";

const COLOR_LIST = ["#e5484d", "#30a46c", "#f2b705", "#3e63dd", "#e5709b", "#7b61ff", "#12a4c9"];

const COLOR_HEX: Record<string, string> = {
  RED: "#e5484d",
  GREEN: "#30a46c",
  YELLOW: "#f2b705",
  BLUE: "#3e63dd",
};

type Room = { code: string; host: boolean };
type Tab = "shop" | "friends" | "home" | "cards" | "me";
type Clock = { deadline: number | null; now: number; recvAt: number; turnSeconds: number };
// this game's per-seat scoreboard, streamed on every state update
type GameStats = {
  rolls: Record<string, Record<string, number>>; // seat -> {face: count}
  dealt: Record<string, number>; // seat -> captures dealt
  taken: Record<string, number>; // seat -> captures suffered
  potential: Record<string, number>; // seat -> captures passed up
};

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
  const [seatLevels, setSeatLevels] = useState<Record<string, number>>({});
  const [seatSkins, setSeatSkins] = useState<Record<string, string>>({});
  const [seatDice, setSeatDice] = useState<Record<string, number>>({});
  const [gameStats, setGameStats] = useState<GameStats>({ rolls: {}, dealt: {}, taken: {}, potential: {} });
  const [cardDraw, setCardDraw] = useState<CardDraw | null>(null);
  const [removedSeats, setRemovedSeats] = useState<number[]>([]);
  const [clock, setClock] = useState<Clock | null>(null);
  const [rematch, setRematch] = useState<{ votes: number[]; humanIds: number[] }>({
    votes: [],
    humanIds: [],
  });
  const sockRef = useRef<MatchSocket | null>(null);
  const myIdRef = useRef<number | null>(null);
  const leaveMatchRef = useRef<(() => void) | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
        setSeatLevels(p.seat_levels ?? {});
        setSeatSkins(p.seat_skins ?? {});
        setSeatDice(p.seat_last_die ?? {});
        setGameStats({
          rolls: p.seat_rolls ?? {},
          dealt: p.seat_dealt ?? {},
          taken: p.seat_taken ?? {},
          potential: p.seat_potential ?? {},
        });
        setCardDraw(p.card ?? null);
        setRemovedSeats(p.removed_seats ?? []);
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
      } else if (msg.type === "kicked") {
        if (msg.user_id === myIdRef.current) {
          notify("warning");
          setNotice("You were removed for missing your turns.");
          leaveMatchRef.current?.();
        }
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
  leaveMatchRef.current = leaveMatch;

  // ---- auth + deep-link ----
  useEffect(() => {
    initTelegram();
    authenticate()
      .then((p) => {
        setProfile(p);
        myIdRef.current = p.id;
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

  const noticeBanner = notice ? <NoticeBanner text={notice} onClose={() => setNotice(null)} /> : null;

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
        seatLevels={seatLevels}
        seatSkins={seatSkins}
        seatDice={seatDice}
        gameStats={gameStats}
        card={cardDraw}
        removedSeats={removedSeats}
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
      {view === "me" && (
        <MeScreen profile={profile} onAdmin={() => setShowAdmin(true)} onProfile={setProfile} />
      )}
      {view === "shop" && <ComingSoon title="Shop" />}
      {view === "friends" && <ComingSoon title="Friends" />}
      {view === "cards" && <CardsGallery />}
      <BottomNav view={view} onChange={setView} />
      {noticeBanner}
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

function NoticeBanner({ text, onClose }: { text: string; onClose: () => void }) {
  useEffect(() => {
    const id = setTimeout(onClose, 4500);
    return () => clearTimeout(id);
  }, [onClose]);
  return (
    <div className="fixed inset-x-0 top-0 z-50 flex justify-center px-4 pt-3">
      <div className="lb-pop flex max-w-md items-center gap-2 rounded-2xl bg-red/90 px-4 py-2.5 text-sm font-semibold text-white shadow-lg ring-1 ring-white/20 backdrop-blur">
        <AlertTriangle className="size-4 shrink-0" />
        <span>{text}</span>
      </div>
    </div>
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


function MatchChat({
  code,
  profile,
  colors,
  canKnock,
}: {
  code: string;
  profile: Profile;
  colors: Record<number, string>;
  canKnock: boolean;
}) {
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [menuId, setMenuId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [reactionSet, setReactionSet] = useState<string[]>([...REACTIONS]);
  const [pollTemplates, setPollTemplates] = useState<PollTemplate[]>([]);
  const [pollMenu, setPollMenu] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => api.getChat(code).then((c) => alive && setChat(c)).catch(() => {});
    load();
    api
      .getReactions()
      .then((r) => alive && r.length && setReactionSet(r))
      .catch(() => {});
    api
      .getPollTemplates()
      .then((t) => alive && setPollTemplates(t))
      .catch(() => {});
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [code]);
  // templates the player may post right now (a "knock" poll only when a capture is live)
  const applicablePolls = pollTemplates.filter(
    (t) => t.trigger !== "knock" || canKnock
  );
  const sendPoll = async (templateId: number) => {
    setPollMenu(false);
    try {
      haptic("light");
      setChat(await api.createPoll(code, templateId));
    } catch {
      /* ignore (e.g. no longer a knock situation) */
    }
  };
  const vote = async (pollId: number, optionId: number) => {
    haptic("light");
    try {
      setChat(await api.votePoll(code, pollId, optionId));
    } catch {
      /* ignore */
    }
  };
  const send = async () => {
    const t = draft.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      haptic("light");
      if (editingId != null) {
        setChat(await api.editChat(code, editingId, t));
        setEditingId(null);
      } else {
        setChat(await api.sendChat(code, t, replyTo?.id ?? null));
        setReplyTo(null);
      }
      setDraft("");
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };
  const del = async (id: number) => {
    setMenuId(null);
    try {
      setChat(await api.deleteChat(code, id));
    } catch {
      /* ignore */
    }
  };
  const react = async (id: number, emoji: string) => {
    haptic("light");
    try {
      setChat(await api.reactChat(code, id, emoji));
    } catch {
      /* ignore */
    }
  };
  const tint = (id: number) =>
    colors[id] ?? COLOR_LIST[((id % COLOR_LIST.length) + COLOR_LIST.length) % COLOR_LIST.length];
  const shadow = "0 1px 3px rgba(0,0,0,0.7)";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="no-scrollbar flex min-h-0 flex-1 flex-col-reverse gap-1.5 overflow-y-auto pb-2"
        style={{
          maskImage: "linear-gradient(to top, #000 78%, transparent)",
          WebkitMaskImage: "linear-gradient(to top, #000 78%, transparent)",
        }}
      >
        {[...chat].reverse().map((m) => {
          const mine = m.user_id === profile.id;
          const open = menuId === m.id;
          if (m.poll) {
            return (
              <PollBubble
                key={m.id}
                poll={m.poll}
                askerName={m.name}
                askerColor={tint(m.user_id)}
                onVote={(oid) => vote(m.poll!.id, oid)}
              />
            );
          }
          return (
            <div key={m.id} className={cn("flex", mine ? "justify-end pl-8" : "justify-start pr-8")}>
              <div
                className={cn(
                  "flex max-w-[88%] items-start gap-2",
                  mine && "flex-row-reverse text-right"
                )}
                onClick={() => setMenuId(open ? null : m.id)}
                style={{ cursor: "pointer" }}
              >
                <span
                  className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white ring-1 ring-white/20"
                  style={{ background: tint(m.user_id) }}
                >
                  {(m.name || "P").slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0 leading-snug" style={{ textShadow: shadow }}>
                  {/* quoted reply */}
                  {m.reply_to != null && m.reply_text != null && (
                    <div
                      className={cn(
                        "mb-0.5 rounded-md border-l-2 bg-white/10 px-1.5 py-0.5 text-[11px] text-white/70",
                        mine ? "border-white/40" : ""
                      )}
                      style={mine ? undefined : { borderColor: tint(m.user_id) }}
                    >
                      <span className="font-bold">{m.reply_name}</span>{" "}
                      <span className="opacity-80">{m.reply_text}</span>
                    </div>
                  )}
                  {!mine && (
                    <>
                      <span className="text-[13px] font-bold" style={{ color: tint(m.user_id) }}>
                        {m.name}
                      </span>{" "}
                    </>
                  )}
                  <span className="text-[13px] text-white break-words">{m.text}</span>
                  {m.edited && <span className="text-[10px] text-white/50"> (edited)</span>}
                  {/* reaction pills (always visible when present) */}
                  {Object.keys(m.reactions).length > 0 && (
                    <div className={cn("mt-1 flex flex-wrap gap-1", mine && "justify-end")}>
                      {Object.entries(m.reactions).map(([emoji, count]) => (
                        <button
                          key={emoji}
                          onClick={(e) => {
                            e.stopPropagation();
                            react(m.id, emoji);
                          }}
                          className={cn(
                            "flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] ring-1 active:scale-90",
                            m.my_reaction === emoji
                              ? "bg-primary/25 ring-primary/50"
                              : "bg-white/10 ring-white/15"
                          )}
                          style={{ textShadow: "none" }}
                        >
                          <span className="leading-none">{emoji}</span>
                          <span className="tabular-nums text-white/85">{count}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {open && (
                    <span className="ml-2 inline-flex flex-wrap items-center gap-1 align-middle">
                      {reactionSet.map((emoji) => (
                        <button
                          key={emoji}
                          onClick={(e) => {
                            e.stopPropagation();
                            react(m.id, emoji);
                            setMenuId(null);
                          }}
                          className={cn(
                            "grid size-6 place-items-center rounded-full text-sm leading-none active:scale-90",
                            m.my_reaction === emoji ? "bg-primary/30" : "bg-white/15"
                          )}
                          style={{ textShadow: "none" }}
                          aria-label={`React ${emoji}`}
                        >
                          {emoji}
                        </button>
                      ))}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setReplyTo(m);
                          setEditingId(null);
                          setMenuId(null);
                        }}
                        className="grid size-6 place-items-center rounded-full bg-white/15 text-white active:scale-90"
                        aria-label="Reply"
                      >
                        <Reply className="size-3" />
                      </button>
                      {mine && (
                        <>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditingId(m.id);
                              setReplyTo(null);
                              setDraft(m.text);
                              setMenuId(null);
                            }}
                            className="grid size-6 place-items-center rounded-full bg-white/15 text-white active:scale-90"
                            aria-label="Edit"
                          >
                            <Pencil className="size-3" />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              del(m.id);
                            }}
                            className="grid size-6 place-items-center rounded-full bg-red/80 text-white active:scale-90"
                            aria-label="Delete"
                          >
                            <Trash2 className="size-3" />
                          </button>
                        </>
                      )}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {chat.length === 0 && (
          <div className="text-xs text-muted-foreground">Say something to the table…</div>
        )}
      </div>

      {(replyTo || editingId != null) && (
        <div className="mb-1 flex items-center justify-between gap-2 rounded-lg bg-white/8 px-2.5 py-1.5 text-[11px]">
          <div className="min-w-0 flex-1 truncate text-muted-foreground">
            {editingId != null ? (
              <span>Editing your message…</span>
            ) : (
              <span>
                Replying to <span className="font-bold text-foreground">{replyTo?.name}</span>:{" "}
                {replyTo?.text}
              </span>
            )}
          </div>
          <button
            onClick={() => {
              setEditingId(null);
              setReplyTo(null);
              setDraft("");
            }}
            className="shrink-0 font-bold text-primary"
          >
            Cancel
          </button>
        </div>
      )}

      {/* poll picker — the applicable instant polls (e.g. "Should I knock it?") */}
      {pollMenu && applicablePolls.length > 0 && (
        <div className="mb-1 flex flex-col gap-1 rounded-xl bg-card p-1.5 ring-1 ring-white/10">
          {applicablePolls.map((t) => (
            <button
              key={t.id}
              onClick={() => sendPoll(t.id)}
              className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-semibold text-white active:bg-white/5"
            >
              <BarChart2 className="size-4 text-primary" />
              {t.question}
            </button>
          ))}
        </div>
      )}

      <div className="flex shrink-0 items-center gap-2 rounded-full bg-white/8 p-1 pl-2 ring-1 ring-white/10 backdrop-blur">
        {applicablePolls.length > 0 && (
          <button
            onClick={() => {
              haptic("light");
              setPollMenu((v) => !v);
            }}
            className={cn(
              "grid size-8 shrink-0 place-items-center rounded-full transition active:scale-90",
              pollMenu ? "bg-primary text-primary-foreground" : "bg-white/10 text-primary"
            )}
            aria-label="Poll"
          >
            <BarChart2 className="size-4" />
          </button>
        )}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={editingId != null ? "Edit message…" : replyTo ? "Reply…" : "Message…"}
          maxLength={200}
          className="h-8 flex-1 bg-transparent pl-1 text-sm outline-none placeholder:text-muted-foreground"
        />
        <button
          onClick={send}
          disabled={busy || !draft.trim()}
          className="grid size-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition active:scale-90 disabled:opacity-40"
          aria-label={editingId != null ? "Save" : "Send"}
        >
          {editingId != null ? <Check className="size-4" /> : <Send className="size-4" />}
        </button>
      </div>
    </div>
  );
}

/* -------------------------------------------------- in-chat poll bubble */

function PollBubble({
  poll,
  askerName,
  askerColor,
  onVote,
}: {
  poll: Poll;
  askerName: string;
  askerColor: string;
  onVote: (optionId: number) => void;
}) {
  const total = poll.total_votes;
  return (
    <div className="flex justify-center px-2">
      <div className="w-full max-w-[92%] rounded-2xl bg-card/90 p-3 ring-1 ring-white/10 backdrop-blur">
        <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <BarChart2 className="size-3.5 text-primary" />
          <span className="font-bold" style={{ color: askerColor }}>{askerName}</span>
          <span>asks</span>
        </div>
        <div className="mb-2.5 text-sm font-extrabold text-white">{poll.question}</div>
        <div className="flex flex-col gap-1.5">
          {poll.options.map((o) => {
            const pct = total ? Math.round((o.votes / total) * 100) : 0;
            const picked = poll.my_vote === o.id;
            return (
              <button
                key={o.id}
                onClick={() => onVote(o.id)}
                className={cn(
                  "relative overflow-hidden rounded-xl px-3 py-2 text-left ring-1 transition active:scale-[0.99]",
                  picked ? "ring-primary" : "ring-white/10"
                )}
              >
                <div
                  className="absolute inset-y-0 left-0 rounded-xl bg-primary/20 transition-all"
                  style={{ width: `${pct}%` }}
                />
                <div className="relative flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5 font-semibold text-white">
                    {picked && <Check className="size-3.5 text-primary" />}
                    {o.text}
                  </span>
                  <span className="tabular-nums text-xs text-muted-foreground">
                    {o.votes}
                    {total > 0 && ` · ${pct}%`}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
        <div className="mt-2 text-[11px] text-muted-foreground">
          {total === 0 ? "No votes yet — tap to vote" : `${total} vote${total === 1 ? "" : "s"}`}
        </div>
      </div>
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
      className="relative h-14 w-full shrink-0 overflow-hidden rounded-2xl bg-secondary ring-1 ring-white/10 transition active:translate-y-px disabled:opacity-55"
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
  seatLevels,
  seatSkins,
  seatDice,
  gameStats,
  card,
  removedSeats,
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
  seatLevels: Record<string, number>;
  seatSkins: Record<string, string>;
  seatDice: Record<string, number>;
  gameStats: GameStats;
  card: CardDraw | null;
  removedSeats: number[];
  clock: Clock | null;
  rematch: { votes: number[]; humanIds: number[] };
  sock: MatchSocket | null;
  onLeave: () => void;
}) {
  const [profileId, setProfileId] = useState<number | null>(null);
  const [showBoard, setShowBoard] = useState(false); // in-game scoreboard sheet
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
    <main className="mx-auto flex h-dvh w-full max-w-md flex-col gap-3 overflow-hidden px-4 pb-3 pt-4">
      <div className="flex shrink-0 items-center justify-between">
        <span className="rounded-full bg-secondary px-3 py-1.5 text-xs font-bold tracking-wider ring-1 ring-white/10">
          ROOM {code}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              haptic("light");
              setShowBoard(true);
            }}
          >
            <Trophy className="size-4" /> Scores
          </Button>
          <Button variant="ghost" size="sm" onClick={onLeave}>
            <ArrowLeft className="size-4" /> Leave
          </Button>
        </div>
      </div>

      {finished && (
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
      )}

      {/* The board carries everything now: each player's name, level, die and the
          draining turn ring — so there's no player strip or turn banner above it. */}
      <Card className="shrink-0 p-2">
        <Board
          state={state}
          legal={legal}
          mySeat={mySeat}
          myColor={mySeat !== null ? state.players[mySeat]?.color ?? null : null}
          seatNames={seatNames}
          seatLevels={seatLevels}
          seatSkins={seatSkins}
          seatDice={seatDice}
          seatUser={seatUser}
          removedSeats={removedSeats}
          clock={clock}
          onMove={(ti) => {
            haptic("light");
            sock?.move(ti);
          }}
          onPlayerTap={(uid) => {
            haptic("light");
            setProfileId(uid);
          }}
        />
      </Card>

      {!finished && (
        <>
          <div className="shrink-0">
            <RollButton
              active={myTurn && state.phase === "roll"}
              clock={clock}
              onRoll={() => {
                haptic("medium");
                sock?.roll();
              }}
            />
            <p className="mt-1 text-center text-[11px] text-muted-foreground">
              {noMoves
                ? `No moves for ${currentColor} — passing…`
                : myTurn
                  ? state.phase === "roll"
                    ? "Your turn"
                    : "Tap a glowing token"
                  : `${currentColor}'s turn…`}
            </p>
          </div>
          {/* chat fills the rest; its input is pinned, its feed scrolls internally */}
          <MatchChat
            code={code}
            profile={profile}
            canKnock={myTurn && state.phase === "move" && legal.some((m) => m.captures.length > 0)}
            colors={Object.fromEntries(
              Object.entries(seatUser)
                .filter(([, uid]) => uid != null)
                .map(([seat, uid]) => [
                  uid as number,
                  COLOR_HEX[state.players[Number(seat)]?.color] ?? "#eef1f6",
                ])
            )}
          />
        </>
      )}
      {card && (
        <ChanceBox
          card={card}
          mine={card.seat === mySeat}
          drawerName={seatNames[String(card.seat)] || "A player"}
          seatNames={seatNames}
          seatColor={Object.fromEntries(
            state.players.map((p, s) => [String(s), COLOR_HEX[p.color] ?? "#8892a6"])
          )}
          onPick={(i) => {
            haptic("medium");
            sock?.pickCard(i);
          }}
          onTarget={(s) => {
            haptic("medium");
            sock?.pickTarget(s);
          }}
        />
      )}
      {profileId != null && (
        <ProfileSheet userId={profileId} onClose={() => setProfileId(null)} />
      )}
      {showBoard && (
        <GameScoreboard
          code={code}
          state={state}
          seatNames={seatNames}
          seatUser={seatUser}
          removedSeats={removedSeats}
          stats={gameStats}
          mySeat={mySeat}
          onClose={() => setShowBoard(false)}
        />
      )}
    </main>
  );
}

/* --------------------------------------------------- fantasy card draw */

// each card renders a crisp SVG icon (no emoji) chosen to match its effect
const CARD_ICON: Record<string, LucideIcon> = {
  extra_roll: RotateCw,
  active_stars: Star,
  shield_one: Shield,
  shield_all: ShieldCheck,
  double_dice: Dices,
  lock_one: Snowflake,
  lock_two: Lock,
  swap: ArrowLeftRight,
  teleport: Sparkles,
  recall: Undo2,
  boost: Zap,
  summon: Rocket,
  steal_turn: Crown,
  second_chance: HeartPulse,
  toll: Ban,
  mirror: Copy,
  jackpot: Coins,
};

function CardIcon({ id, size = 20, color }: { id: string; size?: number; color?: string }) {
  const Icon = CARD_ICON[id] ?? Layers;
  return <Icon size={size} color={color} strokeWidth={2.2} />;
}

function CardFace({ c, big, highlight }: { c?: CardDef; big?: boolean; highlight?: boolean }) {
  if (!c) return null;
  const rc = rarityColor(c.rarity);
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-1.5 rounded-2xl border-2 bg-card p-2 text-center"
      style={{ borderColor: rc, boxShadow: highlight ? `0 0 22px ${rc}` : undefined }}
    >
      <div
        className="grid place-items-center rounded-full"
        style={{ width: big ? 54 : 42, height: big ? 54 : 42, background: rc + "22" }}
      >
        <CardIcon id={c.id} size={big ? 28 : 22} color={rc} />
      </div>
      <div className={cn("font-extrabold leading-tight", big ? "text-base" : "text-[13px]")}>{c.name}</div>
      <div className="text-[8px] font-bold uppercase tracking-wide" style={{ color: rc }}>{c.rarity}</div>
      <div className={cn("leading-tight text-muted-foreground", big ? "text-[11px]" : "text-[9px]")}>{c.description}</div>
    </div>
  );
}

function ChanceBox({
  card,
  mine,
  drawerName,
  seatNames,
  seatColor,
  onPick,
  onTarget,
}: {
  card: CardDraw;
  mine: boolean;
  drawerName: string;
  seatNames: Record<string, string>;
  seatColor: Record<string, string>;
  onPick: (index: number) => void;
  onTarget: (seat: number) => void;
}) {
  const [catalog, setCatalog] = useState<Record<string, CardDef>>({});
  const [tapped, setTapped] = useState<number | null>(null);
  const [targeted, setTargeted] = useState<number | null>(null);
  useEffect(() => {
    api
      .getCards()
      .then((cs) => setCatalog(Object.fromEntries(cs.map((c) => [c.id, c]))))
      .catch(() => {});
  }, []);

  const stage = card.stage;
  const options = card.options ?? [];
  const pickedIdx = card.picked ?? -1;
  const pickedCard = options[pickedIdx] ? catalog[options[pickedIdx]] : undefined;
  const nameOf = (s: number) => seatNames[String(s)] || "Player";

  const tapCard = (i: number) => {
    if (!mine || stage !== "pick" || tapped !== null) return;
    setTapped(i);
    onPick(i);
  };
  const tapTarget = (s: number) => {
    if (!mine || stage !== "target" || targeted !== null) return;
    setTargeted(s);
    onTarget(s);
  };

  let title = "";
  if (stage === "pick") title = mine ? "Your prize" : `${drawerName} is drawing`;
  else if (stage === "reveal") title = mine ? "You drew" : `${drawerName} drew`;
  else if (stage === "target") title = mine ? "Choose a target" : `${drawerName} is aiming`;
  else title = mine ? "You played" : `${drawerName} played`;

  // Result: a slim, non-blocking banner so the BOARD stays visible and plays the effect
  // out (a Recall token gliding back, a Swap, a shield appearing) while it's announced.
  if (stage === "result") {
    const rc = pickedCard ? rarityColor(pickedCard.rarity) : "#7c8698";
    return (
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[70] flex justify-center px-4">
        <div className="lb-pop flex max-w-sm items-center gap-3 rounded-2xl bg-card/95 px-4 py-2.5 shadow-xl ring-1 ring-white/10 backdrop-blur">
          <div className="grid size-9 shrink-0 place-items-center rounded-xl" style={{ background: rc + "22" }}>
            {pickedCard && <CardIcon id={pickedCard.id} size={20} color={rc} />}
          </div>
          <div className="min-w-0">
            <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{title}</div>
            <div className="flex items-center gap-1.5 text-sm font-extrabold">
              <span className="truncate">{pickedCard?.name ?? "…"}</span>
              {card.target != null && (
                <>
                  <span className="text-muted-foreground">→</span>
                  <span className="size-2.5 shrink-0 rounded-full" style={{ background: seatColor[String(card.target)] ?? "#8892a6" }} />
                  <span className="truncate">{nameOf(card.target)}</span>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[70] flex flex-col items-center justify-center bg-black/75 px-6 backdrop-blur-md">
      <div className="mb-1 text-center text-xs font-bold uppercase tracking-widest text-primary">{title}</div>
      <div className="mb-5 text-center text-2xl font-extrabold text-white">
        {stage === "pick"
          ? mine
            ? "Pick a card"
            : "Drawing a card…"
          : pickedCard?.name ?? "…"}
      </div>

      {/* pick / reveal: the four-card fan (flips on reveal) */}
      {(stage === "pick" || stage === "reveal") && (
        <div className="grid w-full max-w-xs grid-cols-2 gap-3">
          {[0, 1, 2, 3].map((i) => {
            const c = stage === "reveal" ? catalog[options[i]] : undefined;
            const isPicked = stage === "reveal" && i === pickedIdx;
            const flipped = stage === "reveal";
            return (
              <button
                key={i}
                onClick={() => tapCard(i)}
                disabled={!mine || stage !== "pick"}
                className={cn(
                  "transition-transform",
                  stage === "pick" && mine && tapped === null && "active:scale-95",
                  flipped && !isPicked && "opacity-40",
                  tapped === i && stage === "pick" && "scale-105"
                )}
                style={{ perspective: 800 }}
              >
                <div
                  style={{
                    position: "relative",
                    aspectRatio: "3 / 4",
                    transformStyle: "preserve-3d",
                    transition: "transform 0.5s",
                    transform: flipped ? "rotateY(180deg)" : "none",
                  }}
                >
                  <div
                    style={{ position: "absolute", inset: 0, backfaceVisibility: "hidden" }}
                    className="grid place-items-center rounded-2xl border-2 border-white/15 bg-gradient-to-br from-primary/40 to-secondary shadow-lg"
                  >
                    <Sparkles className="size-8 text-white/70" />
                  </div>
                  <div
                    style={{ position: "absolute", inset: 0, backfaceVisibility: "hidden", transform: "rotateY(180deg)" }}
                  >
                    <CardFace c={c} highlight={isPicked} />
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* target: the picked card + the opponent picker */}
      {stage === "target" && (
        <div className="flex w-full max-w-xs flex-col items-center gap-4">
          <div className="w-32" style={{ aspectRatio: "3 / 4" }}>
            <CardFace c={pickedCard} big highlight />
          </div>
          <div className="w-full">
            <p className="mb-2 text-center text-xs text-muted-foreground">
              {mine ? "Tap a player to hit" : "Choosing who to hit…"}
            </p>
            <div className="flex flex-col gap-2">
              {(card.targets ?? []).map((s) => (
                <button
                  key={s}
                  onClick={() => tapTarget(s)}
                  disabled={!mine || targeted !== null}
                  className={cn(
                    "flex items-center gap-3 rounded-2xl bg-card px-4 py-3 ring-1 transition active:scale-95",
                    targeted === s ? "ring-primary" : "ring-white/10"
                  )}
                >
                  <span className="size-4 shrink-0 rounded-full" style={{ background: seatColor[String(s)] ?? "#8892a6" }} />
                  <span className="flex-1 text-left font-bold text-white">{nameOf(s)}</span>
                  <Target className="size-4 text-muted-foreground" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ----------------------------------------------------- cards gallery */

function CardsGallery() {
  const [cards, setCards] = useState<CardDef[] | null>(null);
  const [err, setErr] = useState(false);
  const [selected, setSelected] = useState<CardDef | null>(null);
  useEffect(() => {
    api.getCards().then(setCards).catch(() => setErr(true));
  }, []);

  return (
    <Shell className="pb-28">
      <div className="flex items-center gap-2 pt-1">
        <Layers3 className="size-6 text-primary" />
        <h1 className="text-xl font-extrabold">Fantasy Cards</h1>
      </div>
      <p className="-mt-2 text-xs text-muted-foreground">
        Bring a piece home to draw one of four. Tap a card to see it in action.
      </p>

      {err && <Card className="text-center text-sm text-muted-foreground">Couldn&apos;t load cards.</Card>}
      {!cards && !err && <Card className="text-center text-sm text-muted-foreground">Loading…</Card>}
      {cards && (
        <div className="flex flex-col gap-2">
          {cards.map((c) => {
            const rc = rarityColor(c.rarity);
            return (
              <button
                key={c.id}
                onClick={() => {
                  haptic("light");
                  setSelected(c);
                }}
                className="flex items-center gap-3 rounded-2xl bg-card p-3 text-left ring-1 ring-white/10 transition active:scale-[0.99]"
              >
                <div className="grid size-11 shrink-0 place-items-center rounded-xl" style={{ background: rc + "22" }}>
                  <CardIcon id={c.id} size={22} color={rc} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold">{c.name}</span>
                    <span
                      className="rounded-full px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                      style={{ color: rc, background: rc + "22" }}
                    >
                      {c.rarity}
                    </span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{c.description}</div>
                </div>
                <ChevronDown className="size-4 -rotate-90 text-muted-foreground" />
              </button>
            );
          })}
        </div>
      )}

      {selected && <CardDetail card={selected} onClose={() => setSelected(null)} />}
    </Shell>
  );
}

/* ------ card detail: a slide-in panel with an animated "how it plays" demo ------ */

const DEMO_YOU = "#4a90d9";
const DEMO_RIVAL = "#d76a6a";
const DEMO_CELLS = 8;
const demoLeft = (cell: number) => ((cell + 0.5) / DEMO_CELLS) * 100;

type DemoPiece = {
  key: string;
  kind: "you" | "rival";
  cell: number;
  shield?: boolean;
  frozen?: boolean;
  heart?: boolean;
  hidden?: boolean;
};
type DemoScene = {
  pieces: DemoPiece[];
  star?: number;
  starOn?: boolean;
  barrier?: number;
  die?: number;
  badge?: string;
  turn?: "you" | "rival";
  caption: string;
};

// idle (on=false) vs applied (on=true) — the demo animates between the two, forever
function demoScene(id: string, on: boolean): DemoScene {
  switch (id) {
    case "extra_roll":
      return { pieces: [{ key: "y", kind: "you", cell: on ? 4 : 2 }], die: on ? 6 : 4, caption: "Take another roll" };
    case "active_stars":
      return { pieces: [{ key: "y", kind: "you", cell: 4 }], star: 4, starOn: on, caption: "Your stars turn safe" };
    case "shield_one":
      return { pieces: [{ key: "y", kind: "you", cell: 5, shield: true }, { key: "r", kind: "rival", cell: on ? 5 : 3 }], caption: "A piece can't be captured" };
    case "shield_all":
      return { pieces: [{ key: "y", kind: "you", cell: 5, shield: true }, { key: "y2", kind: "you", cell: 6, shield: true }, { key: "r", kind: "rival", cell: on ? 5 : 3 }], caption: "All your pieces shielded" };
    case "double_dice":
      return { pieces: [{ key: "y", kind: "you", cell: on ? 7 : 1 }], die: 3, badge: on ? "×2" : undefined, caption: "Rolls count double" };
    case "lock_one":
      return { pieces: [{ key: "r", kind: "rival", cell: 4, frozen: on }], die: on ? undefined : 4, caption: "Rival skips a turn" };
    case "lock_two":
      return { pieces: [{ key: "r", kind: "rival", cell: 4, frozen: on }], caption: "Rival skips 2 turns" };
    case "swap":
      return { pieces: [{ key: "y", kind: "you", cell: on ? 6 : 2 }, { key: "r", kind: "rival", cell: on ? 2 : 6 }], caption: "Swap places with a rival" };
    case "teleport":
      return { pieces: [{ key: "y", kind: "you", cell: on ? 6 : 2 }], star: 6, starOn: true, caption: "Warp to the next star" };
    case "recall":
      return { pieces: [{ key: "r", kind: "rival", cell: on ? 2 : 6 }], caption: "Send a rival back 4" };
    case "boost":
      return { pieces: [{ key: "y", kind: "you", cell: on ? 5 : 2 }], caption: "Rush forward 3" };
    case "summon":
      return { pieces: [{ key: "y", kind: "you", cell: 0, hidden: !on }], caption: "Free a piece from base" };
    case "steal_turn":
      return { pieces: [{ key: "y", kind: "you", cell: 2 }, { key: "r", kind: "rival", cell: 5 }], turn: on ? "you" : "rival", caption: "Take the next turn" };
    case "second_chance":
      return { pieces: [{ key: "y", kind: "you", cell: 5, heart: on }, { key: "r", kind: "rival", cell: on ? 5 : 3 }], caption: "Survive one knock" };
    case "toll":
      return { pieces: [{ key: "r", kind: "rival", cell: 2 }], star: 4, starOn: true, barrier: on ? 4 : undefined, caption: "Rivals can't pass your star" };
    case "mirror":
      return { pieces: [{ key: "r", kind: "rival", cell: 5 }, { key: "y", kind: "you", cell: 2 }], badge: on ? "copy" : undefined, caption: "Replay a rival's card" };
    case "jackpot":
      return { pieces: [{ key: "y", kind: "you", cell: 3 }], badge: on ? "+150" : undefined, caption: "Pocket 150 coins" };
    default:
      return { pieces: [{ key: "y", kind: "you", cell: on ? 4 : 2 }], caption: "" };
  }
}

function CardDemo({ card }: { card: CardDef }) {
  const [applied, setApplied] = useState(false);
  const [animate, setAnimate] = useState(false);
  useEffect(() => {
    setApplied(false);
    setAnimate(false);
    let t1: ReturnType<typeof setTimeout>, t2: ReturnType<typeof setTimeout>, t3: ReturnType<typeof setTimeout>;
    const cycle = () => {
      t1 = setTimeout(() => {
        setAnimate(true);
        setApplied(true); // glide to the applied state
        t2 = setTimeout(() => {
          setAnimate(false);
          setApplied(false); // snap back invisibly, then loop
          t3 = setTimeout(cycle, 200);
        }, 2200);
      }, 600);
    };
    cycle();
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [card.id]);

  const s = demoScene(card.id, applied);
  const trans = animate
    ? "left 0.7s cubic-bezier(0.34,1.1,0.64,1), opacity 0.4s ease, transform 0.4s ease"
    : "none";

  return (
    <div>
      <div className="relative h-20 overflow-hidden rounded-2xl bg-secondary/40 ring-1 ring-white/10">
        {Array.from({ length: DEMO_CELLS - 1 }).map((_, i) => (
          <div key={i} className="absolute top-0 h-full w-px bg-white/[0.04]" style={{ left: `${((i + 1) / DEMO_CELLS) * 100}%` }} />
        ))}

        {s.star != null && (
          <div className="absolute top-1/2" style={{ left: `${demoLeft(s.star)}%`, transform: "translate(-50%,-50%)", transition: trans }}>
            <Star size={22} color={s.starOn ? "#e5c07b" : "#5b6478"} fill={s.starOn ? "#e5c07b" : "none"} strokeWidth={2} />
          </div>
        )}
        {s.barrier != null && (
          <div className="absolute top-1/2 h-10 w-1.5 rounded-full bg-[#e5c07b]" style={{ left: `${demoLeft(s.barrier)}%`, transform: "translate(-50%,-50%)" }} />
        )}

        {s.pieces.map((p) => (
          <div
            key={p.key}
            className="absolute top-1/2"
            style={{ left: `${demoLeft(p.cell)}%`, transform: "translate(-50%,-50%)", transition: trans, opacity: p.hidden ? 0 : 1 }}
          >
            <div className="relative grid size-8 place-items-center rounded-full ring-2 ring-white/70" style={{ background: p.kind === "you" ? DEMO_YOU : DEMO_RIVAL }}>
              {p.shield && <span className="absolute -inset-1.5 rounded-full border-2 border-dashed border-[#38bdf8]" />}
              {p.frozen && <Snowflake className="absolute -right-2 -top-2 size-4 text-[#93c5fd]" />}
              {p.heart && <HeartPulse className="absolute -right-2 -top-2 size-4 text-[#f472b6]" />}
            </div>
            {s.turn === p.kind && <ChevronDown className="absolute -top-4 left-1/2 size-4 -translate-x-1/2 text-primary" />}
          </div>
        ))}

        {s.die != null && (
          <div className="absolute right-2 top-2">
            <DieFace value={s.die} skin="classic" size={22} />
          </div>
        )}
        {s.badge && (
          <div className="absolute bottom-1.5 right-2 rounded-full bg-primary/20 px-2 py-0.5 text-xs font-extrabold text-primary">
            {s.badge}
          </div>
        )}
      </div>
      <p className="mt-2 text-center text-xs text-muted-foreground">{s.caption}</p>
    </div>
  );
}

function CardDetail({ card, onClose }: { card: CardDef; onClose: () => void }) {
  const rc = rarityColor(card.rarity);
  return (
    <div className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="lb-slide-in absolute inset-y-0 right-0 flex w-full max-w-md flex-col overflow-y-auto bg-card p-5 ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <button onClick={onClose} className="mb-4 flex items-center gap-1.5 self-start text-sm text-muted-foreground">
          <ArrowLeft className="size-4" /> Back
        </button>

        <div className="flex items-center gap-3">
          <div className="grid size-14 shrink-0 place-items-center rounded-2xl" style={{ background: rc + "22" }}>
            <CardIcon id={card.id} size={30} color={rc} />
          </div>
          <div className="min-w-0">
            <div className="truncate text-xl font-extrabold">{card.name}</div>
            <span className="mt-0.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide" style={{ color: rc, background: rc + "22" }}>
              {card.rarity}
            </span>
          </div>
        </div>

        <div className="mt-6">
          <SectionLabel>How it plays</SectionLabel>
          <div className="mt-2">
            <CardDemo card={card} />
          </div>
        </div>

        <p className="mt-5 text-sm leading-relaxed text-muted-foreground">{card.description}</p>

        <div className="mt-4 flex items-center gap-2 rounded-xl bg-secondary/40 px-3 py-2.5 text-xs text-muted-foreground">
          <Layers3 className="size-4 shrink-0 text-primary" />
          Drawn when you bring a piece home — pick 1 of 4.
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------ in-game scoreboard */

// one distinct colour per die face (1..6), used by both the distribution bar and pips.
const FACE_COLOR = ["#e5c07b", "#e08c5a", "#d76a6a", "#a878d0", "#5aa0d8", "#5cb87a"];

// Render a die face as pips (dots) instead of a numeral — 1 → •, 2 → ••, … 6 → ⚅.
function FacePips({ n, color }: { n: number; color: string }) {
  return (
    <span className="inline-grid grid-cols-3 gap-[1.5px]" style={{ width: 15 }}>
      {Array.from({ length: n }).map((_, i) => (
        <span key={i} className="size-[3px] self-center justify-self-center rounded-full" style={{ background: color }} />
      ))}
    </span>
  );
}

function GameScoreboard({
  code,
  state,
  seatNames,
  seatUser,
  removedSeats,
  stats,
  mySeat,
  onClose,
}: {
  code: string;
  state: GameState;
  seatNames: Record<string, string>;
  seatUser: Record<string, number | null>;
  removedSeats: number[];
  stats: GameStats;
  mySeat: number | null;
  onClose: () => void;
}) {
  const [knocks, setKnocks] = useState<KnockEvent[]>([]);
  // detail list: which player's knocks / could'ves to show
  const [detail, setDetail] = useState<{ seat: number; taken: boolean; name: string } | null>(null);
  useEffect(() => {
    let live = true;
    api.matchKnocks(code).then((k) => live && setKnocks(k)).catch(() => {});
    return () => {
      live = false;
    };
  }, [code]);

  const removed = new Set(removedSeats);
  const rows = state.players.map((p, seat) => {
    const hist = stats.rolls[String(seat)] ?? {};
    const rolls = [1, 2, 3, 4, 5, 6].reduce((a, f) => a + (hist[String(f)] ?? 0), 0);
    const sum = [1, 2, 3, 4, 5, 6].reduce((a, f) => a + f * (hist[String(f)] ?? 0), 0);
    return {
      seat,
      color: p.color,
      name: seatNames[String(seat)] || (seatUser[String(seat)] ? "Player" : "Bot"),
      gone: removed.has(seat),
      hist,
      rolls,
      sum,
      avg: rolls ? sum / rolls : 0,
      dealt: stats.dealt[String(seat)] ?? 0,
      taken: stats.taken[String(seat)] ?? 0,
      potential: stats.potential[String(seat)] ?? 0,
    };
  });
  rows.sort((a, b) => b.sum - a.sum || b.rolls - a.rolls);

  const detailList = detail
    ? knocks.filter((k) => k.attacker_seat === detail.seat && k.taken === detail.taken)
    : [];

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="lb-pop max-h-[92dvh] w-full max-w-md overflow-y-auto rounded-t-3xl bg-card p-4 pb-6 ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" />
        <div className="mb-0.5 flex items-center gap-2">
          <Trophy className="size-5 text-primary" />
          <h2 className="text-lg font-extrabold">This game</h2>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">Players ranked by total of their rolls.</p>

        <div className="flex flex-col gap-2">
          {rows.map((r, i) => (
            <div
              key={r.seat}
              className={cn(
                "rounded-xl px-2.5 py-2 ring-1",
                r.seat === mySeat ? "bg-primary/10 ring-primary/40" : "bg-secondary/50 ring-white/10",
                r.gone && "opacity-50"
              )}
            >
              <div className="flex items-center gap-2">
                <span className="w-4 text-center text-sm font-bold tabular-nums text-muted-foreground">{i + 1}</span>
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: COLOR_HEX[r.color] }} />
                <span className="min-w-0 flex-1 truncate text-sm font-bold">
                  {r.name}
                  {r.seat === mySeat && <span className="ml-1 text-xs font-normal text-primary">you</span>}
                  {r.gone && <span className="ml-1 text-xs font-normal text-muted-foreground">left</span>}
                </span>
                <span className="shrink-0 text-right leading-none">
                  <span className="text-base font-extrabold tabular-nums text-primary">{r.sum}</span>
                  <span className="ml-1 text-[10px] uppercase text-muted-foreground">total</span>
                </span>
              </div>

              {/* Distribution as proportions of this player's own rolls — so it reads the
                  same at 6 rolls or 600, instead of bars that mislead as counts pile up. */}
              <div className="mt-1.5 flex h-2 w-full gap-px overflow-hidden rounded-full bg-black/25">
                {[1, 2, 3, 4, 5, 6].map((f) => {
                  const n = r.hist[String(f)] ?? 0;
                  if (!n) return null;
                  return <div key={f} style={{ flexGrow: n, background: FACE_COLOR[f - 1] }} />;
                })}
              </div>
              <div className="mt-1 flex items-center justify-between">
                {[1, 2, 3, 4, 5, 6].map((f) => {
                  const n = r.hist[String(f)] ?? 0;
                  return (
                    <span key={f} className="flex items-center gap-1">
                      <FacePips n={f} color={FACE_COLOR[f - 1]} />
                      <span className="text-[10px] font-bold tabular-nums text-muted-foreground">{n}</span>
                    </span>
                  );
                })}
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                <span>{r.rolls} rolls</span>
                <span>avg {r.avg.toFixed(2)}</span>
                <button
                  disabled={!r.dealt}
                  onClick={() => setDetail({ seat: r.seat, taken: true, name: r.name })}
                  className={cn("tabular-nums", r.dealt ? "text-primary underline decoration-dotted underline-offset-2" : "")}
                >
                  {r.dealt} knocks
                </button>
                <span>{r.taken} knocked</span>
                <button
                  disabled={!r.potential}
                  onClick={() => setDetail({ seat: r.seat, taken: false, name: r.name })}
                  className={cn("tabular-nums", r.potential ? "text-[#e0a44a] underline decoration-dotted underline-offset-2" : "")}
                >
                  {r.potential} could&rsquo;ve
                </button>
              </div>
            </div>
          ))}
        </div>

        <Button className="mt-4 w-full" variant="secondary" onClick={onClose}>
          Close
        </Button>
      </div>

      {detail && (
        <div
          className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 backdrop-blur-sm"
          onClick={(e) => {
            e.stopPropagation();
            setDetail(null);
          }}
        >
          <div
            className="lb-pop max-h-[70dvh] w-full max-w-md overflow-y-auto rounded-t-3xl bg-card p-5 pb-8 ring-1 ring-white/10"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" />
            <h3 className="text-base font-extrabold">
              {detail.name} — {detail.taken ? "knocks" : "could’ve knocked"}
            </h3>
            <p className="mb-3 text-xs text-muted-foreground">
              {detail.taken
                ? "Opponents this player knocked home."
                : "Captures that were legal but not taken."}
            </p>
            <div className="flex flex-col gap-1.5">
              {detailList.map((k) => (
                <div key={k.id} className="flex items-center gap-2 rounded-xl bg-secondary/60 px-3 py-2 text-sm">
                  <span className={cn("shrink-0", detail.taken ? "text-primary" : "text-[#e0a44a]")}>
                    {detail.taken ? <Check className="size-4" /> : <X className="size-4" />}
                  </span>
                  <span className="flex-1 truncate font-semibold">{k.victim_name}</span>
                  <span className="text-[10px] text-muted-foreground">turn {k.turn}</span>
                </div>
              ))}
              {detailList.length === 0 && (
                <div className="text-xs text-muted-foreground">Nothing here yet.</div>
              )}
            </div>
            <Button className="mt-4 w-full" variant="secondary" onClick={() => setDetail(null)}>
              Back
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------- player profile */

function ProfileSheet({ userId, onClose }: { userId: number; onClose: () => void }) {
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let live = true;
    setStats(null);
    setErr(false);
    api
      .userProfile(userId)
      .then((s) => live && setStats(s))
      .catch(() => live && setErr(true));
    return () => {
      live = false;
    };
  }, [userId]);

  const maxCount = stats ? Math.max(1, ...Object.values(stats.dice)) : 1;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="lb-pop w-full max-w-md rounded-t-3xl bg-card p-5 pb-8 ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-white/20" />
        {err && <div className="py-8 text-center text-sm text-muted-foreground">Couldn&apos;t load this player.</div>}
        {!err && !stats && <div className="py-8 text-center text-sm text-muted-foreground">Loading…</div>}
        {stats && (
          <>
            <div className="flex items-center gap-3">
              <div className="flex size-12 items-center justify-center rounded-2xl bg-primary text-lg font-extrabold text-primary-foreground">
                {(stats.first_name || "P").charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-lg font-extrabold">{stats.first_name}</div>
                <div className="text-xs text-muted-foreground">
                  Level {stats.level} · {stats.games_won}/{stats.games_played} wins
                </div>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 text-center">
              <ProfileStat label="Avg roll" value={stats.dice_avg ? stats.dice_avg.toFixed(2) : "—"} />
              <ProfileStat label="Could've" value={stats.potential_knocks} />
              <ProfileStat label="Knocks" value={stats.captures_dealt} />
              <ProfileStat label="Knocked" value={stats.captures_taken} />
            </div>

            <div className="mt-4">
              <div className="mb-1.5 flex items-center justify-between">
                <SectionLabel>Dice history</SectionLabel>
                <span className="text-xs text-muted-foreground tabular-nums">{stats.dice_rolls} rolls</span>
              </div>
              {stats.dice_rolls === 0 ? (
                <div className="rounded-xl bg-secondary/60 px-3 py-4 text-center text-xs text-muted-foreground">
                  No rolls yet.
                </div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {[1, 2, 3, 4, 5, 6].map((f) => {
                    const n = stats.dice[String(f)] ?? 0;
                    return (
                      <div key={f} className="flex items-center gap-2">
                        <span className="flex w-6 justify-center">
                          <FacePips n={f} color={FACE_COLOR[f - 1]} />
                        </span>
                        <div className="h-3 flex-1 overflow-hidden rounded-full bg-secondary">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{ width: `${(n / maxCount) * 100}%`, background: FACE_COLOR[f - 1] }}
                          />
                        </div>
                        <span className="w-8 text-right text-xs font-bold tabular-nums text-muted-foreground">{n}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <Button className="mt-5 w-full" variant="secondary" onClick={onClose}>
              Close
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function ProfileStat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-2xl bg-secondary/60 py-2.5">
      <div className="text-lg font-extrabold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- admin */

const ADMIN_TABS = ["overview", "data", "chats", "knocks", "reactions", "polls"] as const;
type AdminTab = (typeof ADMIN_TABS)[number];
const ADMIN_TAB_LABEL: Record<AdminTab, string> = {
  overview: "Overview",
  data: "Data",
  chats: "Chats",
  knocks: "Knocks",
  reactions: "Reactions",
  polls: "Polls",
};

function AdminPanel({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<AdminTab>("overview");
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

      <div className="flex gap-1.5">
        {ADMIN_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex-1 rounded-xl py-2 text-[11px] font-bold uppercase tracking-wide transition",
              tab === t ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground"
            )}
          >
            {ADMIN_TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {tab === "data" && <DataBrowser />}
      {tab === "chats" && <ChatBrowser />}
      {tab === "knocks" && <KnockLeaderboard />}
      {tab === "reactions" && <ReactionManager />}
      {tab === "polls" && <PollManager />}

      {tab === "overview" && (
      <>
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
      </>
      )}
    </Shell>
  );
}

function DataBrowser() {
  const [tables, setTables] = useState<AdminTable[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [data, setData] = useState<AdminRows | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.adminTables().then(setTables).catch((e) => setErr(String(e)));
  }, []);

  const load = (table: string, offset: number) => {
    setLoading(true);
    setErr(null);
    api
      .adminRows(table, 25, offset)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };
  const open = (table: string) => {
    setActive(table);
    load(table, 0);
  };

  const cell = (v: unknown) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  };

  return (
    <>
      {err && <div className="rounded-xl bg-red/10 px-3 py-2 text-center text-xs text-red">{err}</div>}

      <Card>
        <SectionLabel>Tables</SectionLabel>
        <div className="mt-2.5 flex flex-col gap-1.5">
          {tables.map((t) => (
            <button
              key={t.name}
              onClick={() => open(t.name)}
              className={cn(
                "flex items-center justify-between rounded-xl px-3 py-2 text-left text-sm transition active:scale-[0.98]",
                active === t.name ? "bg-primary/15 ring-1 ring-primary/40" : "bg-secondary/60"
              )}
            >
              <span className="inline-flex items-center gap-2 font-semibold">
                <Database className="size-4 text-muted-foreground" />
                {t.name}
              </span>
              <span className="text-xs tabular-nums text-muted-foreground">{t.rows.toLocaleString()} rows</span>
            </button>
          ))}
          {tables.length === 0 && <div className="text-xs text-muted-foreground">Loading…</div>}
        </div>
      </Card>

      {active && data && (
        <Card className="p-2">
          <div className="flex items-center justify-between px-1 pb-2">
            <SectionLabel>{data.table}</SectionLabel>
            <span className="text-[10px] text-muted-foreground">
              {data.offset + 1}–{Math.min(data.offset + data.rows.length, data.total)} of{" "}
              {data.total.toLocaleString()}
            </span>
          </div>
          <div className="no-scrollbar overflow-x-auto">
            <table className="w-full border-collapse text-[11px]">
              <thead>
                <tr>
                  {data.columns.map((c) => (
                    <th
                      key={c}
                      className="whitespace-nowrap border-b border-white/10 px-2 py-1.5 text-left font-bold text-muted-foreground"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i} className="border-b border-white/5">
                    {data.columns.map((c) => (
                      <td key={c} className="max-w-[180px] truncate whitespace-nowrap px-2 py-1.5 tabular-nums">
                        {cell(row[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex items-center justify-between px-1">
            <Button
              variant="secondary"
              size="sm"
              disabled={loading || data.offset === 0}
              onClick={() => load(data.table, Math.max(0, data.offset - data.limit))}
            >
              Prev
            </Button>
            <span className="text-[10px] text-muted-foreground">{loading ? "loading…" : ""}</span>
            <Button
              variant="secondary"
              size="sm"
              disabled={loading || data.offset + data.limit >= data.total}
              onClick={() => load(data.table, data.offset + data.limit)}
            >
              Next
            </Button>
          </div>
        </Card>
      )}
    </>
  );
}

/* -------------------------------------------------- admin: knocks */

function KnockLeaderboard() {
  const [rows, setRows] = useState<AdminKnockRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.adminKnocks().then(setRows).catch((e) => setErr(String(e)));
  }, []);

  return (
    <Card>
      <SectionLabel>Knock stats — all players</SectionLabel>
      <p className="mt-1 text-xs text-muted-foreground">
        Knocks made, times knocked, and captures passed up (potential).
      </p>
      {err && <div className="mt-2 text-xs text-red">{err}</div>}
      {!rows && !err && <div className="mt-3 text-xs text-muted-foreground">Loading…</div>}
      {rows && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="px-1 py-1 font-semibold">Player</th>
                <th className="px-1 py-1 text-right font-semibold">Knocks</th>
                <th className="px-1 py-1 text-right font-semibold">Knocked</th>
                <th className="px-1 py-1 text-right font-semibold">Could&rsquo;ve</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-white/5">
                  <td className="max-w-[120px] truncate px-1 py-1.5 font-semibold">{r.first_name}</td>
                  <td className="px-1 py-1.5 text-right tabular-nums text-primary">{r.knocks}</td>
                  <td className="px-1 py-1.5 text-right tabular-nums">{r.knocked}</td>
                  <td className="px-1 py-1.5 text-right tabular-nums text-[#e0a44a]">{r.potential}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-1 py-3 text-center text-muted-foreground">
                    No data yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------- admin: instant polls */

function PollManager() {
  const [rows, setRows] = useState<PollTemplate[]>([]);
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState("Yes, No");
  const [trigger, setTrigger] = useState("knock");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.adminPollTemplates().then(setRows).catch((e) => setErr(String(e)));
  }, []);

  const add = async () => {
    const question = q.trim();
    const options = opts.split(",").map((o) => o.trim()).filter(Boolean);
    if (!question || options.length < 2 || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setRows(await api.adminAddPollTemplate(question, options, trigger));
      setQ("");
      setOpts("Yes, No");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id: number) => {
    try {
      setRows(await api.adminRemovePollTemplate(id));
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <Card>
      <SectionLabel>Instant polls</SectionLabel>
      <p className="mt-1 text-xs text-muted-foreground">
        Quick polls a player can drop in chat. A <b>knock</b> poll only appears when they can
        actually capture; <b>any</b> is always available.
      </p>

      <div className="mt-3 flex flex-col gap-2">
        {rows.map((t) => (
          <div key={t.id} className="flex items-center gap-2 rounded-xl bg-secondary/60 px-3 py-2 ring-1 ring-white/10">
            <BarChart2 className="size-4 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{t.question}</div>
              <div className="text-[11px] text-muted-foreground">
                {t.options.join(" · ")} — <span className="uppercase">{t.trigger}</span>
              </div>
            </div>
            <button
              onClick={() => remove(t.id)}
              className="grid size-6 shrink-0 place-items-center rounded-full bg-red/80 text-white active:scale-90"
              aria-label="Remove"
            >
              <Trash2 className="size-3" />
            </button>
          </div>
        ))}
        {rows.length === 0 && <span className="text-xs text-muted-foreground">No polls yet.</span>}
      </div>

      <div className="mt-4 flex flex-col gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Question — e.g. Should I knock it?"
          className="rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-primary/50"
        />
        <input
          value={opts}
          onChange={(e) => setOpts(e.target.value)}
          placeholder="Options, comma-separated"
          className="rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-primary/50"
        />
        <div className="flex gap-2">
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            className="flex-1 rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10"
          >
            <option value="knock">knock</option>
            <option value="any">any</option>
          </select>
          <Button onClick={add} disabled={busy || !q.trim()}>
            <Plus className="size-4" /> Add
          </Button>
        </div>
      </div>
      {err && <div className="mt-2 text-xs text-red">{err}</div>}
    </Card>
  );
}

/* -------------------------------------------------- admin: reactions */

function ReactionManager() {
  const [rows, setRows] = useState<AdminReaction[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.adminReactions().then(setRows).catch((e) => setErr(String(e)));
  }, []);

  const add = async () => {
    const emoji = draft.trim();
    if (!emoji || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setRows(await api.adminAddReaction(emoji));
      setDraft("");
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id: number) => {
    try {
      setRows(await api.adminRemoveReaction(id));
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <Card>
      <SectionLabel>Chat reactions</SectionLabel>
      <p className="mt-1 text-xs text-muted-foreground">
        The emoji players can react with. Add or remove — changes apply everywhere.
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {rows.map((r) => (
          <div
            key={r.id}
            className="flex items-center gap-1.5 rounded-full bg-secondary px-2.5 py-1.5 ring-1 ring-white/10"
          >
            <span className="text-lg leading-none">{r.emoji}</span>
            <button
              onClick={() => remove(r.id)}
              className="grid size-5 place-items-center rounded-full bg-red/80 text-white active:scale-90"
              aria-label="Remove"
            >
              <Trash2 className="size-3" />
            </button>
          </div>
        ))}
        {rows.length === 0 && (
          <span className="text-xs text-muted-foreground">No reactions yet.</span>
        )}
      </div>

      <div className="mt-4 flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="Paste an emoji…"
          maxLength={16}
          className="min-w-0 flex-1 rounded-xl bg-secondary px-3 py-2 text-lg outline-none ring-1 ring-white/10 focus:ring-primary/50"
        />
        <Button onClick={add} disabled={busy || !draft.trim()}>
          <Plus className="size-4" /> Add
        </Button>
      </div>
      {err && <div className="mt-2 text-xs text-red">{err}</div>}
    </Card>
  );
}

/* -------------------------------------------------- admin: chat viewer */

function ChatBrowser() {
  const [ref, setRef] = useState("");
  const [view, setView] = useState<AdminChatView | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const look = async () => {
    const q = ref.trim();
    if (!q) return;
    setLoading(true);
    setErr(null);
    setView(null);
    try {
      setView(await api.adminMatchChat(q));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  const colorOf = (seat?: AdminChatSeat) => (seat ? COLOR_HEX[seat.color] ?? "#8892a6" : "#8892a6");
  const seatByUser = new Map<number, AdminChatSeat>();
  view?.seats.forEach((s) => s.user_id != null && seatByUser.set(s.user_id, s));

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <SectionLabel>Find a game</SectionLabel>
        <p className="mt-1 text-xs text-muted-foreground">
          Enter a match id or room code to see who played and everything they said.
        </p>
        <div className="mt-3 flex gap-2">
          <input
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && look()}
            placeholder="e.g. 42 or ABCDE"
            className="min-w-0 flex-1 rounded-xl bg-secondary px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-primary/50"
          />
          <Button onClick={look} disabled={loading || !ref.trim()}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : "View"}
          </Button>
        </div>
        {err && <div className="mt-2 text-xs text-red">{err}</div>}
      </Card>

      {view && (
        <>
          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel>
                Room {view.code} · #{view.id}
              </SectionLabel>
              <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-bold uppercase text-muted-foreground">
                {view.status}
              </span>
            </div>
            <div className="mt-2 flex flex-col gap-1.5">
              {view.seats.map((s) => (
                <div key={s.seat_index} className="flex items-center gap-2 text-sm">
                  <span className="size-3 shrink-0 rounded-full" style={{ background: colorOf(s) }} />
                  <span className="flex-1 truncate font-semibold">{s.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {s.is_bot ? "bot" : s.user_id != null ? `#${s.user_id}` : "open"}
                  </span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel>Messages</SectionLabel>
              <span className="text-xs text-muted-foreground tabular-nums">{view.messages.length}</span>
            </div>
            <div className="mt-2 flex flex-col gap-2">
              {view.messages.map((m) => {
                const seat = seatByUser.get(m.user_id);
                return (
                  <div
                    key={m.id}
                    className={cn(
                      "rounded-xl bg-secondary/50 px-3 py-2 text-sm ring-1 ring-white/5",
                      m.deleted && "opacity-60"
                    )}
                  >
                    {m.reply_text && (
                      <div className="mb-1 border-l-2 border-white/20 pl-2 text-[11px] text-muted-foreground">
                        <span className="font-bold">{m.reply_name}</span> {m.reply_text}
                      </div>
                    )}
                    <div className="flex items-baseline gap-2">
                      <span className="font-bold" style={{ color: colorOf(seat) }}>
                        {m.name}
                      </span>
                      <span className="text-[10px] text-muted-foreground">#{m.user_id}</span>
                      {m.edited && <span className="text-[10px] text-muted-foreground">edited</span>}
                      {m.deleted && (
                        <span className="rounded bg-red/20 px-1 text-[10px] font-bold text-red">deleted</span>
                      )}
                    </div>
                    <div className={cn("break-words", m.deleted && "line-through")}>{m.text}</div>
                    {Object.keys(m.reactions).length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {Object.entries(m.reactions).map(([emoji, count]) => (
                          <span key={emoji} className="rounded-full bg-white/10 px-1.5 py-0.5 text-[11px]">
                            {emoji} {count}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
              {view.messages.length === 0 && (
                <div className="text-xs text-muted-foreground">No messages in this game.</div>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- nav */

const TABS: { view: Tab; label: string; icon: React.ElementType }[] = [
  { view: "shop", label: "Shop", icon: ShoppingBag },
  { view: "friends", label: "Friends", icon: Users },
  { view: "home", label: "Play", icon: Gamepad2 },
  { view: "cards", label: "Cards", icon: Layers3 },
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

function DieFace({ value, skin, size }: { value: number; skin: string; size: number }) {
  const sk = skinOf(skin);
  const step = size / 3;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <rect
        width={size}
        height={size}
        rx={size * 0.22}
        fill={sk.face}
        stroke={sk.edge}
        strokeWidth={1.5}
      />
      {(PIPS[value] ?? []).map(([c, r], i) => (
        <circle
          key={i}
          cx={step * (c + 0.5)}
          cy={step * (r + 0.5)}
          r={size * 0.082}
          fill={sk.pip}
        />
      ))}
    </svg>
  );
}

function MeScreen({
  profile,
  onAdmin,
  onProfile,
}: {
  profile: Profile;
  onAdmin: () => void;
  onProfile: (p: Profile) => void;
}) {
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

      <Card>
        <SectionLabel>Your dice</SectionLabel>
        <p className="mt-1 text-[11px] text-muted-foreground">
          This is the die you roll on the board — everyone sees it.
        </p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {Object.values(DICE_SKINS).map((sk) => {
            const active = profile.dice_skin === sk.id;
            return (
              <button
                key={sk.id}
                onClick={async () => {
                  haptic("light");
                  try {
                    onProfile(await api.setDiceSkin(sk.id));
                  } catch {
                    /* ignore */
                  }
                }}
                className={cn(
                  "flex flex-col items-center gap-1.5 rounded-2xl bg-secondary/60 p-3 transition active:scale-[0.97]",
                  active ? "ring-2 ring-primary" : "ring-1 ring-white/10"
                )}
              >
                <DieFace value={5} skin={sk.id} size={38} />
                <span className="text-[10px] font-bold text-muted-foreground">{sk.name}</span>
              </button>
            );
          })}
        </div>
      </Card>

      {profile.is_admin && (
        <Button variant="secondary" className="w-full" onClick={onAdmin}>
          <Shield className="size-4" /> Admin panel
        </Button>
      )}
    </Shell>
  );
}
