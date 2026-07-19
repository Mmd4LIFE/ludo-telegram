# 🎲 Ludo Rules (as encoded by the engine)

This is the exact ruleset `backend/app/ludo/` implements. Where real-world Ludo has
regional variants, the choice made here is called out so you can change it deliberately.

## Players & pieces

- **2–4 players.** Colours are `RED, GREEN, YELLOW, BLUE` (seat order = clockwise).
  - 2 players → RED & YELLOW (opposite corners).
  - 3 players → RED, GREEN, YELLOW.
  - 4 players → all four.
- Each player has **4 tokens**, all starting in their **base** (the yard).

## The board

- A shared **ring of 52 cells** (absolute index `0..51`).
- Each colour **enters the ring at a fixed cell**, 13 apart:
  RED→0, GREEN→13, YELLOW→26, BLUE→39.
- Each colour has a private **home column of 6 cells** leading to its final **home**.
- **Safe squares (stars)**: the four entry cells `{0, 13, 26, 39}` plus the four cells
  eight steps after each entry `{8, 21, 34, 47}` → 8 safe cells total. A token on a safe
  cell **cannot be captured**, and any number of tokens may share it.

## A turn

1. **Roll** a single die (1–6).
2. **Move** one token by the rolled number, if any legal move exists.

### Leaving base
- A token leaves base **only on a roll of 6**, moving to its entry cell.

### Moving on the track
- A token moves forward by the die value along its own path.
- It travels **51 ring cells** (its own `progress 0..50`), then turns into its **home
  column** (`progress 51..56`), reaching **home at `progress 56`**.
- **Exact landing to finish**: a token must land on home *exactly*. If the roll would
  overshoot 56, that token cannot move with that die.

### Capturing
- Landing on a ring cell occupied by a **single opponent token** sends that opponent
  **back to its base** — *unless* the cell is a **safe square**.
- **Blockade rule (this engine):** two or more of the *same colour* on one cell form a
  blockade that **cannot be captured**. This engine models that as "no capture" but still
  lets the mover land there. *(A stricter variant blocks passage entirely — see ROADMAP;
  it's an intentional simplification, flagged in `rules._captures_at`.)*
- Home-column cells are private per colour and **never** shared → no captures there.

### Extra turns (roll again)
You take another turn when your move:
- was made after rolling a **6**, **or**
- **captured** an opponent, **or**
- sent one of your tokens **home**.

### Triple six
- Rolling **three 6s in a row forfeits the whole turn** — the third six is void and play
  passes to the next player. (Handled in `register_roll`, which is why rolling is a state
  transition, not a bare RNG call.)

### No legal move
- If the die yields no legal move (e.g. a 3 with every token still in base), the turn
  **passes**. A 6 with no possible move also just ends the turn (no free re-roll).

## Winning & placement

- A player **finishes** when all four tokens reach home.
- The game **ends when only one player is left** still moving; every finisher is recorded
  in `state.ranking` in order (1st, 2nd, …), and the last player is appended so placements
  are complete for 2/3/4-player games.
- The winner (1st) takes the pot (`entry_fee × seats`) in `runtime._settle()`.

## Summary of deliberate choices (change these consciously)

| Rule | This engine | Common alternative |
|---|---|---|
| Blockade | Not capturable; passage allowed | Blocks passage entirely |
| Exact finish | Required | Some play "any roll ≥ needed" |
| Capture → extra turn | Yes | Sometimes only 6 grants it |
| Home → extra turn | Yes | Sometimes not |
| Triple six | Forfeit turn | Sometimes only resets, no penalty |
| Safe squares | 8 (4 starts + 4 stars) | Some boards use fewer/more |
