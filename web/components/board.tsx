"use client";

// Classic Ludo board — a real 15×15 cross with animated tokens, move-destination
// markers, and board rotation so the viewer's colour sits at the front (bottom).
//
// Engine progress model: -1 BASE · 0..50 shared track (abs = (START_OFFSET+prog)%52) ·
// 51..55 private home column · 56 HOME (centre). Colliding tokens share an absolute cell
// so captures read correctly. The whole board is rotated k·90° about its centre so the
// viewer's yard is at the bottom; safe-square stars are SVG polygons kept upright.

import { GameState, LegalMove } from "@/lib/ws";

const CELL = 34;
const SIZE = CELL * 15;
const MID = SIZE / 2;

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

// Clockwise 90° steps to bring each colour's yard to the bottom-left (the "front").
const ROT_K: Record<string, number> = { RED: 3, GREEN: 2, YELLOW: 1, BLUE: 0 };

const TRACK: [number, number][] = [
  [1, 6], [2, 6], [3, 6], [4, 6], [5, 6],
  [6, 5], [6, 4], [6, 3], [6, 2], [6, 1], [6, 0],
  [7, 0],
  [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5],
  [9, 6], [10, 6], [11, 6], [12, 6], [13, 6], [14, 6],
  [14, 7],
  [14, 8], [13, 8], [12, 8], [11, 8], [10, 8], [9, 8],
  [8, 9], [8, 10], [8, 11], [8, 12], [8, 13], [8, 14],
  [7, 14],
  [6, 14], [6, 13], [6, 12], [6, 11], [6, 10], [6, 9],
  [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
  [0, 7],
  [0, 6],
];

const HOME_COL: Record<string, [number, number][]> = {
  RED: [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7]],
  GREEN: [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5]],
  YELLOW: [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7]],
  BLUE: [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9]],
};
// Finished tokens (progress 56) rest inside their OWN colour's centre triangle — the
// centroid of each pinwheel wedge — instead of piling on the middle dot.
const HOME_SLOT: Record<string, [number, number]> = {
  RED: [6, 7],    // left wedge
  GREEN: [7, 6],  // top wedge
  YELLOW: [8, 7], // right wedge
  BLUE: [7, 8],   // bottom wedge
};

// Each yard is a 6×6 block anchored at (ox,oy); slots are the four token positions in
// board cells. They're mirrored corner to corner so every home reads the same once the
// board rotates. The white home disc is derived from these (homeCircle), so moving a
// slot moves the disc with it.
const YARD: Record<string, { ox: number; oy: number; slots: [number, number][] }> = {
  RED: { ox: 0, oy: 0, slots: [[2, 2], [3, 2], [2, 3], [3, 3]] },
  GREEN: { ox: 9, oy: 0, slots: [[11, 2], [12, 2], [11, 3], [12, 3]] },
  YELLOW: { ox: 9, oy: 9, slots: [[11, 11], [12, 11], [11, 12], [12, 12]] },
  BLUE: { ox: 0, oy: 9, slots: [[2, 11], [3, 11], [2, 12], [3, 12]] },
};

// --- home panel tuning ------------------------------------------------------
// TOKEN_R  : token radius, in cells.
// HOME_PAD : white gap left around the tokens, in cells. Smaller = tighter panel.
// The white "home" panel is derived from the yard's slots (homePanel below) rather
// than hardcoded, so it always wraps exactly around the four tokens — move the slots
// in YARD and the border follows.
const TOKEN_R = 0.34;
const HOME_PAD = 0.16;
// thickness of the white ring drawn around each home's four tokens, in cells
const HOME_RING = 0.16;

// Board paper + the white margin left around each coloured home block. The inset is
// applied on all four sides, but only reads on the outer two — the inner sides sit
// against the white track, so the gap is invisible there. Result: a clean white rim
// framing the board.
const BOARD_BG = "#ffffff";
const YARD_INSET = 0.22;
// Home blocks are white with a coloured outline; the colour is carried by the ring and
// the tokens instead of a solid block.
const YARD_BG = "#ffffff";
const YARD_EDGE = 2;

// The white home is a disc centred on the four tokens, just big enough to hold them
// (their ring radius + a token + HOME_PAD). Derived from the slots, so it follows if
// the tokens move.
function homeCircle(slots: [number, number][]) {
  const cs = slots.map(([c]) => c);
  const rs = slots.map(([, r]) => r);
  const cx = (Math.min(...cs) + Math.max(...cs)) / 2 + 0.5;
  const cy = (Math.min(...rs) + Math.max(...rs)) / 2 + 0.5;
  const reach = Math.max(
    ...slots.map(([c, r]) => Math.hypot(c + 0.5 - cx, r + 0.5 - cy))
  );
  return { cx: cx * CELL, cy: cy * CELL, r: (reach + TOKEN_R + HOME_PAD) * CELL };
}

const SAFE = new Set([0, 8, 13, 21, 26, 34, 39, 47]);
const START_ABS = new Set(Object.values(START_OFFSET));

const px = (c: number, r: number): [number, number] => [(c + 0.5) * CELL, (r + 0.5) * CELL];

// five-point star as an SVG polygon (no emoji / font glyphs)
function starPts(cx: number, cy: number, R: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 10; i++) {
    const ang = -Math.PI / 2 + (i * Math.PI) / 5;
    const rad = i % 2 === 0 ? R : R * 0.46;
    pts.push(`${cx + rad * Math.cos(ang)},${cy + rad * Math.sin(ang)}`);
  }
  return pts.join(" ");
}

function tokenCell(color: string, prog: number, ti: number): [number, number] {
  if (prog < 0) return YARD[color].slots[ti];
  if (prog <= 50) return TRACK[(START_OFFSET[color] + prog) % 52];
  if (prog >= 56) return HOME_SLOT[color];
  return HOME_COL[color][prog - 51];
}

export default function Board({
  state,
  legal,
  mySeat,
  myColor,
  onMove,
}: {
  state: GameState;
  legal: LegalMove[];
  mySeat: number | null;
  myColor: string | null;
  onMove: (tokenIndex: number) => void;
}) {
  const movable = new Set(legal.map((m) => m.token_index));
  const myTurn = mySeat !== null && state.current === mySeat && state.phase === "move";
  const moverColor = state.players[state.current]?.color ?? "RED";
  const k = myColor ? ROT_K[myColor] ?? 0 : 0;
  const rot = k * 90;

  // tokens flattened, grouped by cell so stacks spread
  type Tok = { seat: number; ti: number; color: string; prog: number; canMove: boolean };
  const toks: Tok[] = [];
  state.players.forEach((p, seat) => {
    p.tokens.forEach((prog, ti) => {
      toks.push({
        seat, ti, color: p.color, prog,
        canMove: seat === mySeat && myTurn && movable.has(ti),
      });
    });
  });
  const keyOf = (t: Tok) => tokenCell(t.color, t.prog, t.ti).join(",");
  const groups = new Map<string, Tok[]>();
  toks.forEach((t) => (groups.get(keyOf(t)) ?? groups.set(keyOf(t), []).get(keyOf(t))!).push(t));

  // destination markers for the current mover's legal moves
  const dests = myTurn
    ? legal.map((m) => {
        const [c, r] = tokenCell(moverColor, m.dst, m.token_index);
        return { c, r, capture: m.captures.length > 0, dst: m.dst };
      })
    : [];

  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      width="100%"
      style={{ maxWidth: 440, display: "block", margin: "0 auto", borderRadius: 16 }}
    >
      <rect x={0} y={0} width={SIZE} height={SIZE} rx={16} fill={BOARD_BG} />

      <g transform={`rotate(${rot} ${MID} ${MID})`}>
        {/* yards */}
        {Object.entries(YARD).map(([col, y]) => (
          <g key={col}>
            <rect
              x={(y.ox + YARD_INSET) * CELL}
              y={(y.oy + YARD_INSET) * CELL}
              width={(6 - 2 * YARD_INSET) * CELL}
              height={(6 - 2 * YARD_INSET) * CELL}
              rx={12}
              fill={YARD_BG}
              stroke={COLORS[col]}
              strokeWidth={YARD_EDGE}
            />
            {/* the home ring — carries the colour, encircling the four tokens */}
            <circle
              {...homeCircle(y.slots)}
              fill="none"
              stroke={COLORS[col]}
              strokeWidth={HOME_RING * CELL}
            />
            {y.slots.map(([c, r], i) => {
              const [cx, cy] = px(c, r);
              return <circle key={i} cx={cx} cy={cy} r={CELL * TOKEN_R} fill={COLOR_SOFT[col]} stroke={COLORS[col]} strokeWidth={2} />;
            })}
          </g>
        ))}

        {/* track cells */}
        {TRACK.map(([c, r], abs) => {
          const startColor = Object.entries(START_OFFSET).find(([, o]) => o === abs)?.[0];
          return (
            <rect
              key={abs}
              x={c * CELL} y={r * CELL} width={CELL} height={CELL}
              fill={START_ABS.has(abs) && startColor ? COLOR_SOFT[startColor] : "#ffffff"}
              stroke="#d6d9e0" strokeWidth={1}
            />
          );
        })}

        {/* coloured home columns */}
        {Object.entries(HOME_COL).map(([col, cells]) =>
          cells.map(([c, r], i) => (
            <rect key={`${col}-${i}`} x={c * CELL} y={r * CELL} width={CELL} height={CELL} fill={COLORS[col]} stroke="#ffffff" strokeWidth={1} />
          ))
        )}

        {/* safe stars — drawn as SVG polygons (no emoji), upright regardless of rotation */}
        {[...SAFE].map((abs) => {
          const [c, r] = TRACK[abs];
          const [x, y] = px(c, r);
          return (
            <polygon
              key={abs}
              points={starPts(x, y, CELL * 0.28)}
              fill="#aeb4c0"
              transform={`rotate(${-rot} ${x} ${y})`}
            />
          );
        })}

        {/* centre home — four triangles meeting exactly at the board centre, with a
            clean central disc so the finish reads unmistakably as the middle */}
        <rect x={6 * CELL} y={6 * CELL} width={3 * CELL} height={3 * CELL} fill="#ffffff" />
        {(() => {
          const L = 6 * CELL, T = 6 * CELL, R = 9 * CELL, B = 9 * CELL, cx = MID, cy = MID;
          const tris: [string, string][] = [
            ["GREEN", `${L},${T} ${R},${T} ${cx},${cy}`],
            ["YELLOW", `${R},${T} ${R},${B} ${cx},${cy}`],
            ["BLUE", `${R},${B} ${L},${B} ${cx},${cy}`],
            ["RED", `${L},${B} ${L},${T} ${cx},${cy}`],
          ];
          return tris.map(([col, pts]) => (
            <polygon key={col} points={pts} fill={COLORS[col]} stroke="#ffffff" strokeWidth={1} />
          ));
        })()}
        <circle cx={MID} cy={MID} r={CELL * 0.5} fill="#ffffff" />
        <circle cx={MID} cy={MID} r={CELL * 0.5} fill="none" stroke="#c8ccd4" strokeWidth={1.5} />
        <circle cx={MID} cy={MID} r={CELL * 0.18} fill="#c8ccd4" />

        {/* move-destination markers (white inside the same-coloured home column) */}
        {dests.map((d, i) => {
          const [x, y] = px(d.c, d.r);
          const col = d.capture
            ? "#e5484d"
            : d.dst >= 56
              ? "#0e1320"
              : d.dst >= 51
                ? "#ffffff"
                : COLORS[moverColor];
          return (
            <g key={`d-${i}`} pointerEvents="none">
              <circle cx={x} cy={y} r={CELL * 0.4} fill="none" stroke={col} strokeWidth={2.5} strokeDasharray="4 3">
                <animateTransform attributeName="transform" type="rotate" from={`0 ${x} ${y}`} to={`360 ${x} ${y}`} dur="6s" repeatCount="indefinite" />
              </circle>
              <circle cx={x} cy={y} r={CELL * 0.12} fill={col}>
                <animate attributeName="opacity" values="1;0.3;1" dur="1s" repeatCount="indefinite" />
              </circle>
            </g>
          );
        })}

        {/* tokens — stable key per (seat,ti) so CSS transforms animate the glide */}
        {toks.map((t) => {
          const key = keyOf(t);
          const grp = groups.get(key)!;
          const idx = grp.indexOf(t);
          const n = grp.length;
          const [bx, by] = px(...tokenCell(t.color, t.prog, t.ti));
          let ox = 0, oy = 0;
          if (n > 1) {
            const ang = (idx / n) * Math.PI * 2 - Math.PI / 2;
            const rad = CELL * 0.2;
            ox = Math.cos(ang) * rad;
            oy = Math.sin(ang) * rad;
          }
          const r = CELL * (n > 1 ? 0.24 : 0.32);
          return (
            <g
              key={`${t.seat}-${t.ti}`}
              onClick={t.canMove ? () => onMove(t.ti) : undefined}
              style={{
                transform: `translate(${bx + ox}px, ${by + oy}px)`,
                transition: "transform 380ms cubic-bezier(0.34,1.2,0.64,1)",
                cursor: t.canMove ? "pointer" : "default",
              }}
            >
              <circle cx={0} cy={0} r={r + 1.5} fill="#00000022" />
              <circle cx={0} cy={0} r={r} fill={COLORS[t.color]} stroke={t.canMove ? "#111827" : "#ffffff"} strokeWidth={t.canMove ? 2.5 : 2} />
              <circle cx={0} cy={-r * 0.28} r={r * 0.32} fill="#ffffff" opacity={0.6} />
              {t.canMove && (
                <circle cx={0} cy={0} r={r + 4} fill="none" stroke={COLORS[t.color]} strokeWidth={2.5}>
                  <animate attributeName="r" values={`${r + 3};${r + 8};${r + 3}`} dur="1s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.9;0.15;0.9" dur="1s" repeatCount="indefinite" />
                </circle>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
