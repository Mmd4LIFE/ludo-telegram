"use client";

// Ludo board renderer.
//
// The classic Ludo board is a 15x15 cross. Rendering that pixel-perfectly is fiddly and
// easy to get subtly wrong, so this base uses a geometrically EXACT ring: the 52 track
// cells are laid out evenly around a circle (correct by construction), each colour's
// home column is a spoke from its entry toward the centre, and bases sit outside the
// ring. It reads the same GameState the server sends and is a faithful, clickable view
// of the real game. Swapping in a cross-grid skin later is a pure-visual ROADMAP item —
// the coordinate mapping (progress -> point) is the only thing a new skin must provide.

import { GameState, LegalMove } from "@/lib/ws";

const MAIN = 52;
const RING_CELLS = 51; // progress 0..50 live on the ring
const HOME = 56;
const HOME_COL = 6;

// mirror app/ludo/board.py
const START_OFFSET: Record<string, number> = { RED: 0, GREEN: 13, YELLOW: 26, BLUE: 39 };
const SAFE = new Set([0, 8, 13, 21, 26, 34, 39, 47]);
const COLORS: Record<string, string> = {
  RED: "#e5484d",
  GREEN: "#30a46c",
  YELLOW: "#e2a336",
  BLUE: "#3e63dd",
};

const SIZE = 360;
const C = SIZE / 2;
const R = 128; // ring radius
const startAngleDeg = 90; // RED start points down

function ringPoint(absIndex: number): [number, number] {
  const a = ((startAngleDeg + (absIndex * 360) / MAIN) * Math.PI) / 180;
  return [C + R * Math.cos(a), C + R * Math.sin(a)];
}

function dirUnit(absIndex: number): [number, number] {
  const a = ((startAngleDeg + (absIndex * 360) / MAIN) * Math.PI) / 180;
  return [Math.cos(a), Math.sin(a)];
}

// Where to draw a token given its colour + progress.
function tokenPos(color: string, progress: number): [number, number] {
  const off = START_OFFSET[color];
  if (progress < 0) return baseSlot(color, 0); // base handled separately for spread
  if (progress <= RING_CELLS - 1) {
    return ringPoint((off + progress) % MAIN);
  }
  // home column 51..56: spoke from the entry cell inward to the centre
  const entry = (off + RING_CELLS - 1) % MAIN; // last ring cell before turning in
  const [ux, uy] = dirUnit(entry);
  const depth = (progress - (RING_CELLS - 1)) / HOME_COL; // 1/6 .. 1
  const px = C + ux * R * (1 - depth) * 0.72;
  const py = C + uy * R * (1 - depth) * 0.72;
  return [px, py];
}

function baseSlot(color: string, i: number): [number, number] {
  const off = START_OFFSET[color];
  const [ux, uy] = dirUnit(off);
  const bx = C + ux * (R + 46);
  const by = C + uy * (R + 46);
  const dx = (i % 2) * 22 - 11;
  const dy = Math.floor(i / 2) * 22 - 11;
  return [bx + dx, by + dy];
}

export default function Board({
  state,
  legal,
  mySeat,
  onMove,
}: {
  state: GameState;
  legal: LegalMove[];
  mySeat: number | null;
  onMove: (tokenIndex: number) => void;
}) {
  const movable = new Set(legal.map((m) => m.token_index));
  const myTurn = mySeat !== null && state.current === mySeat && state.phase === "move";

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width="100%" style={{ maxWidth: 420, display: "block", margin: "0 auto" }}>
      {/* ring track */}
      <circle cx={C} cy={C} r={R} fill="none" stroke="#3a3a44" strokeWidth={26} opacity={0.35} />
      {Array.from({ length: MAIN }).map((_, i) => {
        const [x, y] = ringPoint(i);
        const safe = SAFE.has(i);
        return (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={safe ? 8 : 6}
            fill={safe ? "#e9c46a" : "#20232b"}
            stroke="#454a55"
            strokeWidth={1}
          />
        );
      })}

      {/* colour start markers */}
      {Object.entries(START_OFFSET).map(([col, off]) => {
        const [x, y] = ringPoint(off);
        return <circle key={col} cx={x} cy={y} r={9} fill="none" stroke={COLORS[col]} strokeWidth={3} />;
      })}

      {/* centre home */}
      <circle cx={C} cy={C} r={26} fill="#20232b" stroke="#454a55" />

      {/* tokens */}
      {state.players.map((p, seat) => {
        // spread base tokens; ring/home tokens use their computed position
        const baseCounter: number[] = [];
        return p.tokens.map((prog, ti) => {
          let pos: [number, number];
          if (prog < 0) {
            const idx = baseCounter.length;
            baseCounter.push(idx);
            pos = baseSlot(p.color, ti);
          } else {
            pos = tokenPos(p.color, prog);
          }
          const isMine = seat === mySeat;
          const canMove = isMine && myTurn && movable.has(ti);
          return (
            <g
              key={`${seat}-${ti}`}
              onClick={canMove ? () => onMove(ti) : undefined}
              style={{ cursor: canMove ? "pointer" : "default" }}
            >
              <circle
                cx={pos[0]}
                cy={pos[1]}
                r={9}
                fill={COLORS[p.color]}
                stroke={canMove ? "#fff" : "#00000055"}
                strokeWidth={canMove ? 2.5 : 1}
              />
              {canMove && (
                <circle cx={pos[0]} cy={pos[1]} r={13} fill="none" stroke="#fff" strokeWidth={1.5} opacity={0.6}>
                  <animate attributeName="r" values="12;15;12" dur="1.2s" repeatCount="indefinite" />
                </circle>
              )}
            </g>
          );
        });
      })}
    </svg>
  );
}
