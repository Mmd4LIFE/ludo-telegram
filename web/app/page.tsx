"use client";

// Ludo Board — Telegram Mini App shell.
// Views: auth splash → lobby → waiting room (with native Telegram invite) → live match.
// The board renders authoritative server state over a per-match WebSocket.

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
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
} from "lucide-react";
import { api, authenticate, MatchSummary, Profile } from "@/lib/api";
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
type Clock = { deadline: number | null; now: number; recvAt: number; turnSeconds: number };

export default function Home() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [room, setRoom] = useState<Room | null>(null); // waiting room
  const [matchCode, setMatchCode] = useState<string>(""); // live match
  const [state, setState] = useState<GameState | null>(null);
  const [legal, setLegal] = useState<LegalMove[]>([]);
  const [seatUser, setSeatUser] = useState<Record<string, number | null>>({});
  const [clock, setClock] = useState<Clock | null>(null);
  const sockRef = useRef<MatchSocket | null>(null);

  // ---- live match socket ----
  const enterMatch = useCallback((code: string) => {
    setRoom(null);
    setMatchCode(code);
    setState(null);
    const sock = new MatchSocket(code, (msg) => {
      if (msg.type === "state") {
        const p = msg as StatePayload;
        setState(p.state);
        setLegal(p.legal_moves);
        setSeatUser(p.seat_user);
        setClock({
          deadline: p.deadline,
          now: p.now,
          recvAt: Date.now() / 1000,
          turnSeconds: p.turn_seconds,
        });
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
            .then(() => enterMatch(code))
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
      await api.joinMatch(code);
      enterMatch(code);
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
          <div className="text-4xl">⚠️</div>
          <p className="mt-2 text-sm text-muted-foreground break-words">{error}</p>
          <Button className="mt-4 w-full" onClick={() => location.reload()}>
            Reload
          </Button>
        </Card>
      </Shell>
    );

  if (!profile) return <Splash />;

  if (matchCode && state)
    return (
      <LiveMatch
        code={matchCode}
        state={state}
        legal={legal}
        profile={profile}
        seatUser={seatUser}
        clock={clock}
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
    <Lobby
      profile={profile}
      busy={busy}
      onPlayBots={playBots}
      onCreateRoom={createRoom}
      onJoin={joinFromList}
    />
  );
}

/* ------------------------------------------------------------------ shells */

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col gap-4 px-4 pb-8 pt-5">
      {children}
    </main>
  );
}

function Splash({ label = "Loading…" }: { label?: string }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4">
      <div className="text-5xl lb-pop">🎲</div>
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
    const load = () => api.listMatches().then((t) => alive && setTables(t)).catch(() => {});
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <Shell>
      <WalletBar profile={profile} />

      {/* hero */}
      <Card className="relative overflow-hidden bg-gradient-to-br from-[#1a2340] to-card">
        <div className="flex items-center gap-4">
          <Image
            src="/logo.png"
            alt="Ludo"
            width={72}
            height={72}
            className="rounded-2xl ring-1 ring-white/10"
            priority
          />
          <div>
            <h1 className="text-xl font-extrabold leading-tight">Ludo Board</h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Race four tokens home. Knock rivals back to base.
            </p>
          </div>
        </div>
        <Button size="lg" className="mt-4 w-full" disabled={busy} onClick={onPlayBots}>
          <Bot className="size-5" /> Play vs Bots
        </Button>
      </Card>

      {/* friends */}
      <Card>
        <SectionLabel>Play with friends</SectionLabel>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Create a private room and invite friends straight from Telegram.
        </p>
        <Button
          variant="secondary"
          className="mt-3 w-full"
          disabled={busy}
          onClick={onCreateRoom}
        >
          <Plus className="size-4" /> Create private room
        </Button>
      </Card>

      {/* open tables */}
      <div className="flex flex-col gap-2">
        <SectionLabel className="px-1">Open rooms</SectionLabel>
        {tables.length === 0 ? (
          <Card className="text-center text-xs text-muted-foreground">
            No open rooms right now — create one above.
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

      <p className="mt-auto pt-4 text-center text-[10px] text-muted-foreground">
        {profile.games_won}/{profile.games_played} games won
        {!getInitData() && " · dev login"}
      </p>
    </Shell>
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
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api.getMatch(room.code).then((s) => alive && setSummary(s)).catch(() => {});
    load();
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [room.code]);

  const seated = summary?.seated ?? 1;
  const ready = seated >= 2;

  const share = () => {
    haptic("light");
    shareRoom(profile.bot_username, room.code, "Join my Ludo game! 🎲");
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

  return (
    <Shell>
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-sm text-muted-foreground"
      >
        <ArrowLeft className="size-4" /> Back
      </button>

      <Card className="text-center">
        <SectionLabel>Room code</SectionLabel>
        <div className="mt-2 text-4xl font-extrabold tracking-[0.3em] text-primary">
          {room.code}
        </div>
        <div className="mt-3 flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 lb-spin" /> Waiting for players · {seated}/4 seated
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-2">
        <Button onClick={share}>
          <Share2 className="size-4" /> Invite
        </Button>
        <Button variant="secondary" onClick={copy}>
          <Copy className="size-4" /> {copied ? "Copied" : "Copy link"}
        </Button>
      </div>

      <Button
        size="lg"
        variant={ready ? "win" : "secondary"}
        className="w-full"
        disabled={!ready}
        onClick={() => onEnter(room.code)}
      >
        {ready ? "Start game" : "Waiting for one more…"}
      </Button>

      <p className="text-center text-xs text-muted-foreground">
        Share the invite — your friends open it in Telegram and drop straight into this room.
      </p>
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

function LiveMatch({
  code,
  state,
  legal,
  profile,
  seatUser,
  clock,
  sock,
  onLeave,
}: {
  code: string;
  state: GameState;
  legal: LegalMove[];
  profile: Profile;
  seatUser: Record<string, number | null>;
  clock: Clock | null;
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

  // ---- turn countdown (server deadline, corrected for clock skew) ----
  const [, force] = useState(0);
  useEffect(() => {
    if (!clock?.deadline) return;
    const id = setInterval(() => force((n) => n + 1), 200);
    return () => clearInterval(id);
  }, [clock?.deadline]);
  let remaining = 0;
  if (clock?.deadline) {
    const serverNow = clock.now + (Date.now() / 1000 - clock.recvAt);
    remaining = Math.max(0, clock.deadline - serverNow);
  }
  const pct = clock?.deadline ? Math.max(0, Math.min(100, (remaining / clock.turnSeconds) * 100)) : 0;
  const low = remaining <= 5;

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
              <div className="mt-1 text-[10px] font-bold text-muted-foreground">
                {isMe ? "YOU" : seatUser[String(seat)] ? "P" + seat : "BOT"}
              </div>
              <div className="text-[11px] font-bold tabular-nums">🏠 {home}/4</div>
            </div>
          );
        })}
      </div>

      {/* turn banner + countdown */}
      <div
        className="overflow-hidden rounded-2xl ring-1 ring-white/10"
        style={{ background: finished ? "#161d2c" : `${COLOR_HEX[currentColor]}22` }}
      >
        <div className="py-2.5 text-center text-sm font-bold">
          {finished ? (
            <span className="inline-flex items-center gap-2">
              <Trophy className="size-4 text-primary" /> Winner: seat {state.ranking[0]}
            </span>
          ) : noMoves ? (
            `No moves for ${currentColor} — passing…`
          ) : myTurn ? (
            state.phase === "roll" ? (
              "Your turn — roll the die!"
            ) : (
              "Your turn — tap a glowing token"
            )
          ) : (
            `${currentColor}'s turn…`
          )}
        </div>
        {clock?.deadline && !finished ? (
          <div className="h-1 w-full bg-black/20">
            <div
              className="h-full transition-[width] duration-200 ease-linear"
              style={{ width: `${pct}%`, background: low ? "#e5484d" : COLOR_HEX[currentColor] }}
            />
          </div>
        ) : null}
      </div>

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

      {/* dice + roll */}
      <div className="flex items-center justify-between gap-3">
        <RollingDie die={state.die} turn={state.turn} />
        <Button
          size="lg"
          className={cn("flex-1", myTurn && state.phase === "roll" && !finished && "animate-pulse")}
          disabled={!myTurn || state.phase !== "roll" || finished}
          onClick={() => {
            haptic("medium");
            sock?.roll();
          }}
        >
          <Dice5 className="size-5" />
          {myTurn && state.phase === "roll" ? (low ? `Roll! ${Math.ceil(remaining)}s` : "Roll") : "Roll"}
        </Button>
      </div>

      {finished && (
        <Button variant="secondary" className="w-full" onClick={onLeave}>
          Back to lobby
        </Button>
      )}
    </Shell>
  );
}
