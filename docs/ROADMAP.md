# 🗺️ Feature Roadmap (with poker-parity notes)

The poker app (`../poker-telegram-bot`) is a mature template. This roadmap ports its best
ideas to Ludo, ordered roughly by value/effort. Each item notes where to steal from.

Legend: ✅ done · 🟡 partial/stub · ⬜ not started

## Core gameplay
- ✅ Pure engine (rules, capture, safe squares, extra turns, triple-six, win/placement)
- ✅ Heuristic bot (`ludo/bots.py`)
- ✅ Live match over WebSocket, bot & timeout auto-play
- 🟡 **Reconnect + seat grace** — hold a disconnected human's seat for
  `IDLE_SEAT_GRACE_SECONDS`, auto-play meanwhile. *(poker: `game/manager.py` janitor +
  seat reaping.)*
- ⬜ **Explicit lobby / ready-up** — waiting room, host "Start", auto-start when full.
  *(poker: rooms + seats flow.)*
- ⬜ **Rematch** button that clones seats into a fresh match.
- ⬜ **Stronger bot** — expectiminimax over the die distribution; difficulty tiers.
- ⬜ **Classic 15×15 cross board skin** (pure-visual; see `ENGINE.md` §Rendering).
- ⬜ **Blockade-blocks-passage** variant toggle (`rules._captures_at`).

## Identity, economy & progression
- ✅ Telegram auth (initData → JWT), coins, level/xp, referral graph
- 🟡 **Entry-fee escrow** — currently paid to winner at settle with no escrow at join; add
  hold-at-join so coins can't be double-spent. *(poker: rooms buy-in/escrow.)*
- ⬜ **Daily reward** + streaks. *(poker: `services/daily.py`.)*
- ⬜ **Referral share sheet** in-app (deep link `?start=ref-<id>` already handled server-side).
- ⬜ **Shop / Stars & TON top-ups** for coins. *(poker: `services/payments.py`, `routes_shop`.)*
- ⬜ **Profile tab** (avatar, stats, match history).

## Cosmetics
- ⬜ **Token skins & board themes** — the board renderer isolates colour → swap per-skin.
  *(poker: full cards/cosmetics/market — copy the catalog + equip + market shape.)*
- ⬜ **Loot boxes / collection** if you want the poker-style meta.

## Social
- ⬜ **Friends** (add, online status, invite to a room). *(poker: `services/friends.py`,
  `routes_friends`, friends-only username exposure — reuse the privacy model exactly.)*
- ⬜ **Emotes at the table** — WS `emote` is already wired server-side; add the UI + skins.
- ⬜ **Clubs** (poker's Squads→Clubs) — group play, club leaderboards. *(poker: `models/club.py`,
  `routes_clubs`, PRD in `docs/prd/clubs.md`.)*

## Competitive
- ⬜ **League / seasons** — LP, cohorts, promotion/relegation, placement scoring. Ludo maps
  cleanly onto poker's league (win-rate + average placement instead of chip skill).
  *(poker: `services/league.py`, `league_score.py`, `dq.py`; anti-farming lessons apply.)*
- ⬜ **Global leaderboards** (wins, win-rate, longest streak).

## Ops & insight
- ⬜ **Admin panel** — match monitor, bot monitor, user tools. *(poker: `routes_admin`,
  `components/screens/admin*`.)*
- ⬜ **Analytics schema** — a dedicated `analytics` Postgres schema (fact tables + views)
  written by a daily janitor, plus an in-app data explorer. *(poker: `models/analytics.py`,
  `alembic 0029/0030`, `routes_explorer`, `admin-data.tsx`.)*
- ⬜ **Changelog** surfaced in-app (player-facing, no internal/tech references — a poker
  lesson learned the hard way).
- ⬜ **Notifications** — "your turn" / "you were captured" pushes via the bot.
  *(poker: `services/notify.py` reminder loop.)*

## Frontend structure
- 🟡 Single-screen `page.tsx` → split into tabbed screens with a real router, a design
  system, and shared components. *(poker `web/` is the reference implementation.)*
- ⬜ Board animations (token slide, capture bounce, dice roll), haptics (partly wired),
  sound.

## Testing & quality
- ✅ Pure-engine unit + fuzz tests
- ⬜ Operational tests (runtime turn loop, timeout/auto-play, settle payouts)
- ⬜ A headless "match simulator" CLI that plays N bot matches through the *runtime* (not
  just the engine) to shake out async bugs.

---

### Suggested first sprint for the next session
1. Reconnect + seat grace + explicit lobby/start (makes friend matches actually playable).
2. Entry-fee escrow (makes stakes safe).
3. Profile + daily reward + referral share (retention loop).
4. Then pick a meta system (cosmetics *or* league) and port its poker shape wholesale.
