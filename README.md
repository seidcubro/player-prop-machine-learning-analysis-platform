# PropSignal — Player Prop Machine Learning Platform

A full-stack machine learning platform for analyzing and predicting NFL player prop outcomes using real data, engineered features, and model-driven insights.

---

## 🚀 Overview

PropSignal is a production-style system designed to:

- Ingest real NFL data (nflverse-based pipelines)
- Engineer player, team, and opponent-level features
- Train machine learning models for specific prop markets
- Track model performance and artifacts
- Serve predictions through a backend API
- (In progress) Deliver insights through a modern frontend interface

The goal is to move beyond simple averages and build context-aware, signal-driven projections.

---

## 🏗️ Architecture

### Apps
- `apps/web/`  
  React + Vite frontend (currently being rebuilt)

### Services
- `services/api/`  
  FastAPI backend exposing data and model endpoints

- `services/training/`  
  Model training pipeline, feature processing, and evaluation

- `services/inference/`  
  (Planned) Dedicated inference service for production predictions

### Jobs
- `jobs/ingestion/`  
  NFL data ingestion pipeline (nflverse)

- `jobs/features/`  
  Feature generation and transformation

- `jobs/etl/`  
  Supporting ETL workflows

- `jobs/training/`  
  Training job orchestration

### Database
- PostgreSQL (Dockerized)

Core table:
- `player_market_features` → feature store for all player/market combinations

---

## 🧠 Machine Learning Pipeline

Training flow:

1. Load market metadata  
2. Load feature data from Postgres  
3. Flatten JSON features into tabular format  
4. Add derived features (Python-side)  
5. Time-based train/test split  
6. Train model (RandomForest baseline)  
7. Save:
   - model artifact (`.joblib`)
   - metadata (`.json`)
   - evaluation metrics  

---

## 📊 Current Model Example

Market: `rec_yds` (Receiving Yards)

- Model: RandomForest (`rf_v6`)
- R²: ~0.38  
- MAE: ~16  
- RMSE: ~23  

Top features:
- rolling mean  
- targets_mean  
- weighted_mean  
- team_pass_attempts  
- target_share (derived)  

---

## ⚙️ Feature System

### Base Features (DB)
- mean  
- stddev  
- weighted_mean  
- trend  
- recs_mean  
- recs_trend  

### Context Features (JSON → flattened)
- opponent defensive stats  
- rolling opponent allowances  
- targets and usage metrics  
- team-level passing volume  

### Derived Features (Python)
- `target_share = targets_mean / team_pass_attempts`  

---

## ⚠️ Current Limitations

- Heavy reliance on rolling averages (mean dominance)  
- Limited game environment context  
- Redundant feature relationships  
- No Vegas or implied scoring signals  
- Frontend is outdated relative to backend/data  

---

## 🎯 Current Focus

- Reduce reliance on rolling averages  
- Introduce contextual features:
  - team strength  
  - game environment  
  - usage efficiency  
- Improve model signal quality  
- Build prediction endpoints  
- Rebuild frontend into a modern prop analysis dashboard  

---

## 🐳 Local Development

Start the full stack:

```bash
docker compose up --build
```

API health check:

```bash
http://localhost:8000/health
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

---

## 🧪 Training a Model

```bash
docker compose build training
docker compose run --rm -e MARKET_CODE=rec_yds -e MODEL_NAME=rf_v7 training
```

---

## 🧾 Running SQL

```bash
Get-Content db\inspection\inspect_pmf.sql | docker exec -i player-prop-platform-postgres-1 psql -U app -d app
```

---

## 📁 Database SQL Structure

- `db/migrations/` → schema + registry changes  
- `db/views/` → feature-building SQL views  
- `db/backfills/` → data repair and injections  
- `db/inspection/` → debugging and validation  

---

## 🧠 Philosophy

Most prop tools rely on simple averages and surface-level stats.

PropSignal is built to:
- model context, not just history  
- identify real signal vs noise  
- evolve into a system that surfaces true betting edges  

---

## 📌 Status

The system is currently in the feature engineering phase.

Core infrastructure is complete.  
Active work is focused on improving model intelligence and signal quality.

---

## 🔮 Roadmap

- [ ] Add game environment and scoring context  
- [ ] Introduce Vegas-style features  
- [ ] Build prediction API endpoints  
- [ ] Create prop cards and edge detection UI  
- [ ] Add model comparison system  
- [ ] Deploy inference service  

---

## 🧑‍💻 Author

**Seid Cubro**

Cloud Computing student and builder focused on real-world machine learning systems, data engineering, and sports analytics.

Designed and developed PropSignal as a full-stack platform to move beyond surface-level sports data and toward context-driven, model-based insights.

Passionate about building systems that combine:
- data engineering  
- machine learning  
- real-world decision-making  

LinkedIn: www.linkedin.com/in/seid-cubro

Personal Website: https://seidcubro.vercel.app

Email: seidcubro754@gmail.com

---