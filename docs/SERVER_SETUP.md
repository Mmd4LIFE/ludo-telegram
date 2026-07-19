# 🖥️ Server Setup & Deploy

Written to be **server-agnostic** — the user plans to move Ludo to a new box separate from
the poker server. Anywhere with Docker + a way to expose HTTPS works.

## Prerequisites

- A Linux host with **Docker + Docker Compose v2**.
- A public **HTTPS** URL pointing at the box (Telegram Mini Apps require HTTPS). Options:
  - **Cloudflare Tunnel** (what poker uses — no open ports, free TLS), or
  - a reverse proxy (Caddy/nginx/Traefik) terminating TLS in front of `WEB_PORT`.
- The Ludo bot created in **@BotFather** (token already in `.env.example`).

## 1. First-time server bootstrap

```bash
# on the server
git clone https://github.com/Mmd4LIFE/ludo-telegram.git ~/mk-projects/ludo
cd ~/mk-projects/ludo
cp .env.example .env
# EDIT .env:
#   SECRET_KEY   -> a long random string
#   PUBLIC_URL / WEBAPP_URL -> your https domain
#   WEB_PORT     -> a free localhost port (poker uses 8080; pick e.g. 8081)
#   ADMIN_IDS    -> 592354162
#   BOT_TOKEN is already the Ludo token
```

### Build the web bundle once (needed before nginx has anything to serve)

The nginx container serves `./webout`. Produce it from the Next.js export:

```bash
cd web && npm install && npm run build
# copy the export into the served dir
mkdir -p ../webout && cp -a out/. ../webout/
cd ..
```

### Bring it up

```bash
docker compose up -d --build
# db + backend (migrations + seed run automatically) + nginx on 127.0.0.1:${WEB_PORT}
docker compose logs -f backend    # watch for "Bot polling started"
```

## 2. Expose HTTPS

### Option A — Cloudflare Tunnel (recommended, matches poker)

```bash
cloudflared tunnel create ludo
# route your hostname to http://127.0.0.1:${WEB_PORT}
cloudflared tunnel route dns ludo ludo.yourdomain.com
# config.yml: ingress -> service: http://127.0.0.1:8081
cloudflared tunnel run ludo      # or install as a systemd service
```

### Option B — Caddy (auto-TLS)

```
ludo.yourdomain.com {
    reverse_proxy 127.0.0.1:8081
}
```

## 3. Point the bot at the Mini App

Either automatically (the bot sets its menu button to `WEBAPP_URL` on startup — see
`bot/runner.py`) or in **@BotFather → Bot Settings → Menu Button / Web App URL**. For
`BOT_MODE=webhook`, also ensure `PUBLIC_URL` is reachable and set `WEBHOOK_SECRET`.

- `BOT_MODE=polling` (default): simplest, no inbound webhook needed.
- `BOT_MODE=webhook`: Telegram POSTs to `PUBLIC_URL/webhook/<WEBHOOK_SECRET>` (nginx proxies
  `/webhook/` to the backend).

## 4. Routine deploy (after the first time)

Same flow as poker — build web locally or on the box, publish atomically, rebuild backend:

```bash
# on the server
cd ~/mk-projects/ludo
git pull

# rebuild the web export and publish it atomically (no empty-dir window)
cd web && npm run build && tar czf /tmp/ludo-webout.tgz -C out . && cd ..
KEEP_DAYS=7 sh deploy/deploy-web.sh "$PWD" /tmp/ludo-webout.tgz

# rebuild backend (entrypoint runs alembic upgrade + seed on boot)
docker compose up -d --build backend
```

`deploy/deploy-web.sh` overlays the new build, swaps entry files with atomic renames, and
only retires old content-hashed chunks after `KEEP_DAYS` — so open Mini App sessions never
404 mid-deploy (Telegram's webview caches HTML aggressively).

## 5. Migrations

- The backend entrypoint runs `alembic upgrade head` on every boot.
- New migration: `docker compose exec backend alembic revision -m "..."`, edit it, keep it
  **additive and idempotent** (`IF EXISTS`/`IF NOT EXISTS`), and **never** write one that
  can drop production data. Verify against a copy before deploying.

## 6. Resource notes (1GB box)

- Keep `BOT_TABLES` low (1–2). Self-play tables throttle to one step per
  `BOT_TABLE_IDLE_SECONDS` when nobody is watching.
- Postgres + backend + nginx + other bots share the RAM. Watch `docker stats` after
  raising any table/bot count.

## 7. Health & smoke test

```bash
curl -s http://127.0.0.1:8081/api/health      # {"status":"ok","service":"ludo-board",...}
```

Open the Mini App from the bot, hit **Play vs bots**, and confirm the die rolls and tokens
move. `docker compose logs -f backend` shows match lifecycle lines.
