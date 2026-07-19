# 🏛️ Architecture

Ludo Board reuses the poker app's proven shape. If you know that codebase, this will feel
familiar by design.

## The pieces

```
                         Telegram
                            │
              ┌─────────────┴──────────────┐
        Mini App (webview)            Bot (BotFather)
              │                            │  polling/webhook
        HTTPS │ /api  /ws                  │
              ▼                            ▼
   ┌────────────────── nginx ──────────────────┐
   │  /            -> static Next.js export     │
   │  /api/, /ws/  -> proxy to backend:8000     │
   └───────────────────┬────────────────────────┘
                       ▼
              FastAPI (uvicorn) ── aiogram bot (same event loop, lifespan)
                       │
          ┌────────────┼───────────────────────────┐
          ▼            ▼                             ▼
   REST routes    WebSocket /ws/match/{code}   MatchManager (janitor)
   (auth, profile,      │                       + self-play bot tables
    matches)            ▼
                 MatchRuntime (one per live match)
                        │  drives ↓, renders → ConnectionHub → sockets
                        ▼
                 pure engine  app.ludo  ── no I/O, deterministic
                        │
                        ▼  snapshots
                    Postgres (users, matches, match_seats)
```

## Backend layers

- **`app/ludo/`** — pure engine (see `ENGINE.md`). Knows nothing about the server.
- **`app/game/`** — the operational layer that *drives* the engine:
  - `connection.py` — `ConnectionHub`: per-match socket registry + personalised fan-out.
  - `runtime.py` — `MatchRuntime`: one async task per match; the roll/move loop, bot &
    timeout auto-play, per-viewer render, DB snapshots, settlement.
  - `manager.py` — `MatchManager` (singleton): runtime registry, action routing, a janitor
    that reaps finished matches and keeps `BOT_TABLES` self-play tables alive.
- **`app/api/`** — FastAPI routers: `routes_auth`, `routes_profile`, `routes_matches`,
  `routes_ws`. `deps.py` has `get_current_user` (JWT) and `require_admin`.
- **`app/bot/`** — aiogram `instance` (Bot/Dispatcher singletons), `handlers` (/start,
  /play, /help), `runner` (registers handlers, sets menu button, polling or webhook).
- **`app/models/`** — SQLAlchemy 2.0 models: `User`, `Match`, `MatchSeat`.
- **`app/core/security.py`** — Telegram initData HMAC validation + JWT mint/verify.
- **`app/config.py`** — env-driven `Settings` (pydantic-settings).
- **`app/main.py`** — FastAPI app + lifespan (starts bot, janitor; graceful shutdown).

## Request → game flow

1. Mini App loads, calls `POST /api/auth/telegram` with Telegram `initData`.
   Server validates the HMAC, upserts the `User`, returns a 7-day JWT.
2. Player creates/joins a match (`/api/matches…`) → rows in `matches` + `match_seats`.
3. Player opens `wss://…/ws/match/{code}?token=<jwt>`. The route resolves the match, gets
   (or creates) its `MatchRuntime`, registers the socket, and pushes current state.
4. The runtime's async loop advances the game: it waits for the seated human's
   `roll`/`move` (with a timeout), or auto-plays for bots / disconnected / timed-out
   players via the pure engine, then broadcasts the new state to all viewers.
5. On game over the runtime writes placements, pays the pot, bumps stats, and the janitor
   drops the runtime from memory.

## Why a runtime task per match

Ludo is turn-based but real-time-feeling: bots "think", humans have a clock, and everyone
should see the same board instantly. A single owning async task per match keeps the
game-state authority in one place (no lock dance over shared mutable state) and makes
timeouts/animation pacing trivial (`await asyncio.sleep`). The DB is the durable snapshot,
not the live authority — the runtime is.

## Data model

- **`users`** — one row per Telegram user & per house bot. `telegram_id` is PRIVATE.
  Soft currency (`coins`), progression (`level`, `xp`, `games_played/won`), presence,
  referral graph.
- **`matches`** — a room. `code` (deep-link), `status`, `max_players`, `entry_fee`,
  `is_bot_table`, and `state` (JSONB serialised `GameState`).
- **`match_seats`** — seat index → colour → user (or bot). `place` holds final placement.

## Frontend

Next.js **static export** (`output: "export"`) — no Node runtime in prod, nginx serves the
files. `lib/api.ts` (REST + JWT), `lib/telegram.ts` (WebApp SDK bridge), `lib/ws.ts`
(match socket + typed payloads), `components/board.tsx` (SVG ring board), `app/page.tsx`
(lobby + live match). The whole thing is one screen today — see `ROADMAP.md` for the split.

## Conventions inherited from poker (keep them)

- `telegram_id` never leaves the server; `username` is friends-only.
- All game rules live in the pure engine; the server never re-implements one.
- Migrations are additive/idempotent and must not lose production data.
- Small footprint: this runs on a 1GB box shared with other bots.
