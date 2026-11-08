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

### Demo seeding

`make demo-seed` fills the product database with one believable demo workspace, so the
product console (Overview, Meetings, Members, Usage, Settings and Audit) can be shown to
companies without real uploads.

```bash
uv run alembic upgrade head    # the schema must exist first
ENVIRONMENT=development make demo-seed
```

What it creates:

- One organization and one workspace with a Persian name, on the `business` plan, with
  audio and concurrent-job quotas for the current month.
- One account per workspace role (owner, admin, member, viewer) on the reserved
  `demo.example.com` domain. The admin, member and viewer join through the invitation flow.
- Four teams: Sales and Support (a supervisor and one agent each), and two call teams,
  Call Center and Customer Care, that share the five demo agents.
- 270 short meetings in Persian and English, all processed by the real job processor:
  - 240 scripted customer calls (HOM-365), 30 per closed Tehran week for each call team,
    whose scripted intelligence returns a real taxonomy extraction (intent, topics,
    outcome) cited to the call's turns. They are dated in the 4 closed weeks before the
    current week, so the `/trends` page shows real percent values and moving trend lines
    for the workspace and both call teams, with some outcome rows still under the
    30-call sample minimum ("Too few calls") on purpose.
    The owner ruled on 2026-11-09 that these 240 calls are stored as non-mock analysis:
    the trend views never count a mock analysis, and the product rule is not changed for
    a demo. For these 240 calls only: the call report has no mock notice and can be
    complete; the export has no "Mock output: yes" line; coverage counts them as
    evaluated; the console shows no mock badge; the processing audit records
    `mock: false`. What still marks them as demo data: the provider names
    `demo_seed_call_*`, the `example.com` accounts and the generated near-silence
    recordings. The 30 console meetings stay mock.
  - The 30 console meetings of the original demo (HOM-175): every status the console
    shows (completed, partial, failed, processing, queued, one dead-lettered job) with
    transcripts, summaries, action items, evidence and quality data from scripted mock
    providers. Mock analyses are excluded from the trend counts by design, and the
    console meetings are created last, so the meetings, report and evidence pages still
    open a mock demo call.
- Usage events, one API key, two share links, and audit history for all of the above.

The recordings are a few seconds of generated near-silence, unique per meeting (no real
audio, no network calls). A database seeded by an older demo seed must be reset before
this seed runs on it: the plan changed with HOM-365.

The command prints the console URL and the logins. All four accounts share one password:

```text
owner@demo.example.com   Homayar-Demo-1405
admin@demo.example.com   Homayar-Demo-1405
member@demo.example.com  Homayar-Demo-1405
viewer@demo.example.com  Homayar-Demo-1405
```

Running it again changes nothing: every object is found by a fixed key and skipped.
If a run is interrupted while it processes meetings, start again from a fresh database.

It only runs when `ENVIRONMENT` is `development` or `test`. In `production` it prints a
refusal and exits with a non-zero code before it opens a database connection, because demo
accounts with a public password must never exist there. It also refuses `staging`: staging
requires qualified model manifests, which the scripted demo providers do not have.

The two `processing` meetings hold a worker lease. If a Celery worker runs against the same
database, it may pick those jobs up after the lease expires, and the two `queued` jobs when
their dispatch is swept, and process them with the configured providers.

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
