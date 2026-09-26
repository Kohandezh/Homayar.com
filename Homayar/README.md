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

- **Dashboard** — 8 KPI cards, QC trend charts, sentiment distribution, issue tracking
- **Call Reports** — Full analysis with score bars, root cause tables, business insights, transcript
- **PDF Export** — Generate and download professional call reports
- **User Management** — Role-based access (admin/supervisor/agent) with CRUD operations
- **VOIP Integration** — Webhook receivers for Asterisk, 3CX, FreePBX with auto-normalization
- **LDAP/AD Sync** — Authenticate and sync users from Active Directory
- **AI Copilot** — Chat interface to query call data in natural language
- **Graph Explorer** — Interactive Neo4j graph visualization

## Quick Start

### Prerequisites

- Python 3.9+
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

**Default login:** `admin@homayar.io` / `admin123`

## Configuration

Copy `.env.example` to `.env` and configure:

```env
# Database
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=homayar_dev_2024

# LLM Provider (required for real analysis)
LLM_PROVIDER=auto          # auto | gapgpt | zai
ZAI_API_KEY=your-key
GAPGPT_API_KEY=your-key

# Optional: per-agent model override
LLM_MODEL_QC=gpt-4
LLM_MODEL_PROBLEM=gpt-4
LLM_MODEL_INSIGHT=gpt-4
LLM_MODEL_TEACHER=gpt-4

# Auth
JWT_SECRET=change-this-in-production
JWT_EXPIRY_HOURS=24

# LDAP / Active Directory (optional)
LDAP_SERVER=ad.company.com
LDAP_PORT=636
LDAP_BIND_DN=CN=service_account,DC=company,DC=com
LDAP_BIND_PASSWORD=your-password
LDAP_SEARCH_BASE=DC=company,DC=com

# VOIP Webhook (optional)
VOIP_WEBHOOK_SECRET=your-shared-secret
```

## API Endpoints

29 endpoints across 7 routers:

| Router | Endpoints | Purpose |
|--------|-----------|---------|
| `/api/auth` | 6 | Login, user CRUD, JWT management |
| `/api/dashboard` | 8 | KPIs, trends, aggregated metrics |
| `/api/calls` | 6 | Upload, list, detail, analyze, PDF export |
| `/api/graph` | 4 | Entity search, neighborhood, stats |
| `/api/copilot` | 1 | AI chat interface |
| `/api/webhook` | 2 | VOIP call event ingestion |
| `/api/ldap` | 2 | LDAP connectivity test and sync |

## Project Structure

```
homayar/
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Settings (env-based)
│   ├── database/
│   │   ├── neo4j_connection.py  # Neo4j driver singleton
│   │   ├── models.py           # Pydantic models (21 types)
│   │   └── seed.py             # Sample data generator
│   ├── middleware/
│   │   └── auth.py             # JWT validation + RBAC
│   ├── routers/
│   │   ├── auth.py             # Auth + user management
│   │   ├── calls.py            # Call upload, list, analyze, PDF
│   │   ├── dashboard.py        # Dashboard metrics
│   │   ├── graph.py            # Graph exploration
│   │   ├── copilot.py          # AI copilot chat
│   │   ├── webhook.py          # VOIP event webhooks
│   │   └── ldap_sync.py        # LDAP sync endpoints
│   ├── services/
│   │   ├── analysis_service.py  # 4-agent LLM pipeline
│   │   ├── llm_router.py       # Multi-provider LLM routing
│   │   ├── call_service.py     # Call CRUD
│   │   ├── dashboard_service.py # Dashboard queries
│   │   ├── copilot_service.py  # Copilot intent + response
│   │   ├── pdf_service.py      # PDF report generation
│   │   ├── user_service.py     # User CRUD + auth
│   │   ├── ad_service.py       # LDAP/AD integration
│   │   ├── graph_service.py    # Graph queries
│   │   └── logging_service.py  # Structured JSON logging
│   ├── prompts/                # Bilingual prompt templates (FA/EN)
│   └── tests/                  # pytest test suite (22 tests)
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

## VOIP Webhook Integration

Homayar auto-normalizes call events from major VOIP systems:

```bash
# Asterisk / FreePBX / Generic
curl -X POST http://your-server:8001/api/webhook/voip/call-event \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret" \
  -d '{"extension": "101", "caller_id": "09121234567", "direction": "inbound", "duration": 120, "recording_base64": "..."}'

# 3CX
curl -X POST http://your-server:8001/api/webhook/voip/call-event \
  -H "Content-Type: application/json" \
  -d '{"CallId": "abc-123", "Extension": "101", "CallerId": "09129999999", "Direction": "Inbound", "Duration": 60}'
```

Agents are auto-created on first call by extension number.

## Security

- JWT authentication on all API routes
- bcrypt password hashing
- Role-based access control (admin, supervisor, agent)
- Rate limiting (60 req/min default)
- Cypher injection protection (label whitelist, depth clamping)
- Webhook secret verification
- Global exception handler (no stack traces leaked)

## License

Proprietary — All rights reserved.
