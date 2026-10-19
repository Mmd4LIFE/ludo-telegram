CREATE DATABASE IF NOT EXISTS ludo;

CREATE TABLE IF NOT EXISTS ludo.rolls
(
    id UInt64,
    match_id UInt64,
    user_id UInt64,
    seat UInt8,
    value UInt8,
    turn UInt32,
    created_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree
ORDER BY id;

CREATE TABLE IF NOT EXISTS ludo.knocks
(
    id UInt64,
    match_id UInt64,
    turn UInt32,
    attacker_user_id UInt64,
    attacker_seat UInt8,
    victim_user_id UInt64,
    victim_seat UInt8,
    taken UInt8,
    created_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree
ORDER BY id;
