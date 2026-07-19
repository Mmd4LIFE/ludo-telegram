# 🧩 The Pure Engine

`backend/app/ludo/` is a **pure, deterministic state machine** with no I/O — no DB, no
network, no async, no Telegram. Given a state and a die, it tells you the legal moves and
the result of applying one. That purity is why it can be exhaustively fuzz-tested and
reused verbatim by the server runtime and the bots.

## Files

| File | Responsibility |
|---|---|
| `board.py` | Geometry: the coordinate model, `START_OFFSET`, safe squares, `absolute_square()` |
| `state.py` | Data containers: `GameState`, `PlayerState`, `Move`, `Phase` (+ `to_dict`/`from_dict`) |
| `rules.py` | The rules: `initial_state`, `roll_die`, `register_roll`, `legal_moves`, `apply_move` |
| `bots.py` | `choose_move()` — the heuristic house bot |

## The coordinate model (read this once)

Every token has a single integer **`progress`**:

```
BASE (-1)      in the yard, not yet in play
0 .. 50        on the shared 52-cell ring, RELATIVE to the token's own start
51 .. 56       in that colour's private 6-cell home column
HOME (= 56)    finished
```

- A **released token's journey is exactly `0 → 56`** (56 steps). To leave BASE you roll a
  6, landing on `progress 0` (your own start cell).
- The **absolute** ring cell (0..51, shared by all players) is
  `absolute_square(color, progress) = (START_OFFSET[color] + progress) % 52`, valid only
  while `0 ≤ progress ≤ 50`. At `progress ≥ 51` the token is in its private column and has
  **no absolute cell** (`absolute_square` returns `None`) — so it can never collide, hence
  no capture in the home column.

This one function is the whole reason the engine is simple: **captures are just "same
absolute cell, different colour, not safe."**

## State transitions

```
        register_roll(die)                 apply_move(move)
 ROLL ────────────────────────►  MOVE  ─────────────────────►  ROLL (same or next player)
   ▲   (triple-6 → skip to next)    │   (no legal move → pass)      │
   │                                └──────────────────────────────┘
   └──────────────  extra turn (6 / capture / home)  ◄─────────────┘
                                                                 └─► FINISHED
```

- `register_roll` records the die, increments the six-streak, and on the **third six**
  discards the turn and advances the player. Otherwise it flips ROLL → MOVE.
- `legal_moves` returns `list[Move]`; each `Move` is pre-annotated with
  `releases_from_base`, `reaches_home`, and the exact `captures` it triggers, so the UI and
  bots rank moves without re-deriving anything.
- `apply_move(state, move)` mutates the state, sends captured tokens home, records finishes
  in `ranking`, grants the extra turn if earned, and either loops back to the same player
  (ROLL) or advances. Passing (`move=None`) is only legal when there are no legal moves.

## Determinism & testing

- All randomness goes through an injectable `random.Random`. Pass a seeded RNG (or a
  scripted die sequence) and games are fully reproducible.
- `app/tests/test_engine.py` asserts geometry, release-on-6, extra turns, the triple-six
  forfeit, capture + safe squares, exact-finish, and **fuzzes 300 complete bot-driven
  games**, checking each terminates with a full, distinct ranking and never reaches an
  illegal state. Run: `cd backend && python -m app.tests.test_engine`.

## Serialization

`GameState.to_dict()` / `from_dict()` round-trip the whole game to plain JSON. This is what
`matches.state` (JSONB) stores and what the WebSocket sends. **Any field you add to the
engine state must be handled in both** or a resume/reload will silently drop it.

## Rendering (frontend)

The board coordinate math lives once in `web/components/board.tsx`, mirroring
`board.py`'s constants (`START_OFFSET`, `SAFE`, `HOME`). The current skin lays the 52 ring
cells **evenly around a circle** and draws each home column as an inward spoke — this is
*correct by construction* and avoids fragile 15×15-cross pixel math. A classic cross skin
is a pure-visual swap: provide a new `progress → (x, y)` mapping and the rest is unchanged.

## Extending the engine safely

- Add rules as pure functions of `(state, die)`; never reach for I/O here.
- Keep `Move` annotations complete so callers stay dumb.
- Add a test in `test_engine.py` (and, ideally, a targeted assertion inside the fuzz loop)
  for every rule you touch. The fuzz harness is your regression net.
