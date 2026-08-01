# PRD — Fantasy Cards (reach-home reward overhaul)

Status: **All 17 cards live** (Phases 1–4 shipped) · Owner: game team · Last updated: 2026-08-01

> **Update:** every card's effect is wired into gameplay, with **manual target selection**
> for opponent-targeting cards (Freeze, Deep Freeze, Recall, Switcheroo) — the drawer picks
> which rival from a list; a single valid target auto-resolves, and a timeout falls back to
> the leading rival. The whole draw is a shared, spectator-visible flow (pick → reveal →
> target → result) so every player sees the card and who it hit. Cards use SVG icons (no
> emoji), and a **Cards** tab shows the full catalog. Buffs live on `GameState.effects`.

## 1. Summary

Bringing a token home used to grant a bonus dice roll. That reward is replaced by a
**fantasy-card draw**: the finishing player is shown four face-down cards, picks one by
chance, all four flip, and the chosen card's effect is applied. Cards range from small
tempo boosts to board-changing powers, giving each finish a moment of drama and a layer of
strategy on top of classic Ludo.

## 2. Goals / non-goals

**Goals**
- Make finishing a token a highlight moment (the "chance box").
- Introduce a catalog of ≥15 collectible power cards, editable without a code deploy.
- Lay engine + data foundations so new effects can be added incrementally.

**Non-goals (for now)**
- Card inventories / decks / trading. Cards are drawn on the spot, not held.
- Buying cards with coins, or rarity-weighted draw odds (draw is currently uniform).
- Bot players drawing cards (humans only in Phase 1).

## 3. Core mechanic

1. A human moves a token to HOME.
2. The runtime pauses that player's flow and offers **4 random cards** (uniform, distinct),
   face-down. Other players see "*X is choosing a card*".
3. The player taps one card. (Timeout → auto-pick, so play never stalls.)
4. All four flip; the picked card is highlighted; its effect applies.
5. Every draw is logged (`card_draws`: the 4 options + the pick).

The reward for reaching home is the **card**, not an extra roll. (One of the cards, *Encore*,
happens to grant the extra roll — so the old behaviour is a possible outcome, not the rule.)

## 4. Catalog & data model

Card definitions live in the database, **not in code**, so the set can grow via migration
or an admin tool without a deploy.

- `cards` — `id, name, icon, rarity, effect, status, description, position, enabled`
- `card_draws` — `id, match_id, user_id, seat, options(jsonb), picked, turn, created_at`
- Fetched by the app via `GET /api/cards`; drawn/applied by `MatchRuntime`.

`status`: `live` = effect wired into play; `soon` = drawable + shown but inert.

### Starter set (17 cards)

| Card | Icon | Rarity | Effect | Status |
|------|------|--------|--------|--------|
| Encore | 🎲 | common | roll again now | **live** |
| Starfall | ⭐ | rare | activate your colour's stars (safe) for the game | **live** |
| Aegis | 🛡️ | uncommon | shield one token from capture, 3 rounds | soon |
| Bulwark | 🏰 | epic | shield all your tokens, 2 rounds | soon |
| Twin Dice | 🎯 | uncommon | next 2 rolls count double | soon |
| Freeze | 🧊 | rare | a rival skips their next roll | soon |
| Deep Freeze | ❄️ | epic | a rival skips 2 turns | soon |
| Switcheroo | 🔄 | epic | swap one of your tokens with a rival's | soon |
| Warp | 🌀 | rare | jump a token to your nearest star | soon |
| Recall | 🪃 | uncommon | push a rival's lead token back | soon |
| Sprint | ⚡ | common | move a token +3 | soon |
| Rally | 🚀 | uncommon | free a token from base without a six | soon |
| Usurp | 👑 | epic | take the next player's turn | soon |
| Second Wind | 🍀 | rare | next knock-home on you is negated | soon |
| Toll Gate | ⛩️ | uncommon | your star blocks rivals for 1 round | soon |
| Mirror | 🪞 | rare | copy the last card an opponent played | soon |
| Jackpot | 💰 | common | bonus coins | soon |

## 5. Stars rework (shipped in Phase 1)

Previously there were 8 safe squares (4 coloured starts + 4 neutral stars), each protecting
its owner colour. Now:

- **Only the 4 coloured start squares are safe by default** (each a private sanctuary).
- The **4 neutral stars are inert** — safe for no one — until a colour plays **Starfall**,
  which activates that colour's neutral star (safe for the owner only) for the rest of the
  game. `GameState.active_stars` holds the activated colours and is serialised with the game.
- On the board, an inactive neutral star is faint grey; once activated it turns the owner's
  colour to signal it is now safe.

## 6. Phased rollout

- **Phase 1 (shipped):** chance-box draw flow, DB-backed catalog, draw logging, stars
  rework, *Encore* + *Starfall*.
- **Phase 2 (shipped):** self buffs via `GameState.effects` + engine hooks — *Sprint*,
  *Rally*, *Twin Dice* (movement doubled, keyed off `roll_face`), *Aegis/Bulwark*
  (shield in `_captures_at`), *Second Wind* (negate + consume in `apply_move`).
- **Phase 3 (shipped, auto-targeted):** *Freeze/Deep Freeze* (skip in the turn walker),
  *Recall*, *Warp*, *Switcheroo*, *Usurp*, *Toll Gate* (block in `legal_moves`), *Mirror*
  (replays the last opponent card). Targets are chosen automatically (leading rival / lead
  token) — no picker yet.
- **Phase 4 (shipped):** *Jackpot* awards coins (flushed with the stat write). Board shows
  shield/frozen/2× buff chips + a shield ring on protected tokens.
- **Remaining:** manual target selection UI, rarity-weighted draw odds, admin card editor,
  per-player draw history in profile, and a fuller animation/sound pass.

## 7. Open questions

- Draw odds: keep uniform, or weight by rarity?
- Should bots draw cards (and can they be targeted/target)?
- Stacking/expiry rules for timed buffs (rounds vs turns) — define precisely in Phase 2.
- Do multi-token finishes in one turn grant multiple draws? (Currently: one draw per
  token that reaches home.)

## 8. Implementation pointers

- Engine: `app/ludo/board.py` (safe squares / neutral stars), `app/ludo/state.py`
  (`active_stars`), `app/ludo/rules.py` (`_captures_at`, reach-home no longer grants extra).
- Runtime: `app/game/runtime.py` — `_draw_card`, `_apply_card`, `card` in `render()`.
- Data: migration `0007_card_draws` (seeds `cards`, creates `card_draws`); models in
  `app/models/event.py`; `GET /api/cards` in `app/api/routes_stats.py`.
- Client: `web/lib/cards.ts` (types), `ChanceBox` in `web/app/page.tsx`, star rendering in
  `web/components/board.tsx`, `pickCard` in `web/lib/ws.ts`.
