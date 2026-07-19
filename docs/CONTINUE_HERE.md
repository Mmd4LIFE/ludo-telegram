# 🧭 Continue Here — handoff for the next session

This project was scaffolded in one session to be a **professional, working base** for a
Telegram Ludo game, mirroring the architecture of the sibling poker app
(`../poker-telegram-bot`). This doc is the single source of truth for *what exists, what's
stubbed, and what to build next*. Read it first.

---

## 0. Facts you need

| Thing | Value |
|---|---|
| Git remote | `https://github.com/Mmd4LIFE/ludo-telegram.git` |
| Ludo bot token | `8904921147:AAEB5avl6EuOsdrXmv0fwqntVIc909-U3B0` (in `.env.example`) |
| Admin Telegram id | `592354162` (@mmdsvm) |
| Sibling app to copy patterns from | `../poker-telegram-bot` |
| Server (poker box, Ludo will move to a new server soon) | root@104.194.144.46 |

> The user said they will **change the poker server soon** and deploy Ludo separately.
> Don't hard-wire a server yet — `docs/SERVER_SETUP.md` is written to be server-agnostic.

**Privacy rule (inherited, non-negotiable):** `telegram_id` is PRIVATE and must never
appear in any API response, WebSocket payload, or log shown to a user. `username` is shown
only to a player's friends. The schemas already enforce this — keep it that way.

---

## 1. What is DONE and verified

- **Pure engine** `backend/app/ludo/` — the whole rulebook, no I/O. Verified:
  `cd backend && python -m app.tests.test_engine` → unit tests + **300 fuzzed full games**
  all pass. This is the part you can trust and build on without fear.
- **Backend imports + wires cleanly** — all routes register, all tables build (verified in
  a throwaway venv). REST: auth (telegram + dev), profile, matches (create/join/list/get).
  WS: `/ws/match/{code}`. Bot: /start /play /help. Self-play bot tables via the janitor.
- **Frontend builds + static-exports** — `cd web && npm install && npm run build` → `out/`
  with `index.html`. Lobby + live board render server state; roll & move work over WS.
- **Infra** — `docker-compose.yml`, `backend/Dockerfile`, `backend/entrypoint.sh` (waits
  for db, `alembic upgrade head`, seed, uvicorn), `alembic/versions/0001_initial.py`,
  `nginx/nginx.conf`, `deploy/deploy-web.sh`, `.env.example` (token pre-filled).

## 2. What is STUBBED / intentionally minimal (your job)

These are deliberately thin so you can extend without unpicking anything:

- **`app/game/runtime.py`** drives one match end-to-end but has **no reconnect grace,
  no entry-fee escrow, and no move animation timing beyond a simple think-delay**. The
  turn loop is correct; enrich it.
- **`app/seed.py`** is a no-op (just logs user count). Add house-bot user rows, cosmetics
  catalog, daily rewards seed.
- **Economy** is `coins` only on the user; matches can carry an `entry_fee` that is paid to
  the winner in `_settle()`, but there is **no escrow at join** yet (a player could spend
  the coins elsewhere before settling). Add escrow when you add stakes for real.
- **Frontend** is a single `page.tsx`. No profile/shop/friends/league tabs, no proper
  router, no design system. The poker app's `web/` is your reference for all of these.
- **No tests on the operational layer** (runtime/manager/routes). The pure engine is
  tested; the async plumbing is not.

## 3. Recommended build order (ported from what made poker good)

1. **Reconnect + seat grace** in the runtime (poker: `IDLE_SEAT_GRACE_SECONDS`, janitor
   reaps disconnected seats). A Ludo turn should auto-play for a disconnected human and
   hold their seat for the grace window.
2. **Match lobby polish** — waiting room UI (who's seated, ready-up, start button for the
   host), then auto-start when full. Right now a friend match only "starts" once someone
   opens the WS; add an explicit start.
3. **Profile + economy tabs** — port poker's coins/level/xp UI, daily reward, referral
   share sheet (deep link `?start=ref-<user_id>` is already handled server-side).
4. **Cosmetics** — token skins & board themes (poker has a full cards/cosmetics/market
   system to copy the shape of). The board renderer already isolates colour/skin.
5. **League / ranking** — poker's league (seasons, cohorts, LP, DQ/Skill score) is a rich
   template; Ludo win-rate + placement maps onto it cleanly.
6. **Analytics** — poker has an `analytics` Postgres schema (fact tables + views) and an
   in-app data explorer. Port when there's data worth exploring.
7. **Classic cross board skin** — the current board is a correct *ring*; a 15×15 cross is a
   pure-visual upgrade (see `docs/ENGINE.md` §Rendering).

The full feature list with poker parity notes is in [`ROADMAP.md`](ROADMAP.md).

## 4. How to run it

```bash
# engine tests (no deps)
cd backend && python -m app.tests.test_engine

# full stack
cp .env.example .env         # set SECRET_KEY; token already filled
docker compose up -d --build
# Mini App: http://127.0.0.1:8081  (put a tunnel / HTTPS in front for Telegram)

# frontend dev
cd web && npm install && npm run dev      # http://localhost:3000 (uses /api/auth/dev)
# frontend prod build
cd web && npm run build                   # -> web/out (tar this to webout on the server)
```

Deploy flow (same as poker): push → on server `git pull` → build web → `tar` → run
`deploy/deploy-web.sh` → `docker compose up -d --build backend`. Details in
`docs/SERVER_SETUP.md`.

## 5. Gotchas carried over from poker

- **aiogram runs in the FastAPI lifespan** (one event loop) — don't spawn a second loop.
- **Static export**: `web/AGENTS.md` warns this Next.js (16.x) differs from older docs —
  read `web/node_modules/next/dist/docs/` before non-trivial frontend work.
- **Single-file bind mounts** (if you add a CHANGELOG mount like poker) need
  `docker compose up -d --force-recreate` after a host edit (inode gotcha).
- **1GB box, shared with other bots** — keep `BOT_TABLES` small; the runtime already
  throttles self-play tables when nobody is watching.
- Engine state is stored as JSON in `matches.state`; it round-trips via
  `GameState.to_dict()/from_dict()`. Any engine field you add must be in both.
