-- Say a player is out before the injury report does.
--
-- The injury feed is nflverse's copy of the official Wednesday-to-Friday
-- report. News breaks after it: a Saturday downgrade, a Sunday morning
-- scratch, the inactive list ninety minutes before kickoff. Puka Nacua was
-- ruled out of a Monday game and the site was still projecting him for 6.0
-- receptions, because nothing in the feed had said otherwise. The board
-- happened to be safe only because the sportsbooks had already pulled his
-- props, which is luck, not a design.
--
-- So this is the manual lane. A row here is read exactly like a report status,
-- and it wins over one, because a person typing "out" at 11am on game day
-- knows something the Friday report does not.
--
-- Rows expire. A player marked out for one game must not still be out in
-- November: expires_at defaults to two days out, which covers the game being
-- played and then lets the feed take over again.
CREATE TABLE IF NOT EXISTS player_status_overrides (
    id           BIGSERIAL PRIMARY KEY,
    player_id    TEXT NOT NULL,
    player_name  TEXT,
    -- 'Out', 'Doubtful', 'Questionable', or 'Active' to undo a feed status
    -- when a player is cleared after being listed.
    status       TEXT NOT NULL,
    note         TEXT,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '2 days'
);

CREATE INDEX IF NOT EXISTS idx_player_status_overrides_live
    ON player_status_overrides (player_id, expires_at);
