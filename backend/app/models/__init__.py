"""Import every model module so they register on Base.metadata for Alembic + create_all."""
from app.models.analytics import AnalyticsCursor, AnalyticsOutbox
from app.models.user import User
from app.models.match import Match, MatchJoinRequest, MatchSeat, MatchStatus
from app.models.chat import ChatMessage, MessageReaction, ReactionEmoji
from app.models.event import DiceRoll, Knockout, Card, CardDraw
from app.models.poll import PollTemplate, Poll, PollOption, PollVote
from app.models.config import AppConfig
from app.models.insights import InsightsQuery, InsightsStep, InsightsKnowledge, InsightsSqlRun
from app.models.ledger import Balance, BalanceHistory
from app.models.wallet import Transfer, StarPayment, TransferStatus, StarPaymentStatus

__all__ = [
    "User", "Match", "MatchSeat", "MatchJoinRequest", "MatchStatus",
    "AnalyticsOutbox", "AnalyticsCursor",
    "ChatMessage", "MessageReaction", "ReactionEmoji",
    "DiceRoll", "Knockout", "Card", "CardDraw",
    "PollTemplate", "Poll", "PollOption", "PollVote",
    "AppConfig", "InsightsQuery", "InsightsStep", "InsightsKnowledge", "InsightsSqlRun",
    "Balance", "BalanceHistory",
    "Transfer", "StarPayment", "TransferStatus", "StarPaymentStatus",
]
