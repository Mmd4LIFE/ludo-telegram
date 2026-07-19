"use client";

// Single-screen Mini App for the base: authenticate -> lobby -> live match.
// Deliberately compact; a follow-up session splits this into proper screens/router,
// adds the profile/economy/cosmetics tabs, and richer match UX. See docs/ROADMAP.md.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, authenticate, Profile } from "@/lib/api";
import { getInitData, haptic, initTelegram } from "@/lib/telegram";
import { GameState, LegalMove, MatchSocket, StatePayload } from "@/lib/ws";
import Board from "@/components/board";

const COLOR_NAMES = ["RED", "GREEN", "YELLOW", "BLUE"];

export default function Home() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState<string>("");         // active match code
  const [joinCode, setJoinCode] = useState<string>("");
  const [state, setState] = useState<GameState | null>(null);
  const [legal, setLegal] = useState<LegalMove[]>([]);
  const [seatUser, setSeatUser] = useState<Record<string, number | null>>({});
  const sockRef = useRef<MatchSocket | null>(null);

  useEffect(() => {
    initTelegram();
    authenticate().then(setProfile).catch((e) => setError(String(e)));
  }, []);

  const enterMatch = useCallback((c: string) => {
    setCode(c);
    const sock = new MatchSocket(c, (msg) => {
      if (msg.type === "state") {
        const p = msg as StatePayload;
        setState(p.state);
        setLegal(p.legal_moves);
        setSeatUser(p.seat_user);
      }
    });
    sock.connect();
    sockRef.current = sock;
  }, []);

  const leaveMatch = useCallback(() => {
    sockRef.current?.close();
    sockRef.current = null;
    setCode("");
    setState(null);
  }, []);

  const mySeat = (() => {
    if (!profile) return null;
    for (const [seat, uid] of Object.entries(seatUser)) {
      if (uid === profile.id) return Number(seat);
    }
    return null;
  })();

  // ---- render ----
  if (error) return <div className="wrap"><div className="card">⚠️ {error}</div></div>;
  if (!profile) return <div className="wrap"><div className="card">Loading…</div></div>;

  if (code && state) {
    return (
      <LiveMatch
        code={code}
        state={state}
        legal={legal}
        mySeat={mySeat}
        seatUser={seatUser}
        sock={sockRef.current}
        onLeave={leaveMatch}
      />
    );
  }

  return (
    <div className="wrap">
      <div className="row spread">
        <h1>🎲 Ludo Board</h1>
        <span className="pill">🪙 {profile.coins.toLocaleString()}</span>
      </div>
      <div className="muted">
        Hi {profile.first_name} · Lvl {profile.level} · {profile.games_won}/{profile.games_played} won
        {!getInitData() && " · (dev login)"}
      </div>

      <div className="card">
        <h2>Quick play</h2>
        <p className="muted">Start instantly against three house bots.</p>
        <button
          className="primary"
          style={{ width: "100%" }}
          onClick={async () => {
            const m = await api.createMatch({ max_players: 4, fill_with_bots: true });
            enterMatch(m.code);
          }}
        >
          Play vs bots
        </button>
      </div>

      <div className="card">
        <h2>Play with friends</h2>
        <p className="muted">Create a room and share the code, or join one.</p>
        <div className="row" style={{ marginBottom: 10 }}>
          <button
            className="good"
            style={{ flex: 1 }}
            onClick={async () => {
              const m = await api.createMatch({ max_players: 4 });
              enterMatch(m.code);
            }}
          >
            Create room
          </button>
        </div>
        <div className="row">
          <input
            placeholder="CODE"
            value={joinCode}
            maxLength={6}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
          />
          <button
            disabled={joinCode.length < 4}
            onClick={async () => {
              try {
                const m = await api.joinMatch(joinCode);
                enterMatch(m.code);
              } catch (e) {
                setError(String(e));
              }
            }}
          >
            Join
          </button>
        </div>
      </div>
    </div>
  );
}

function LiveMatch({
  code,
  state,
  legal,
  mySeat,
  seatUser,
  sock,
  onLeave,
}: {
  code: string;
  state: GameState;
  legal: LegalMove[];
  mySeat: number | null;
  seatUser: Record<string, number | null>;
  sock: MatchSocket | null;
  onLeave: () => void;
}) {
  const myTurn = mySeat !== null && state.current === mySeat;
  const currentColor = COLOR_NAMES[state.players[state.current]?.color as unknown as number] ??
    state.players[state.current]?.color;
  const finished = state.phase === "finished";

  return (
    <div className="wrap">
      <div className="row spread">
        <span className="pill">Room {code}</span>
        <button onClick={onLeave}>Leave</button>
      </div>

      {finished ? (
        <div className="turn-banner">
          🏆 Winner: seat {state.ranking[0]} ({state.players[state.ranking[0]]?.color})
        </div>
      ) : (
        <div className="turn-banner">
          {myTurn
            ? state.phase === "roll"
              ? "Your turn — roll!"
              : "Your turn — pick a token"
            : `Waiting for ${state.players[state.current]?.color}…`}
        </div>
      )}

      <div className="card">
        <Board
          state={state}
          legal={legal}
          mySeat={mySeat}
          onMove={(ti) => {
            haptic("light");
            sock?.move(ti);
          }}
        />
      </div>

      <div className="row spread">
        <div className="die">{state.die ?? "–"}</div>
        <button
          className="primary"
          disabled={!myTurn || state.phase !== "roll"}
          onClick={() => {
            haptic("medium");
            sock?.roll();
          }}
        >
          🎲 Roll
        </button>
      </div>
    </div>
  );
}
