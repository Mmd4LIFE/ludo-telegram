# 🔌 API Reference (base)

Base URL is same-origin (nginx proxies `/api` and `/ws` to the backend). All REST responses
are JSON. Auth is a Bearer JWT from the auth endpoints. **No response ever contains
`telegram_id`.**

## REST

### `POST /api/auth/telegram`
Body: `{ "init_data": "<Telegram WebApp initData>" }`
→ `{ "token": "<jwt>", "user": UserProfile }`. Validates the initData HMAC, upserts the
user, applies a `?start=ref-<id>` referral once.

### `POST /api/auth/dev`  *(non-production only)*
Body: `{ "telegram_id": 111, "first_name": "Dev", "username": "dev" }` → same shape.
Returns 403 when `ENV=production`.

### `GET /api/profile/me`  *(auth)*
→ `UserProfile { id, first_name, username, coins, level, xp, games_played, games_won }`.

### `POST /api/matches`  *(auth)*
Body: `{ max_players?: 2..4, is_public?: bool, entry_fee?: int, fill_with_bots?: bool }`
→ `MatchSummary`. Seats you on RED (seat 0). `fill_with_bots` seats bots and starts.

### `POST /api/matches/join`  *(auth)*
Body: `{ code }` → `MatchSummary`. Idempotent if already seated; 409 if full/closed.

### `GET /api/matches`  *(auth)* → `MatchSummary[]` (public, waiting rooms).
### `GET /api/matches/{code}`  *(auth)* → `MatchSummary`.

`MatchSummary = { code, status, max_players, seated, is_public, entry_fee }`.

### `GET /api/health` → `{ status, service, version }`.

## WebSocket — `/ws/match/{code}?token=<jwt>`

On connect the server pushes the current state, then streams updates. All rules are
enforced server-side; the client is a renderer. Out-of-turn/stale messages are ignored.

**Client → server**
- `{ "type": "roll" }` — roll the die (only on your turn, ROLL phase).
- `{ "type": "move", "token_index": 0 }` — move one of your tokens (MOVE phase).
- `{ "type": "ping" }` → `{ "type": "pong" }`.
- `{ "type": "sync" }` — request a fresh state snapshot.
- `{ "type": "emote", "emote": "fire" }` — broadcast a reaction (whitelisted).

**Server → client**
- `{ "type": "state", "code", "state": GameState, "seat_user": {seat: user_id|null},
     "legal_moves": LegalMove[] }`
- `{ "type": "emote", "user_id", "emote" }`
- `{ "type": "pong" }`

`GameState = { players: [{ color, tokens: number[], finished_at }], current, phase,
die, consecutive_sixes, turn, ranking }`. Token `progress`: `-1` base, `0..50` ring,
`51..56` home column, `56` HOME. See `docs/ENGINE.md`.
