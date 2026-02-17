# Data Model Spec

Last updated: 2026-02-16
Database: PostgreSQL

---

## 1. players

Columns:
- id (PK)
- first_name
- last_name
- position (nullable)
- team (nullable)
- external_id (nullable)

Indexes:
- (last_name, first_name)
- optional trigram index for search

---

## 2. games

Columns:
- id (PK)
- season
- week
- game_id_external
- home_team
- away_team
- game_date

---

## 3. player_game_stats

Columns:
- id (PK)
- player_id (FK players.id)
- game_id
- rec_yds
- rush_yds
- pass_yds
- receptions
- targets
- rush_att
- snap_pct

Indexes:
- (player_id, game_id)
- (season, week)

---

## 4. Feature Tables (Planned)

features_{market}

Example:
features_rec_yds

Columns:
- player_id
- game_id
- rolling_mean_N
- rolling_std_N
- home_indicator
- usage_features

---

## 5. Label Tables (Planned)

labels_{market}

Example:
labels_rec_yds

Columns:
- player_id
- game_id
- y (float)

Alignment:
Features and labels must join 1:1 on (player_id, game_id)

---

## 6. Model Artifacts

Location:
data/models/

Each bundle must include:
- serialized model
- metadata.json with:
  - market
  - model_name
  - lookback
  - feature list
  - training window
  - created_at

Integrity:
- No duplicate (player_id, game_id)
- external_id unique when present

