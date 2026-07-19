# Ludo Board — Mini App (web)

Next.js **static export** served by nginx (no Node runtime in production), same model as
the poker app.

```bash
npm install
npm run dev     # http://localhost:3000 — uses POST /api/auth/dev (needs backend running)
npm run build   # -> ./out (static export). tar this into the server's webout dir.
```

## ⚠️ Read before non-trivial changes
This Next.js (16.x) has breaking changes vs older docs. See `AGENTS.md` — read
`node_modules/next/dist/docs/` before writing framework code.

## Files
- `lib/api.ts` — REST client + JWT (auth, profile, matches).
- `lib/telegram.ts` — Telegram WebApp SDK bridge (initData, expand, haptics).
- `lib/ws.ts` — live-match WebSocket client + typed server payloads.
- `components/board.tsx` — SVG ring board (mirrors `backend/app/ludo/board.py` constants).
- `app/page.tsx` — the whole app today: auth → lobby → live match. Split this next.

## Notes
- No design system yet (plain CSS in `app/globals.css`). The poker `web/` uses
  Tailwind v4 + shadcn/base-ui — adopt that when you build out the tabs.
- In a plain browser (no Telegram) it dev-logs via `/api/auth/dev`, which the backend
  refuses when `ENV=production`.
