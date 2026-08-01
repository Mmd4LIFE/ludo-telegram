"""Immutable-ish game state containers.

These are plain dataclasses — the *data* of a game with no rule logic on them (rules
live in ``rules.py``). They are trivially serialisable to a dict for persistence and for
the websocket payload, and cheap to deep-copy for simulation / what-if search by bots.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum

from app.ludo.board import BASE, TOKENS_PER_PLAYER, Color


class Phase(str, Enum):
    ROLL = "roll"        # current player must roll the die
    MOVE = "move"        # die is rolled; current player must pick a token to move
    FINISHED = "finished"  # game over; see GameState.ranking


@dataclass
class PlayerState:
    color: Color
    # progress of each of the 4 tokens; BASE (-1) means "in the yard".
    tokens: list[int] = field(default_factory=lambda: [BASE] * TOKENS_PER_PLAYER)
    finished_at: int | None = None   # turn number when all tokens reached home

    def home_count(self) -> int:
        from app.ludo.board import HOME
        return sum(1 for t in self.tokens if t == HOME)

    def all_home(self) -> bool:
        return self.home_count() == TOKENS_PER_PLAYER

    def to_dict(self) -> dict:
        return {
            "color": self.color.name,
            "tokens": list(self.tokens),
            "finished_at": self.finished_at,
        }


@dataclass
class Move:
    """A single legal move: advance ``token_index`` from ``src`` to ``dst`` progress."""

    token_index: int
    src: int
    dst: int
    # populated by legal_moves() so the UI/bots can rank without re-deriving:
    releases_from_base: bool = False
    reaches_home: bool = False
    captures: tuple[tuple[int, int], ...] = ()  # (opponent_seat, token_index) pairs

    def to_dict(self) -> dict:
        return {
            "token_index": self.token_index,
            "src": self.src,
            "dst": self.dst,
            "releases_from_base": self.releases_from_base,
            "reaches_home": self.reaches_home,
            "captures": [list(c) for c in self.captures],
        }


@dataclass
class GameState:
    players: list[PlayerState]
    current: int = 0                 # seat index of the player to act
    phase: Phase = Phase.ROLL
    die: int | None = None           # face showing after a roll (1..6), else None
    consecutive_sixes: int = 0       # triple-6 forfeits the turn
    turn: int = 0                    # monotonically increasing turn counter
    ranking: list[int] = field(default_factory=list)  # seats in finishing order
    # colour VALUES (0..3) whose neutral stars are active (safe) — set by the
    # "Active Stars" fantasy card. Empty by default: neutral stars protect no one.
    active_stars: list[int] = field(default_factory=list)

    # ---- convenience ------------------------------------------------------
    @property
    def current_player(self) -> PlayerState:
        return self.players[self.current]

    def active_seats(self) -> list[int]:
        """Seats that have NOT yet finished, in seating order."""
        return [i for i, p in enumerate(self.players) if not p.all_home()]

    def clone(self) -> "GameState":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return {
            "players": [p.to_dict() for p in self.players],
            "current": self.current,
            "phase": self.phase.value,
            "die": self.die,
            "consecutive_sixes": self.consecutive_sixes,
            "turn": self.turn,
            "ranking": list(self.ranking),
            "active_stars": list(self.active_stars),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        players = [
            PlayerState(
                color=Color[p["color"]],
                tokens=list(p["tokens"]),
                finished_at=p.get("finished_at"),
            )
            for p in d["players"]
        ]
        return cls(
            players=players,
            current=d.get("current", 0),
            phase=Phase(d.get("phase", "roll")),
            die=d.get("die"),
            consecutive_sixes=d.get("consecutive_sixes", 0),
            turn=d.get("turn", 0),
            ranking=list(d.get("ranking", [])),
            active_stars=list(d.get("active_stars", [])),
        )
