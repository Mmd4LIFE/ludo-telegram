"""Curated database knowledge for the Insights assistant.

Three kinds of hand-written knowledge feed retrieval (``knowledge.py``) alongside the
auto-introspected schema:

* ``TABLE_DOCS``  — what each table/column means (merged into the per-table chunk)
* ``TOPICS``      — business definitions and query conventions
* ``EXAMPLES``    — verified question → SQL pairs (few-shot); ``{TZ}`` is replaced with
                    the admin timezone interval, e.g. ``INTERVAL '+03:30'``

Keep these accurate: the model trusts them over its own guesses. Every example must run
on the read-only role (``test_insights.py`` executes them all).
"""
from __future__ import annotations

TABLE_DOCS: dict[str, dict[str, str]] = {
    "users": {
        "_": "One row per player account (Telegram user). House bots normally have NO user row "
             "(bot seats have match_seats.user_id NULL), but rows with is_bot = true may exist — "
             "exclude them from player metrics. created_at = signup time.",
        "id": "Internal player id. Use this to identify players (never telegram_id).",
        "username": "Telegram @username without the @, may be NULL.",
        "first_name": "Display name. Search players with first_name ILIKE '%name%' OR username ILIKE '%name%'.",
        "language_code": "Telegram client language, e.g. 'en', 'fa', 'ru'.",
        "wallet_code": "Public UUID used for wallet-to-wallet transfers.",
        "coins": "Mirror of balances.amount (currency 'coins'). Prefer balances for economy math.",
        "level": "Player level (progression).",
        "xp": "Experience points (+100 per win, +25 per non-winning finished game).",
        "games_played": "Lifetime finished games counter (humans only).",
        "games_won": "Lifetime 1st-place finishes counter.",
        "dice_hist": "JSONB {\"1\": n, ..., \"6\": n}: lifetime count of each die face rolled.",
        "captures_dealt": "Lifetime knockouts this player made (sent an opponent token home).",
        "captures_taken": "Lifetime times this player's tokens were knocked home.",
        "potential_knocks": "Lifetime legal captures the player passed up.",
        "dice_skin": "Active cosmetic dice skin id.",
        "unlocked_skins": "JSONB array of unlocked dice skin ids.",
        "is_bot": "True for house-bot accounts. Exclude by default.",
        "is_banned": "Banned by an admin.",
        "bot_started": "Player pressed /start in the Telegram bot chat.",
        "last_seen_at": "Last time the player was seen in the Mini App (only the latest, no history).",
        "updated_at": "Row last modified.",
    },
    "matches": {
        "_": "A Ludo room/game. created_at = room created; for finished/abandoned matches "
             "updated_at ≈ when it ended. is_bot_table = self-play filler tables run only by "
             "house bots — exclude them from human metrics.",
        "code": "Short room code used in deep links (rm-<code>).",
        "status": "Postgres enum match_status: 'waiting' (lobby filling), 'playing', "
                  "'finished' (someone won, placements final), 'abandoned' (closed without finishing).",
        "max_players": "Seats in the room, 2..4.",
        "is_public": "Listed in the public lobby.",
        "is_bot_table": "Self-play bot table (lobby filler). Exclude by default.",
        "entry_fee": "Coins staked per human seat (0 = free room).",
        "created_by": "users.id of the room creator (NULL for bot tables).",
        "state": "JSONB snapshot of the game engine state (players, ranking, turn, die …). "
                 "Rarely needed; use match_seats.place for results.",
    },
    "match_seats": {
        "_": "Seats of a match. One row per seat; a human seat has user_id set, a bot seat has "
             "is_bot = true and user_id NULL. place is the final placement (1 = winner) and is only "
             "set once the match is finished. created_at ≈ when the seat was taken.",
        "seat_index": "0..3, board position.",
        "color": "'BLUE', 'RED', 'GREEN' or 'YELLOW' (turn order is always blue → red → green → yellow).",
        "user_id": "users.id of the human in this seat; NULL for bots.",
        "is_bot": "True for a house-bot seat.",
        "connected": "Live connection flag (only meaningful for live matches).",
        "place": "Final placement 1..N for finished matches, NULL otherwise.",
    },
    "dice_rolls": {
        "_": "Append-only log of every die roll by a human (bots are not logged). "
             "Good proxy for activity: a user who rolled on a day was active that day.",
        "value": "Die face 1..6.",
        "turn": "Engine turn counter inside the match.",
        "seat": "Seat index of the roller.",
    },
    "knockouts": {
        "_": "Append-only log of captures. taken = true: an actual capture (attacker sent the "
             "victim's token home). taken = false: a *potential* capture that was legal but the "
             "attacker passed it up. victim_user_id is NULL when the victim was a bot.",
        "attacker_user_id": "users.id of the capturing human.",
        "victim_user_id": "users.id of the victim, NULL for bots.",
        "taken": "true = real knock, false = missed opportunity.",
    },
    "cards": {
        "_": "Catalog of fantasy cards (seeded in the DB).",
        "id": "Card id (string, e.g. 'twin_dice'). card_draws.picked references it.",
        "effect": "Engine effect key.",
        "status": "'live' (in the game) or 'soon' (teaser).",
        "rarity": "common/rare/epic/legendary …",
        "enabled": "Admin toggle.",
    },
    "card_draws": {
        "_": "Every fantasy-card draw: the 4 card ids offered (options JSONB array) and the one "
             "the player picked (picked = cards.id).",
        "options": "JSONB array of 4 cards.id strings offered.",
        "picked": "cards.id chosen.",
    },
    "balances": {
        "_": "Current coin balance per user — the source of truth for balances (users.coins mirrors "
             "it). One row per (user_id, currency); currency is always 'coins' today.",
        "amount": "Current balance in coins.",
    },
    "balance_history": {
        "_": "Append-only coin ledger: one row per credit/debit. delta is signed (+credit / -debit), "
             "balance_after is the running balance after the row. reason classifies the movement "
             "(see the 'Coin economy' doc). ref_type/ref_id link to the source, e.g. "
             "ref_type = 'match' → ref_id = matches.id. meta holds details (JSONB).",
        "delta": "Signed coin change.",
        "balance_after": "User balance right after this movement.",
        "reason": "Movement type, e.g. placement, entry_fee, reroll, stars_purchase, transfer_in …",
        "ref_type": "'match' for in-game movements, else NULL.",
        "ref_id": "matches.id when ref_type = 'match'; transfers.id / star_payments.id for wallet rows.",
        "meta": "JSONB details, e.g. {\"place\": 1, \"humans\": 3} for placement, "
                "{\"role\": \"referrer\", \"friend_id\": 12} for referral.",
    },
    "transfers": {
        "_": "Wallet-to-wallet coin transfers. A direct transfer is created with status 'completed' "
             "and to_user_id set. A gift link escrows coins from the sender (status 'pending', "
             "to_user_id NULL) until claimed ('claimed', to_user_id set), or refunded as "
             "'expired' / 'cancelled'. Gift links expire after 7 days.",
        "from_user_id": "Sender users.id.",
        "to_user_id": "Recipient users.id (NULL while a gift is unclaimed).",
        "amount": "Coins moved.",
        "status": "pending | completed | claimed | cancelled | expired.",
        "note": "Optional message from the sender.",
        "claimed_at": "When the gift was claimed.",
    },
    "star_payments": {
        "_": "Telegram Stars purchases of coins. A row is created as 'pending' when the invoice "
             "is issued and becomes 'paid' (paid_at set) after Telegram confirms the payment. "
             "Revenue = SUM(stars) WHERE status = 'paid'. stars is in Telegram Stars (XTR).",
        "stars": "Stars charged.",
        "coins": "Coins credited for the purchase.",
        "pack_stars": "Shop pack size chosen (50/100/250/500).",
        "status": "pending | paid | failed.",
        "telegram_payment_charge_id": "Telegram charge id (set when paid).",
    },
    "chat_messages": {
        "_": "In-room chat messages. Soft-deleted rows have deleted_at set — exclude them "
             "(deleted_at IS NULL) for visible messages. poll_id set = the message is a poll card.",
        "name": "Author display name snapshot.",
        "text": "Message text.",
    },
    "message_reactions": {"_": "One emoji reaction per (message, user)."},
    "reaction_emojis": {"_": "Admin-managed set of allowed chat reaction emojis."},
    "poll_templates": {"_": "Admin-defined instant polls (question + options JSONB) players can launch in chat. trigger: 'knock' | 'any'."},
    "polls": {"_": "Polls launched in a match chat. kind: instant | open. status: open | closed."},
    "poll_options": {"_": "Options of a poll."},
    "poll_votes": {"_": "One vote per (poll, user)."},
    "app_configs": {
        "_": "Append-only admin settings. The current value of a key is the newest row with "
             "deleted_at IS NULL; no row = code default. value is a string (cast to int as needed).",
    },
    "insights_queries": {"_": "Audit log of questions asked to this Insights assistant (admin-only internals)."},
    "insights_steps": {"_": "Per-step trace of Insights questions (internal)."},
    "insights_knowledge": {"_": "The assistant's own knowledge store (internal)."},
    "insights_sql_runs": {"_": "Audit of native SQL queries run by admins (internal)."},
}

# Low-cardinality text columns whose live distinct values are indexed for retrieval.
CATEGORICAL: list[tuple[str, str]] = [
    ("balance_history", "reason"),
    ("balance_history", "ref_type"),
    ("matches", "status"),
    ("match_seats", "color"),
    ("transfers", "status"),
    ("star_payments", "status"),
    ("star_payments", "pack_stars"),
    ("cards", "id"),
    ("cards", "effect"),
    ("cards", "rarity"),
    ("cards", "status"),
    ("polls", "kind"),
    ("polls", "status"),
    ("poll_templates", "trigger"),
    ("users", "language_code"),
    ("users", "dice_skin"),
    ("app_configs", "key"),
]

TOPICS: list[tuple[str, str, str]] = [
    (
        "doc:conventions",
        "Query conventions",
        """- All timestamps are timestamptz stored in UTC. The admin's local timezone is {TZ}.
- Calendar periods ("today", "yesterday", "this week", "last month") are in the admin timezone:
  start of today        = date_trunc('day',   now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
  start of this week    = date_trunc('week',  now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}   (weeks start Monday)
  start of this month   = date_trunc('month', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
  local calendar day of a timestamp = (created_at AT TIME ZONE {TZ})::date
  "last 7 days" = created_at >= now() - interval '7 days'
- Use half-open ranges: ts >= start AND ts < end.
- Exclude house bots unless asked: users.is_bot = false, match_seats.is_bot = false / user_id IS NOT NULL,
  matches.is_bot_table = false.
- Never use SELECT * on users (private columns are not readable). Identify players by id, first_name, username.
- Use COUNT(DISTINCT ...) when counting people through join tables.
- For time series, generate the full calendar with generate_series so empty days show as 0.
- Limit row listings (LIMIT 50) unless the admin asks for everything.""",
    ),
    (
        "doc:privacy",
        "Privacy rules",
        """users.telegram_id and users.referred_by (it stores the referrer's Telegram id) are private and cannot be selected.
transfers.claim_token and star_payments.invoice_payload are secrets and cannot be selected either.
For referrals use balance_history rows with reason = 'referral': meta->>'role' is 'referrer' or 'friend',
meta->>'friend_id' / meta->>'referrer_id' hold users.id values.""",
    ),
    (
        "doc:players",
        "Players, signups and activity",
        """- A player = a row in users with is_bot = false. Signups on a day = users.created_at in that day.
- "Active users" has no stored history; last_seen_at only holds the latest visit.
  • Currently/recently active: last_seen_at >= now() - interval '…'.
  • Historical daily/weekly active users (DAU/WAU): count DISTINCT user_id from dice_rolls (they rolled)
    in the period; optionally union match_seats.user_id by created_at (joined a room).
- Retention (came back): a user active on a later day than their signup day (dice_rolls after created_at + 1 day).
- Banned players: users.is_banned = true. Players who opened the bot chat: bot_started = true.
- Paying users: users with at least one star_payments row with status = 'paid'.""",
    ),
    (
        "doc:matches",
        "Matches, results and wins",
        """- Match lifecycle: waiting → playing → finished, or → abandoned. Live games = status IN ('waiting','playing').
- Human matches: matches.is_bot_table = false. A human took part if a match_seats row has user_id = that user.
- Results: match_seats.place (1 = winner) is set when status = 'finished'. Wins of a player in a period =
  COUNT(*) FROM match_seats s JOIN matches m ON m.id = s.match_id WHERE s.user_id = X AND s.place = 1
  AND m.status = 'finished' AND m.updated_at in the period (updated_at ≈ finish time).
- users.games_won / games_played are lifetime counters (fine for all-time questions).
- Win rate = wins / finished games played (from match_seats + matches) — guard division by zero with NULLIF.
- Match duration ≈ matches.updated_at - matches.created_at for finished matches.
- Players per match = COUNT(match_seats) WHERE user_id IS NOT NULL.""",
    ),
    (
        "doc:economy",
        "Coin economy (ledger)",
        """Coins are a soft currency. balances.amount is the current balance; balance_history is the append-only ledger.
Faucets (coins created, delta > 0): placement (finishing rewards), couldve (bonus for passed-up knocks),
jackpot_card (card payout), insurance_payout, referral, signup, bot_start, admin_credit,
stars_purchase (bought with Telegram Stars), opening_balance (ledger migration seed).
Sinks (coins destroyed, delta < 0): entry_fee (room stake; the winner's pot credit is also reason 'entry_fee'
with meta->>'kind' = 'pot_win'), reroll, hint, peek, insurance, buy_card, card_boost, skin_unlock, admin_debit.
Transfers move coins between players: transfer_out (sender, also escrow when a gift link is created),
transfer_in (recipient), transfer_refund (expired/cancelled gift back to sender). They net to zero overall.
Coins in circulation = SUM(balances.amount) of non-bot users.
Net flow per reason = SUM(delta) GROUP BY reason. Spending = -SUM(delta) WHERE delta < 0.""",
    ),
    (
        "doc:monetization",
        "Telegram Stars revenue",
        """star_payments with status = 'paid' are completed purchases. Revenue in Stars = SUM(stars).
Coins sold = SUM(coins). Buyers = COUNT(DISTINCT user_id). Conversion = buyers / players.
The exchange rate is the app_configs key COINS_PER_STAR (default 50 coins per star).
Pending rows are invoices that were opened but not paid.""",
    ),
    (
        "doc:gameplay",
        "Dice, knockouts and cards",
        """- dice_rolls: every human roll (value 1..6). Sixes = value = 6. Average roll = AVG(value).
- knockouts: taken = true is a real capture ("knock"), taken = false a missed opportunity.
  Knocks by a player = COUNT(*) WHERE attacker_user_id = X AND taken. Times knocked = victim_user_id = X AND taken.
- card_draws: a fantasy-card draw; picked is the chosen cards.id. Popularity = COUNT(*) GROUP BY picked.
  Join cards ON cards.id = card_draws.picked for names.
- Power-up spending lives in balance_history: reroll, hint, peek, insurance, buy_card, card_boost.""",
    ),
    (
        "doc:settings",
        "Admin settings",
        """app_configs is append-only. Current value per key:
SELECT DISTINCT ON (key) key, value, created_at FROM app_configs WHERE deleted_at IS NULL ORDER BY key, created_at DESC, id DESC.
Keys include payouts (PAY_1ST_4 …), timers (TURN_TIMEOUT_SECONDS, CARD_PICK_SECONDS), power-up costs
(REROLL_COST …) and COINS_PER_STAR. A key with no row uses its code default.""",
    ),
]

EXAMPLES: list[tuple[str, str]] = [
    (
        "How many players do we have?",
        "SELECT COUNT(*) AS players FROM users WHERE NOT is_bot",
    ),
    (
        "How many new users signed up today?",
        """SELECT COUNT(*) AS signups
FROM users
WHERE NOT is_bot
  AND created_at >= date_trunc('day', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}""",
    ),
    (
        "Daily signups for the last 14 days",
        """WITH days AS (
  SELECT generate_series(
    (now() AT TIME ZONE {TZ})::date - 13,
    (now() AT TIME ZONE {TZ})::date,
    interval '1 day')::date AS day
)
SELECT d.day, COUNT(u.id) AS signups
FROM days d
LEFT JOIN users u
  ON NOT u.is_bot AND (u.created_at AT TIME ZONE {TZ})::date = d.day
GROUP BY d.day
ORDER BY d.day""",
    ),
    (
        "Daily active users this week",
        """SELECT (r.created_at AT TIME ZONE {TZ})::date AS day, COUNT(DISTINCT r.user_id) AS active_users
FROM dice_rolls r
WHERE r.created_at >= date_trunc('week', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
GROUP BY 1
ORDER BY 1""",
    ),
    (
        "How many users were online in the last hour?",
        """SELECT COUNT(*) AS online_last_hour
FROM users
WHERE NOT is_bot AND last_seen_at >= now() - interval '1 hour'""",
    ),
    (
        "How many matches finished yesterday?",
        """SELECT COUNT(*) AS finished_matches
FROM matches
WHERE status = 'finished' AND NOT is_bot_table
  AND updated_at >= (date_trunc('day', now() AT TIME ZONE {TZ}) - interval '1 day') AT TIME ZONE {TZ}
  AND updated_at <  date_trunc('day', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}""",
    ),
    (
        "Matches by status (human rooms)",
        """SELECT status, COUNT(*) AS matches
FROM matches
WHERE NOT is_bot_table
GROUP BY status
ORDER BY matches DESC""",
    ),
    (
        "How many games did Nila win last week?",
        """SELECT u.id, u.first_name, u.username, COUNT(*) FILTER (WHERE s.place = 1) AS wins, COUNT(*) AS finished_games
FROM users u
JOIN match_seats s ON s.user_id = u.id
JOIN matches m ON m.id = s.match_id AND m.status = 'finished'
WHERE (u.first_name ILIKE '%nila%' OR u.username ILIKE '%nila%')
  AND m.updated_at >= date_trunc('week', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ} - interval '7 days'
  AND m.updated_at <  date_trunc('week', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
GROUP BY u.id, u.first_name, u.username
ORDER BY wins DESC""",
    ),
    (
        "Top 10 players by wins with win rate",
        """SELECT u.id, u.first_name, u.username, u.games_won AS wins, u.games_played AS games,
       ROUND(100.0 * u.games_won / NULLIF(u.games_played, 0), 1) AS win_rate_pct
FROM users u
WHERE NOT u.is_bot AND u.games_played > 0
ORDER BY u.games_won DESC, win_rate_pct DESC
LIMIT 10""",
    ),
    (
        "Top 5 players by knockouts this month",
        """SELECT u.id, u.first_name, COUNT(*) AS knocks
FROM knockouts k
JOIN users u ON u.id = k.attacker_user_id
WHERE k.taken
  AND k.created_at >= date_trunc('month', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
GROUP BY u.id, u.first_name
ORDER BY knocks DESC
LIMIT 5""",
    ),
    (
        "How many dice were rolled today and how many sixes?",
        """SELECT COUNT(*) AS rolls, COUNT(*) FILTER (WHERE value = 6) AS sixes
FROM dice_rolls
WHERE created_at >= date_trunc('day', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}""",
    ),
    (
        "Which fantasy cards are picked the most?",
        """SELECT d.picked AS card_id, c.name, COUNT(*) AS picks
FROM card_draws d
LEFT JOIN cards c ON c.id = d.picked
GROUP BY d.picked, c.name
ORDER BY picks DESC
LIMIT 20""",
    ),
    (
        "How many coins are in circulation?",
        """SELECT COALESCE(SUM(b.amount), 0) AS coins_in_circulation, COUNT(*) FILTER (WHERE b.amount > 0) AS holders
FROM balances b
JOIN users u ON u.id = b.user_id
WHERE NOT u.is_bot AND b.currency = 'coins'""",
    ),
    (
        "Coin faucets and sinks in the last 30 days by reason",
        """SELECT reason,
       SUM(delta) FILTER (WHERE delta > 0) AS credited,
       -SUM(delta) FILTER (WHERE delta < 0) AS debited,
       SUM(delta) AS net,
       COUNT(*) AS movements
FROM balance_history
WHERE created_at >= now() - interval '30 days'
GROUP BY reason
ORDER BY ABS(SUM(delta)) DESC""",
    ),
    (
        "Who spent the most coins on rerolls?",
        """SELECT u.id, u.first_name, -SUM(h.delta) AS spent, COUNT(*) AS rerolls
FROM balance_history h
JOIN users u ON u.id = h.user_id
WHERE h.reason = 'reroll'
GROUP BY u.id, u.first_name
ORDER BY spent DESC
LIMIT 10""",
    ),
    (
        "Richest players",
        """SELECT u.id, u.first_name, u.username, b.amount AS coins
FROM balances b
JOIN users u ON u.id = b.user_id
WHERE NOT u.is_bot AND b.currency = 'coins'
ORDER BY b.amount DESC
LIMIT 10""",
    ),
    (
        "Stars revenue per day this month",
        """SELECT (paid_at AT TIME ZONE {TZ})::date AS day, SUM(stars) AS stars, SUM(coins) AS coins_sold,
       COUNT(DISTINCT user_id) AS buyers
FROM star_payments
WHERE status = 'paid'
  AND paid_at >= date_trunc('month', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}
GROUP BY 1
ORDER BY 1""",
    ),
    (
        "Pending and claimed gifts",
        """SELECT status, COUNT(*) AS transfers, SUM(amount) AS coins
FROM transfers
GROUP BY status
ORDER BY transfers DESC""",
    ),
    (
        "Who referred the most friends?",
        """SELECT u.id, u.first_name, COUNT(*) AS friends_referred
FROM balance_history h
JOIN users u ON u.id = h.user_id
WHERE h.reason = 'referral' AND h.meta->>'role' = 'referrer'
GROUP BY u.id, u.first_name
ORDER BY friends_referred DESC
LIMIT 10""",
    ),
    (
        "Average match duration and players for finished games this week",
        """SELECT COUNT(*) AS matches,
       ROUND(AVG(EXTRACT(EPOCH FROM (m.updated_at - m.created_at)) / 60.0)::numeric, 1) AS avg_minutes,
       ROUND(AVG(p.humans)::numeric, 2) AS avg_humans
FROM matches m
JOIN LATERAL (SELECT COUNT(*) AS humans FROM match_seats s WHERE s.match_id = m.id AND s.user_id IS NOT NULL) p ON true
WHERE m.status = 'finished' AND NOT m.is_bot_table
  AND m.updated_at >= date_trunc('week', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ}""",
    ),
    (
        "Current value of every admin setting",
        """SELECT DISTINCT ON (key) key, value, created_at AS set_at
FROM app_configs
WHERE deleted_at IS NULL
ORDER BY key, created_at DESC, id DESC""",
    ),
    (
        "Players by language",
        """SELECT COALESCE(language_code, 'unknown') AS language, COUNT(*) AS players
FROM users
WHERE NOT is_bot
GROUP BY 1
ORDER BY players DESC""",
    ),
]
