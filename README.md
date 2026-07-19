# 🎲 Ludo Board — Telegram Mini App

A multiplayer Ludo game for Telegram, built on the same proven architecture as the
[Poker CM](../poker-telegram-bot) app: a **FastAPI + async SQLAlchemy + Postgres** backend
running an **aiogram** bot in-process, a **pure Python game engine** validated by headless
simulation, and a **Next.js static-export** Mini App served by nginx. Real-time play runs
over a per-match WebSocket.

> **New here / picking this up in a fresh session?** Start with
> [`docs/CONTINUE_HERE.md`](docs/CONTINUE_HERE.md) — it is the handoff doc: what exists,
> what's stubbed, how to run it, and exactly what to build next.

## What works today (the base)

- ✅ **Pure Ludo engine** (`backend/app/ludo/`) — board geometry, dice, legal moves,
  capture, safe squares, extra turns, triple-six forfeit, win/placement. Fully covered by
  `app/tests/test_engine.py` (unit tests + 300 fuzzed full games).
- ✅ **Backend**: Telegram auth (initData → JWT), profile, match create/join/list,
  live-match WebSocket, a heuristic bot, self-play lobby-filler tables, aiogram bot
  (/start /play /help) wired to the Ludo bot token.
- ✅ **Frontend**: Mini App shell — auth, lobby (quick-play vs bots / create / join), and a
  live board that renders server state and lets you roll & move.
- ✅ **Infra**: Docker Compose (db + backend + nginx), Alembic migration, entrypoint,
  atomic web-deploy script, `.env.example` pre-filled with the Ludo bot token.

## Layout

```
ludo-telegram/
├── backend/            FastAPI app, pure engine, bot, Alembic
│   └── app/
│       ├── ludo/       PURE game engine (no I/O) — the heart
│       ├── game/       operational runtime: WS hub, runtime loop, manager
│       ├── models/     users, matches, seats
│       ├── api/        auth, profile, matches, websocket
│       └── bot/        aiogram handlers + runner
├── web/                Next.js static-export Mini App
├── nginx/              reverse-proxy config
├── deploy/             atomic web publish script
├── docs/               ← full documentation (read these)
├── docker-compose.yml
└── .env.example
```

## Quick start (local)

```bash
cp .env.example .env        # token is pre-filled; set SECRET_KEY
docker compose up -d --build
# backend on :8000 (internal), Mini App on 127.0.0.1:8081 via nginx
cd backend && python -m app.tests.test_engine   # run the engine tests
```

See [`docs/SERVER_SETUP.md`](docs/SERVER_SETUP.md) for production (Cloudflare Tunnel, HTTPS,
BotFather Mini App setup) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how it all
fits together.
