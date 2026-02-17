# ML Design Spec

Last updated: 2026-02-16

---

## 1. Architecture

Market-scoped ML:
- market
- model_name
- lookback

Current model:
Ridge regression (ridge_v1)

---

## 2. Feature Engineering v1

- rolling mean (N games)
- rolling stddev
- rolling median (optional)
- usage metrics
- home/away flag

Features must be reproducible from player_game_stats.

---

## 3. Labels

y = realized market value for that game.

Example:
rec_yds -> actual receiving yards.

---

## 4. Training Contract

Input:
features_{market} + labels_{market}

Output:
artifact bundle including:
- model
- metadata.json
  - market
  - model_name
  - lookback
  - feature list
  - train_rows
  - train_start
  - train_end
  - created_at

---

## 5. Inference Contract

Steps:
1. Load artifact bundle
2. Recompute features with same lookback
3. Apply model
4. Return projection

Failure modes:
- unsupported_market
- insufficient_data
- artifact_missing

---

## 6. Evaluation

Track:
- MAE per market
- MAE per position group

Store under:
data/eval/

---

## 7. Roadmap

- Market-specific feature tables
- Label tables
- Model registry structure
- Opponent context features
- Probabilistic outputs

