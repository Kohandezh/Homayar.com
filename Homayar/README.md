# Homayar — Post-Call Voice Intelligence Platform

AI-powered post-call analysis engine for call centers. Upload call recordings,
get automated quality control, root cause detection, and business insights.

## What It Does

Upload a call recording → Homayar transcribes it → runs 4 AI analysis agents →
generates a full report with QC scores, problem detection, and business recommendations.

### 4 Analysis Agents

| Agent | What It Does |
|-------|-------------|
| **QC Agent** | Scores empathy, problem-solving, clarity, tone, call closing, silence handling |
| **Problem Detection** | Identifies root causes, system failures, issue severity, repeated problems |
| **Business Insight** | Detects upsell signals, churn risk, NPS, revenue opportunities, satisfaction trends |
| **QC Teacher** | Generates training packages from agent weaknesses with behavioral recommendations |

### Bilingual Support

Full Persian (FA) and English (EN) analysis. RTL layout auto-switches. All AI prompts
are bilingual — your agents can speak Farsi and still get accurate analysis.

## Architecture

```
VOIP Call → Audio Saved → Homayar → STT (Whisper) → LLM Analysis → Dashboard
                                                          ↓
                                                   QC Report
                                                   Problem Detection
                                                   Business Insight
                                                   Training Package
```

**Stack:** Python/FastAPI · React/TypeScript · Neo4j · Multi-provider LLM

## Features

- **Call Reports** — Full analysis with score bars, root cause tables, business insights, transcript
- **User Management** — Role-based access (admin/supervisor/agent) with CRUD operations

## Quick Start

### Prerequisites

- Python 3.12 (>=3.12,<3.14)
- Node.js 18+
- Docker (for Neo4j)

### One-Command Startup

```bash
make up
```

This starts Neo4j + Backend + Frontend.

### Manual Startup

```bash
# 1. Start Neo4j
make neo4j-up

# 2. Seed with sample data (6 agents, 22 calls)
make seed

# 3. Start backend (port 8001)
make backend

# 4. Start frontend (port 5174)
make frontend
```

### Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5174 |
| API Docs | http://localhost:8001/docs |
| Neo4j Browser | http://localhost:7474 |

## Configuration

Copy `.env.example` to `.env` and configure:

```env
# Database
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=your-local-password   # docker-compose.yml's Neo4j needs this; the backend/seed may use NEO4J_PASSWORD_FILE instead (docs/operations/deployment.md)

# LLM Provider (required for real analysis)
LLM_PROVIDER=auto          # auto | gapgpt | zai
ZAI_API_KEY=your-key
GAPGPT_API_KEY=your-key

# Auth
JWT_SECRET=change-this-in-production
```

## API Endpoints

The product API is served under `/api/v1`; its route inventory is in [docs/security/authorization-matrix.md](docs/security/authorization-matrix.md) (§6).

The legacy dashboard, graph, Copilot, VoIP webhook and LDAP routers, their services and settings are retired: see [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md).

## Project Structure

```
homayar/
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Settings (env-based)
│   ├── database/               # SQLAlchemy models, RLS, Neo4j projection, dev seed
│   ├── migrations/             # Alembic revisions (single linear head)
│   ├── routers/v1/             # The /api/v1 routers (route inventory: authorization matrix)
│   ├── security/               # Tenant context, authorization, tokens
│   ├── services/               # Domain services
│   ├── jobs/                   # Processing pipeline, outbox, leases
│   ├── providers/              # Speech/LLM/TTS provider adapters and registry
│   ├── voice/, voicemail/, locations/, speech/, privacy/
│   └── tests/                  # pytest suite (PostgreSQL-, Redis- and ffmpeg-marked groups)
├── frontend/
│   └── src/
│       ├── App.tsx             # Routes + auth guard
│       ├── components/
│       │   ├── auth/           # Login page
│       │   ├── dashboard/      # KPI cards, charts
│       │   ├── calls/          # Call list + upload
│       │   ├── reports/        # Full call report + PDF
│       │   ├── incidents/      # Severity-filtered incidents
│       │   ├── analytics/      # Agent comparison charts
│       │   ├── users/          # User management table
│       │   ├── graph/          # Interactive graph explorer
│       │   └── copilot/        # AI chat interface
│       ├── lib/                # API client, hooks, i18n, auth
│       └── types/              # TypeScript interfaces
├── docker-compose.yml          # Neo4j
├── Makefile                    # All commands
└── .env.example                # Configuration template
```

## Security

- JWT authentication on all API routes
- bcrypt password hashing
- Role-based access control (admin, supervisor, agent)
- Per-scope rate limits (uploads, API keys, provider calls)
- Global exception handler (no stack traces leaked)

## License

Proprietary — All rights reserved.
