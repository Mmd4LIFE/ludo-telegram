"""Fantasy Arena rules: mana, hands, the opening draft, casting, realm events, champions.

Everything here is synchronous and operates on ``GameState.fantasy`` (a plain dict that
rides along in the serialised state so a restart resumes mid-arena). The runtime owns
timing, player input and broadcasting; the pure engine (``app.ludo``) only reads
``state.realm_event`` for Fog / Eclipse / Tailwind.

Shape of ``state.fantasy``::

    {
      "mana": {seat: int},            # current mana, 0..FANTASY_MANA_MAX
      "hand": {seat: [card_id, ...]},  # oldest first
      "turns": {seat: int},           # own turns started (drives the draw cadence)
      "champion": {seat: champion_id},
      "casts": {seat: int},           # cards cast this game
      "cast_log": {card_id: int},     # how often each card was cast ("Most cast")
      "event": {"id": str, "until_turn": int} | None,
      "next_event_turn": int,
      "cast_this_turn": [turn, seat] | None,
      "time_warp": seat | None,       # seat owed a second consecutive turn
      "draft": {"round": int, "picked": [seat, ...]} | None,
      "draft_options": {seat: [card_id, card_id, card_id]},
      "drafted": bool,
    }
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from app.ludo.board import HOME, MAIN_TRACK_LEN, START_OFFSET, absolute_square
from app.ludo.state import GameState, Phase

if TYPE_CHECKING:
    from app.game.card_catalog import CardInfo

MODE = "fantasy"

CHAMPIONS: dict[str, dict] = {
    "knight": {
        "name": "Knight", "icon": "🛡️",
        "blurb": "Shields last one extra round. Starts with 2 mana.",
    },
    "rogue": {
        "name": "Rogue", "icon": "🗡️",
        "blurb": "Every capture gives +2 mana instead of +1.",
    },
    "sage": {
        "name": "Sage", "icon": "📜",
        "blurb": "Draws a card every 2nd turn instead of every 3rd.",
    },
    "trickster": {
        "name": "Trickster", "icon": "🃏",
        "blurb": "Targeted cards cost 1 mana less.",
    },
}

EVENTS: dict[str, dict] = {
    "eclipse": {
        "name": "Eclipse", "icon": "🌑",
        "blurb": "Every neutral star is safe for everyone.",
    },
    "tailwind": {
        "name": "Tailwind", "icon": "🌬️",
        "blurb": "Every move is one step longer. Leaving the yard still needs a real six.",
    },
    "fog": {
        "name": "Fog", "icon": "🌫️",
        "blurb": "No captures this round.",
    },
    "harvest": {
        "name": "Harvest", "icon": "🌾",
        "blurb": "Everyone gains +2 mana.",
    },
    "quake": {
        "name": "Quake", "icon": "🌋",
        "blurb": "Every piece on the ring slips back two squares.",
    },
}

# effects implemented here (arena-only cards); every other effect is MatchRuntime._apply_card
ARENA_EFFECTS = frozenset({"mana_surge", "pickpocket", "hex", "time_warp"})
# effects that need an opponent chosen (seat) — matches MatchRuntime._TARGETING + arena ones
TARGETED_EFFECTS = frozenset(
    {"lock", "lock2", "recall", "swap", "vertigo", "direct_kick", "pickpocket", "hex"}
)
DRAFT_ROUNDS = 3
DRAFT_CHOICES = 3

_COST_KEY = {
    "common": "FANTASY_COST_COMMON",
    "uncommon": "FANTASY_COST_UNCOMMON",
    "rare": "FANTASY_COST_RARE",
    "epic": "FANTASY_COST_EPIC",
}


def random_champion(rng: random.Random | None = None) -> str:
    return (rng or random).choice(sorted(CHAMPIONS))


def init_state(
    state: GameState,
    champions: dict[int, str | None] | None = None,
    cfg: dict | None = None,
    rng: random.Random | None = None,
) -> None:
    """Turn a fresh classic ``GameState`` into an arena board (no hands yet: see draft)."""
    cfg = cfg or {}
    champions = champions or {}
    every = int(cfg.get("FANTASY_EVENT_EVERY", 8))
    state.fantasy = {
        "mana": {}, "hand": {}, "turns": {}, "champion": {}, "casts": {},
        "cast_log": {}, "event": None,
        "next_event_turn": every if every > 0 else 0,
        "cast_this_turn": None, "time_warp": None, "turn_started": None,
        "draft": None, "draft_options": {}, "drafted": False,
    }
    for seat in range(len(state.players)):
        add_seat(state, seat, champions.get(seat), rng=rng)


def add_seat(
    state: GameState, seat: int, champion: str | None, rng: random.Random | None = None
) -> None:
    """Register a seat (game start or late join) with its champion and starting mana."""
    f = state.fantasy
    if f is None:
        return
    champ = champion if champion in CHAMPIONS else random_champion(rng)
    f["champion"][seat] = champ
    f["mana"][seat] = 2 if champ == "knight" else 0
    f["hand"][seat] = []
    f["turns"][seat] = 0
    f["casts"][seat] = 0


class FantasyRules:
    """Stateless helpers bound to a config snapshot, the card catalog and an RNG."""

    def __init__(
        self, cfg: dict, cards: dict[str, "CardInfo"], rng: random.Random | None = None
    ) -> None:
        self.cfg = cfg
        self.cards = cards
        self.rng = rng or random.Random()

    # ---- tunables ---------------------------------------------------------
    @property
    def mana_max(self) -> int:
        return max(1, int(self.cfg.get("FANTASY_MANA_MAX", 10)))

    @property
    def hand_max(self) -> int:
        return max(1, int(self.cfg.get("FANTASY_HAND_MAX", 5)))

    def draw_every(self, state: GameState, seat: int) -> int:
        base = max(1, int(self.cfg.get("FANTASY_DRAW_EVERY", 3)))
        if self.champion(state, seat) == "sage":
            return max(1, min(base, 2))
        return base

    @property
    def event_every(self) -> int:
        return max(0, int(self.cfg.get("FANTASY_EVENT_EVERY", 8)))

    # ---- lookups ----------------------------------------------------------
    @staticmethod
    def champion(state: GameState, seat: int) -> str | None:
        return (state.fantasy or {}).get("champion", {}).get(seat)

    def effect_of(self, card_id: str) -> str | None:
        c = self.cards.get(card_id)
        return c.effect if c else None

    def is_targeted(self, card_id: str) -> bool:
        return self.effect_of(card_id) in TARGETED_EFFECTS

    def cost(self, state: GameState, seat: int, card_id: str) -> int:
        c = self.cards.get(card_id)
        if c is None:
            return 0
        cost = int(c.cost) if c.cost is not None else 0
        if cost <= 0:
            cost = int(self.cfg.get(_COST_KEY.get(c.rarity, "FANTASY_COST_COMMON"), 1))
        if self.champion(state, seat) == "trickster" and c.effect in TARGETED_EFFECTS:
            cost -= 1
        return max(0, cost)

    def pool(self) -> list[str]:
        return sorted(self.cards)

    # ---- mana -------------------------------------------------------------
    def mana(self, state: GameState, seat: int) -> int:
        return int((state.fantasy or {}).get("mana", {}).get(seat, 0))

    def gain_mana(self, state: GameState, seat: int, amount: int) -> int:
        """Add mana (clamped to the pool). Returns the amount actually gained."""
        f = state.fantasy
        if f is None or amount <= 0:
            return 0
        before = int(f["mana"].get(seat, 0))
        after = min(self.mana_max, before + amount)
        f["mana"][seat] = after
        return after - before

    def on_capture(self, state: GameState, seat: int, count: int = 1) -> int:
        per = 2 if self.champion(state, seat) == "rogue" else 1
        return self.gain_mana(state, seat, per * max(0, count))

    def on_home(self, state: GameState, seat: int) -> int:
        return self.gain_mana(state, seat, 2)

    def shield_bonus(self, state: GameState, seat: int) -> int:
        return 1 if self.champion(state, seat) == "knight" else 0

    # ---- hand -------------------------------------------------------------
    def hand(self, state: GameState, seat: int) -> list[str]:
        return list((state.fantasy or {}).get("hand", {}).get(seat, []))

    def add_to_hand(self, state: GameState, seat: int, card_id: str) -> list[str]:
        """Append a card; a full hand loses its OLDEST card. Returns discarded ids."""
        f = state.fantasy
        if f is None:
            return []
        hand = f["hand"].setdefault(seat, [])
        hand.append(card_id)
        discarded: list[str] = []
        while len(hand) > self.hand_max:
            discarded.append(hand.pop(0))
        return discarded

    def draw(self, state: GameState, seat: int, n: int = 1) -> list[str]:
        """Draw ``n`` random cards from the catalog into the seat's hand."""
        ids = self.pool()
        if not ids or state.fantasy is None:
            return []
        drawn = [self.rng.choice(ids) for _ in range(max(0, n))]
        for cid in drawn:
            self.add_to_hand(state, seat, cid)
        return drawn

    # ---- opening draft ----------------------------------------------------
    def draft_active(self, state: GameState) -> bool:
        f = state.fantasy
        return bool(f) and not f.get("drafted") and len(self.pool()) >= DRAFT_CHOICES

    def draft_seats(self, state: GameState) -> list[int]:
        return [s for s, p in enumerate(state.players) if not p.all_home()]

    def start_draft(self, state: GameState) -> None:
        f = state.fantasy
        if f is None:
            return
        f["draft"] = {"round": 1, "picked": []}
        self._deal_draft(state)

    def _deal_draft(self, state: GameState) -> None:
        f = state.fantasy
        ids = self.pool()
        f["draft_options"] = {
            s: self.rng.sample(ids, DRAFT_CHOICES) for s in self.draft_seats(state)
        }

    def draft_pending(self, state: GameState) -> list[int]:
        f = state.fantasy or {}
        d = f.get("draft")
        if not d:
            return []
        return [s for s in self.draft_seats(state) if s not in d["picked"]]

    def draft_pick(self, state: GameState, seat: int, index: int | None) -> str | None:
        """Seat takes option ``index`` (random when None/invalid). Returns the card id."""
        f = state.fantasy
        d = f.get("draft") if f else None
        if not d or seat in d["picked"]:
            return None
        options = f["draft_options"].get(seat) or []
        if not options:
            d["picked"].append(seat)
            return None
        if index is None or not 0 <= index < len(options):
            index = self.rng.randrange(len(options))
        card = options[index]
        self.add_to_hand(state, seat, card)
        d["picked"].append(seat)
        return card

    def draft_advance(self, state: GameState) -> bool:
        """Once everyone picked: deal the next round, or finish. True when the draft is over."""
        f = state.fantasy
        d = f.get("draft") if f else None
        if not d:
            return True
        if self.draft_pending(state):
            return False
        if d["round"] >= DRAFT_ROUNDS:
            f["draft"] = None
            f["draft_options"] = {}
            f["drafted"] = True
            return True
        d["round"] += 1
        d["picked"] = []
        self._deal_draft(state)
        return False

    # ---- turn flow --------------------------------------------------------
    def start_of_turn(self, state: GameState, seat: int) -> dict:
        """Called once when ``seat`` begins a NEW turn (not on a six's extra roll).

        +1 mana, a draw on every Nth own turn, realm-event expiry and firing.
        """
        f = state.fantasy
        out: dict = {"mana": 0, "drew": [], "event": None, "event_ended": None}
        if f is None:
            return out
        key = [state.turn, seat]
        if f.get("turn_started") == key:
            return out   # already ran for this turn (extra roll on a six, or a restart)
        f["turn_started"] = key
        f["turns"][seat] = int(f["turns"].get(seat, 0)) + 1
        out["mana"] = self.gain_mana(state, seat, 1)
        if f["turns"][seat] % self.draw_every(state, seat) == 0:
            out["drew"] = self.draw(state, seat, 1)
        ev = f.get("event")
        if ev and state.turn >= int(ev.get("until_turn", 0)):
            out["event_ended"] = ev["id"]
            f["event"] = None
        every = self.event_every
        if every > 0 and state.turn > 0 and state.turn >= int(f.get("next_event_turn", 0)):
            f["next_event_turn"] = state.turn + every
            out["event"] = self.apply_event(state)
        return out

    def apply_event(self, state: GameState, event_id: str | None = None) -> str:
        """Start a realm event lasting one full round. Harvest/Quake act immediately."""
        f = state.fantasy
        eid = event_id or self.rng.choice(sorted(EVENTS))
        active = max(1, len(state.active_seats()))
        f["event"] = {"id": eid, "until_turn": state.turn + active}
        if eid == "harvest":
            for seat in range(len(state.players)):
                if not state.players[seat].all_home():
                    self.gain_mana(state, seat, 2)
        elif eid == "quake":
            for p in state.players:
                for i, prog in enumerate(p.tokens):
                    if 0 <= prog <= 50:
                        p.tokens[i] = max(0, prog - 2)
        return eid

    def event_turns_left(self, state: GameState) -> int:
        f = state.fantasy or {}
        ev = f.get("event")
        if not ev:
            return 0
        return max(0, int(ev.get("until_turn", 0)) - state.turn)

    def adjust_roll(self, state: GameState, seat: int, face: int) -> int:
        """Hex caps the next roll at 3 (one charge)."""
        if state.eff("hex", seat) > 0:
            state.add_eff("hex", seat, -1)
            return min(face, 3)
        return face

    def consume_time_warp(self, state: GameState, seat: int) -> bool:
        """After ``seat`` moved: True if Time Warp owes them another turn right now."""
        f = state.fantasy
        if f is None or f.get("time_warp") != seat:
            return False
        if state.phase is Phase.FINISHED or state.players[seat].all_home():
            f["time_warp"] = None
            return False
        if state.current == seat:
            return False   # they already have the turn (a six); keep the warp armed
        f["time_warp"] = None
        f["turn_started"] = None   # a fresh turn so start_of_turn grants mana/draw
        state.turn += 1
        return True

    # ---- casting ----------------------------------------------------------
    def cast_this_turn(self, state: GameState, seat: int) -> bool:
        f = state.fantasy or {}
        return f.get("cast_this_turn") == [state.turn, seat]

    def can_cast(self, state: GameState, seat: int, card_id: str) -> str | None:
        """None when the cast is legal, else a short reason."""
        f = state.fantasy
        if f is None:
            return "not_fantasy"
        if state.phase is not Phase.ROLL or state.current != seat:
            return "not_your_roll"
        if f.get("draft"):
            return "drafting"
        if card_id not in f["hand"].get(seat, []):
            return "not_in_hand"
        if self.cast_this_turn(state, seat):
            return "already_cast"
        if self.mana(state, seat) < self.cost(state, seat, card_id):
            return "no_mana"
        return None

    def pay(self, state: GameState, seat: int, card_id: str) -> int:
        """Remove the card from the hand, spend its mana, mark the turn. Returns the cost."""
        f = state.fantasy
        cost = self.cost(state, seat, card_id)
        hand = f["hand"].setdefault(seat, [])
        if card_id in hand:
            hand.remove(card_id)
        f["mana"][seat] = max(0, int(f["mana"].get(seat, 0)) - cost)
        f["cast_this_turn"] = [state.turn, seat]
        f["casts"][seat] = int(f["casts"].get(seat, 0)) + 1
        f["cast_log"][card_id] = int(f["cast_log"].get(card_id, 0)) + 1
        return cost

    def valid_targets(self, state: GameState, seat: int, effect: str) -> list[int] | None:
        """Targets for the arena-only effects (None = untargeted)."""
        if effect not in ARENA_EFFECTS or effect not in TARGETED_EFFECTS:
            return None
        opps = [s for s, p in enumerate(state.players) if s != seat and not p.all_home()]
        if effect == "pickpocket":
            opps = [s for s in opps if (state.fantasy or {}).get("hand", {}).get(s)]
        return opps

    def apply_arena_effect(
        self, state: GameState, seat: int, effect: str, target: int | None
    ) -> int | None:
        """Apply one of the arena-only effects. Returns the seat it landed on, if any."""
        f = state.fantasy
        if f is None:
            return None
        if effect == "mana_surge":
            self.gain_mana(state, seat, 3)
            return None
        if effect == "time_warp":
            f["time_warp"] = seat
            return None
        if effect == "hex":
            if target is None:
                target = self.leading_rival(state, seat)
            if target is not None:
                state.add_eff("hex", target, 1)
            return target
        if effect == "pickpocket":
            if target is None:
                rich = self.valid_targets(state, seat, effect) or []
                target = max(rich, key=lambda s: len(f["hand"].get(s, [])), default=None)
            if target is None:
                return None
            hand = f["hand"].get(target) or []
            if not hand:
                return target
            stolen = hand.pop(self.rng.randrange(len(hand)))
            self.add_to_hand(state, seat, stolen)
            return target
        return None

    @staticmethod
    def leading_rival(state: GameState, seat: int) -> int | None:
        best, best_score = None, -1
        for s, p in enumerate(state.players):
            if s == seat or p.all_home():
                continue
            score = sum(t for t in p.tokens if t >= 0)
            if score > best_score:
                best, best_score = s, score
        return best

    # ---- bots -------------------------------------------------------------
    def threatened(self, state: GameState, seat: int) -> bool:
        """True when a rival piece sits 1..6 squares behind one of our ring pieces."""
        me = state.players[seat]
        mine = [absolute_square(me.color, t) for t in me.tokens if 0 <= t <= 50]
        if not mine:
            return False
        for s, p in enumerate(state.players):
            if s == seat or p.all_home():
                continue
            for t in p.tokens:
                sq = absolute_square(p.color, t)
                if sq is None:
                    continue
                for m in mine:
                    if m == START_OFFSET[me.color]:
                        continue  # our own start is a sanctuary
                    if 1 <= (m - sq) % MAIN_TRACK_LEN <= 6:
                        return True
        return False

    def bot_pick_cast(self, state: GameState, seat: int) -> str | None:
        """The card a house bot casts now, or None to hold.

        Summon when everything is in the yard, a shield when a piece is threatened,
        disruption on the leading rival, Mana Surge when poor, otherwise save mana.
        """
        f = state.fantasy
        if f is None or f.get("draft") or self.cast_this_turn(state, seat):
            return None
        hand = f["hand"].get(seat, [])
        mana = self.mana(state, seat)
        affordable = [c for c in hand if self.cost(state, seat, c) <= mana]
        if not affordable:
            return None

        def first(*effects: str) -> str | None:
            for eff in effects:
                for c in affordable:
                    if self.effect_of(c) == eff:
                        return c
            return None

        me = state.players[seat]
        on_ring = [t for t in me.tokens if 0 <= t <= 50]
        in_base = [t for t in me.tokens if t < 0]
        if in_base and not on_ring:
            pick = first("summon")
            if pick:
                return pick
        if on_ring and self.threatened(state, seat):
            pick = first("shield", "shield_all", "second_chance")
            if pick:
                return pick
        if self.leading_rival(state, seat) is not None:
            pick = first("direct_kick", "lock2", "lock", "vertigo", "recall", "hex")
            if pick and self.cost(state, seat, pick) <= mana:
                # only disrupt when a rival actually has pieces on the ring to hit
                rival = self.leading_rival(state, seat)
                rival_ring = [
                    t for t in state.players[rival].tokens if 0 <= t < HOME
                ] if rival is not None else []
                if rival_ring or self.effect_of(pick) in ("lock", "lock2", "hex"):
                    return pick
        if mana <= 3:
            pick = first("mana_surge")
            if pick:
                return pick
        if on_ring and mana >= 6:
            pick = first("time_warp", "extra_roll", "steal_turn", "boost", "teleport", "double_dice")
            if pick:
                return pick
        if mana >= self.mana_max:
            return affordable[0]   # never waste a full pool
        return None

    # ---- rendering --------------------------------------------------------
    def view(self, state: GameState, viewer_seat: int | None) -> dict | None:
        """The per-user Fantasy Arena payload: own hand in full, rivals as counts."""
        f = state.fantasy
        if f is None:
            return None
        seats = range(len(state.players))
        ev = f.get("event")
        event = None
        if ev and state.turn < int(ev.get("until_turn", 0)):
            meta = EVENTS.get(ev["id"], {})
            event = {
                "id": ev["id"], "name": meta.get("name", ev["id"]),
                "icon": meta.get("icon", ""), "blurb": meta.get("blurb", ""),
                "turns_left": self.event_turns_left(state),
            }
        own_hand = []
        if viewer_seat is not None:
            for cid in f["hand"].get(viewer_seat, []):
                c = self.cards.get(cid)
                own_hand.append({
                    "id": cid,
                    "effect": c.effect if c else cid,
                    "cost": self.cost(state, viewer_seat, cid),
                    "targeted": self.is_targeted(cid),
                    "castable": self.can_cast(state, viewer_seat, cid) is None,
                })
        draft = None
        d = f.get("draft")
        if d:
            draft = {
                "round": d["round"], "rounds": DRAFT_ROUNDS,
                "options": list(f["draft_options"].get(viewer_seat, []))
                if viewer_seat is not None else [],
                "picked": viewer_seat in d["picked"] if viewer_seat is not None else True,
                "waiting": self.draft_pending(state),
            }
        return {
            "mana_max": self.mana_max,
            "hand_max": self.hand_max,
            "mana": {str(s): int(f["mana"].get(s, 0)) for s in seats},
            "hand_counts": {str(s): len(f["hand"].get(s, [])) for s in seats},
            "hand": own_hand,
            "champion": {str(s): f["champion"].get(s) for s in seats},
            "casts": {str(s): int(f["casts"].get(s, 0)) for s in seats},
            "cast_log": dict(f.get("cast_log", {})),
            "event": event,
            "next_event_turn": int(f.get("next_event_turn", 0)),
            "cast_this_turn": f.get("cast_this_turn"),
            "time_warp": f.get("time_warp"),
            "draft": draft,
        }
