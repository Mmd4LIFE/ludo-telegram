"""Import every model module so they register on Base.metadata for Alembic + create_all."""
from app.models.user import User
from app.models.match import Match, MatchSeat, MatchStatus

__all__ = ["User", "Match", "MatchSeat", "MatchStatus"]
