DROP TABLE IF EXISTS player_game_stats;

CREATE TABLE player_game_stats (
    player_id TEXT,
    game_id TEXT,
    season INT,
    week INT,
    season_type TEXT,
    game_date DATE,
    opponent TEXT,
    team TEXT,
    position TEXT,
    position_group TEXT,

    passing_yards FLOAT,
    passing_tds FLOAT,
    passing_interceptions FLOAT,
    attempts FLOAT,
    completions FLOAT,
    passing_air_yards FLOAT,
    passing_yards_after_catch FLOAT,
    passing_first_downs FLOAT,

    rushing_yards FLOAT,
    rushing_tds FLOAT,
    carries FLOAT,
    rushing_first_downs FLOAT,

    receiving_yards FLOAT,
    receiving_tds FLOAT,
    receptions FLOAT,
    targets FLOAT,
    receiving_air_yards FLOAT,
    receiving_yards_after_catch FLOAT,

    def_tackles_solo FLOAT,
    def_sacks FLOAT,
    def_interceptions FLOAT,

    fg_made FLOAT,
    fg_att FLOAT,
    fg_long FLOAT,

    punt_return_yards FLOAT,
    kickoff_return_yards FLOAT,

    PRIMARY KEY (player_id, game_id)
);
