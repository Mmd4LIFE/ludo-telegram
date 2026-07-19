"use client";

// Classic Ludo board — a real 15×15 cross, not a ring.
//
// The engine (app/ludo/board.py) models each token as one integer `progress`:
//   -1        in the yard (BASE)
//   0..50     on the shared 52-cell track, relative to the token's own start
//   51..56    in that colour's private 6-cell home column (56 = HOME, finished)
// A token's ABSOLUTE track cell is (START_OFFSET[colour] + progress) % 52.
//
// This renderer maps every one of those states to an (x,y) grid cell of the classic
// cross so the picture matches a physical Ludo board exactly. The 52-cell TRACK path,
// the four home columns and the safe stars below were derived from the engine's offsets
// (RED 0 / GREEN 13 / YELLOW 26 / BLUE 39) and verified: each colour's start lands on its
// coloured entry cell and each home column runs into the centre. Colliding tokens share an
// absolute cell, so they always render on the same square (capture reads correctly).

import { GameState, LegalMove } from "@/lib/ws";

const CELL = 34;
const SIZE = CELL * 15;

// Vivid, board-authentic colours (kept in sync with the app palette).
const COLORS: Record<string, string> = {
  RED: "#e5484d",
  GREEN: "#30a46c",
  YELLOW: "#f2b705",
  BLUE: "#3e63dd",
};
const COLOR_SOFT: Record<string, string> = {
  RED: "#f7c6c8",
  GREEN: "#bfe6d3",
  YELLOW: "#fbe6a6",
  BLUE: "#c6d2f5",
};

const START_OFFSET: Record<string, number> = { RED: 0, GREEN: 13, YELLOW: 26, BLUE: 39 };

// The 52 track cells as [col,row] on the 15×15 grid, index = absolute track cell 0..51.
const TRACK: [number, number][] = [
  [1, 6], [2, 6], [3, 6], [4, 6], [5, 6],                 // 0-4   RED start → right
  [6, 5], [6, 4], [6, 3], [6, 2], [6, 1], [6, 0],         // 5-10  up the top arm (left lane)
  [7, 0],                                                  // 11    top tip
  [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5],         // 12-17 GREEN start (13) → down
  [9, 6], [10, 6], [11, 6], [12, 6], [13, 6], [14, 6],    // 18-23 right arm (top lane)
  [14, 7],                                                 // 24    right tip
  [14, 8], [13, 8], [12, 8], [11, 8], [10, 8], [9, 8],    // 25-30 YELLOW start (26) → left
  [8, 9], [8, 10], [8, 11], [8, 12], [8, 13], [8, 14],    // 31-36 down the bottom arm
  [7, 14],                                                 // 37    bottom tip
  [6, 14], [6, 13], [6, 12], [6, 11], [6, 10], [6, 9],    // 38-43 BLUE start (39) → up
  [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],         // 44-49 left arm (bottom lane)
  [0, 7],                                                  // 50    left tip
  [0, 6],                                                  // 51    back toward RED
];

// Private home columns (progress 51..56), innermost cell = HOME (56).
const HOME_COL: Record<string, [number, number][]> = {
  RED: [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7], [6, 7]],
  GREEN: [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5], [7, 6]],
  YELLOW: [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7], [8, 7]],
  BLUE: [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9], [7, 8]],
};

// 6×6 yard (origin col,row) and the four token home-slots inside it.
const YARD: Record<string, { ox: number; oy: number; slots: [number, number][] }> = {
  RED: { ox: 0, oy: 0, slots: [[2, 2], [4, 2], [2, 4], [4, 4]] },
  GREEN: { ox: 9, oy: 0, slots: [[11, 2], [13, 2], [11, 4], [13, 4]] },
  YELLOW: { ox: 9, oy: 9, slots: [[11, 11], [13, 11], [11, 13], [13, 13]] },
  BLUE: { ox: 0, oy: 9, slots: [[2, 11], [4, 11], [2, 13], [4, 13]] },
};

// Safe stars = the four starts + four cells eight steps past each start (mirrors board.py).
const SAFE = new Set([0, 8, 13, 21, 26, 34, 39, 47]);
const START_ABS = new Set(Object.values(START_OFFSET));

// grid cell (col,row) → pixel centre
const px = (c: number, r: number): [number, number] => [(c + 0.5) * CELL, (r + 0.5) * CELL];

function tokenCell(color: string, prog: number, ti: number): [number, number] {
  if (prog < 0) return YARD[color].slots[ti];
  if (prog <= 50) return TRACK[(START_OFFSET[color] + prog) % 52];
  return HOME_COL[color][prog - 51];
}

function StarGlyph({ c, r }: { c: number; r: number }) {
  const [x, y] = px(c, r);
  return (
    <text x={x} y={y + CELL * 0.14} textAnchor="middle" fontSize={CELL * 0.5} fill="#9aa1ad">
      ★
    </text>
  );
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

  // Collect every token, grouped by the cell it occupies so overlaps can be spread.
  type Tok = { seat: number; ti: number; color: string; canMove: boolean; finished: boolean };
  const byCell = new Map<string, Tok[]>();
  state.players.forEach((p, seat) => {
    p.tokens.forEach((prog, ti) => {
      const [c, r] = tokenCell(p.color, prog, ti);
      const key = `${c},${r}`;
      const canMove = seat === mySeat && myTurn && movable.has(ti);
      (byCell.get(key) ?? byCell.set(key, []).get(key)!).push({
        seat, ti, color: p.color, canMove, finished: prog >= 56,
      });
    });
  });

  const cellFor = (key: string): [number, number] => {
    const [c, r] = key.split(",").map(Number);
    return px(c, r);
  };

  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      width="100%"
      style={{ maxWidth: 440, display: "block", margin: "0 auto", borderRadius: 16 }}
    >
      {/* board surface */}
      <rect x={0} y={0} width={SIZE} height={SIZE} rx={16} fill="#f6f7f9" />

      {/* four coloured yards */}
      {Object.entries(YARD).map(([col, y]) => (
        <g key={col}>
          <rect
            x={y.ox * CELL} y={y.oy * CELL} width={6 * CELL} height={6 * CELL}
            rx={12} fill={COLORS[col]}
          />
          <rect
            x={(y.ox + 1) * CELL} y={(y.oy + 1) * CELL} width={4 * CELL} height={4 * CELL}
            rx={10} fill="#ffffff"
          />
          {y.slots.map(([c, r], i) => {
            const [cx, cy] = px(c, r);
            return <circle key={i} cx={cx} cy={cy} r={CELL * 0.34} fill={COLOR_SOFT[col]} stroke={COLORS[col]} strokeWidth={2} />;
          })}
        </g>
      ))}

      {/* track cells */}
      {TRACK.map(([c, r], abs) => {
        const [x, y] = [c * CELL, r * CELL];
        const isStart = START_ABS.has(abs);
        const startColor = Object.entries(START_OFFSET).find(([, o]) => o === abs)?.[0];
        return (
          <rect
            key={abs}
            x={x} y={y} width={CELL} height={CELL}
            fill={isStart && startColor ? COLOR_SOFT[startColor] : "#ffffff"}
            stroke="#d6d9e0" strokeWidth={1}
          />
        );
      })}

      {/* coloured home columns */}
      {Object.entries(HOME_COL).map(([col, cells]) =>
        cells.map(([c, r], i) => (
          <rect
            key={`${col}-${i}`}
            x={c * CELL} y={r * CELL} width={CELL} height={CELL}
            fill={COLORS[col]} stroke="#ffffff" strokeWidth={1} opacity={0.92}
          />
        ))
      )}

      {/* safe stars */}
      {[...SAFE].map((abs) => {
        const [c, r] = TRACK[abs];
        return <StarGlyph key={abs} c={c} r={r} />;
      })}

      {/* centre home — pinwheel of the four colours */}
      <rect x={6 * CELL} y={6 * CELL} width={3 * CELL} height={3 * CELL} fill="#ffffff" />
      {(() => {
        const [cx, cy] = px(7, 7);
        const L = 6 * CELL, T = 6 * CELL, R = 9 * CELL, B = 9 * CELL;
        const tris: [string, string][] = [
          ["GREEN", `${L},${T} ${R},${T} ${cx},${cy}`], // top
          ["YELLOW", `${R},${T} ${R},${B} ${cx},${cy}`], // right
          ["BLUE", `${R},${B} ${L},${B} ${cx},${cy}`],   // bottom
          ["RED", `${L},${B} ${L},${T} ${cx},${cy}`],    // left
        ];
        return tris.map(([col, pts]) => <polygon key={col} points={pts} fill={COLORS[col]} />);
      })()}

      {/* tokens (grouped per cell, spread when stacked) */}
      {[...byCell.entries()].map(([key, toks]) => {
        const [cx, cy] = cellFor(key);
        const n = toks.length;
        return toks.map((t, i) => {
          // spread stacked tokens around the cell centre
          let ox = 0, oy = 0;
          if (n > 1) {
            const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
            const rad = CELL * 0.2;
            ox = Math.cos(ang) * rad;
            oy = Math.sin(ang) * rad;
          }
          const r = CELL * (n > 1 ? 0.26 : 0.32);
          return (
            <g
              key={`${t.seat}-${t.ti}`}
              onClick={t.canMove ? () => onMove(t.ti) : undefined}
              style={{ cursor: t.canMove ? "pointer" : "default" }}
            >
              <circle cx={cx + ox} cy={cy + oy} r={r + 1.5} fill="#00000022" />
              <circle
                cx={cx + ox} cy={cy + oy} r={r}
                fill={COLORS[t.color]}
                stroke={t.canMove ? "#111827" : "#ffffff"}
                strokeWidth={t.canMove ? 2.5 : 2}
              />
              <circle cx={cx + ox} cy={cy + oy - r * 0.28} r={r * 0.32} fill="#ffffff" opacity={0.65} />
              {t.canMove && (
                <circle cx={cx + ox} cy={cy + oy} r={r + 4} fill="none" stroke={COLORS[t.color]} strokeWidth={2}>
                  <animate attributeName="r" values={`${r + 3};${r + 7};${r + 3}`} dur="1.1s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.9;0.2;0.9" dur="1.1s" repeatCount="indefinite" />
                </circle>
              )}
            </g>
          );
        });
      })}
    </svg>
  );
}
