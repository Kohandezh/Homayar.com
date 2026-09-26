from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ─── User Management ─────────────────────────────────────────

class User(BaseModel):
    id: str
    email: str
    name: str
    role: str = "agent"  # admin | supervisor | agent
    extension: Optional[str] = None  # VOIP extension e.g. "101"
    department: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: bool = True
    auth_method: str = "local"  # local | ldap | voip
    created_at: Optional[str] = None
    last_login: Optional[str] = None


class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "agent"
    extension: Optional[str] = None
    department: Optional[str] = None
    phone_number: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    extension: Optional[str] = None
    department: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: Optional[bool] = None


class LoginRequest(BaseModel):
    email: str
    password: str


# ─── Call Center Agent ────────────────────────────────────────

class CallAgent(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    team: Optional[str] = None
    language: str = "fa"
    avg_qc_score: float = 0.0
    total_calls: int = 0


# ─── Call Recording ───────────────────────────────────────────

class Call(BaseModel):
    id: str
    agent_id: str
    agent_name: Optional[str] = None
    direction: str = "inbound"  # inbound | outbound
    language: str = "fa"
    duration_seconds: float = 0.0
    status: str = "uploaded"  # uploaded | transcribing | analyzing | completed | failed
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    phone_number: Optional[str] = None
    created_at: Optional[str] = None
    analyzed_at: Optional[str] = None


class CallListItem(BaseModel):
    id: str
    agent_name: Optional[str] = None
    direction: str
    language: str
    duration_seconds: float
    status: str
    overall_qc_score: Optional[float] = None
    sentiment: Optional[str] = None
    file_name: Optional[str] = None
    created_at: Optional[str] = None


# ─── Transcript ───────────────────────────────────────────────

class TranscriptSegment(BaseModel):
    speaker: str  # "customer" | "agent"
    text: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None

class Transcript(BaseModel):
    call_id: str
    language: str = "fa"
    segments: list[TranscriptSegment] = []
    full_text: Optional[str] = None


# ─── QC Report ────────────────────────────────────────────────

class QCMetrics(BaseModel):
    overall_score: float = 0.0
    empathy: float = 0.0
    problem_solving: float = 0.0
    communication_clarity: float = 0.0
    call_closing: float = 0.0
    silence_handling: float = 0.0
    tone: float = 0.0

class QCReport(BaseModel):
    call_id: str
    metrics: QCMetrics
    sentiment: str = "neutral"  # negative | neutral | positive
    sentiment_progression: Optional[str] = None  # e.g. "negative → positive"
    resolution: str = "unknown"  # successful | unsuccessful | partial | unknown
    silence_total_seconds: float = 0.0
    notes: list[str] = []


# ─── Problem Detection ────────────────────────────────────────

class ProblemDetection(BaseModel):
    call_id: str
    root_cause: Optional[str] = None
    issue_category: Optional[str] = None  # billing, technical, account, product
    severity: str = "low"  # low | medium | high | critical
    system_failures: list[str] = []
    repeated_issue: bool = False
    impact_scope: Optional[str] = None


# ─── Business Insight ─────────────────────────────────────────

class BusinessInsight(BaseModel):
    call_id: str
    upsell_signal: bool = False
    churn_risk: str = "low"  # low | medium | high
    customer_intent: Optional[str] = None
    revenue_opportunity: Optional[str] = None
    conversion_probability: Optional[str] = None
    estimated_nps: Optional[int] = None
    satisfaction_trend: Optional[str] = None  # declining | stable | improving


# ─── Full Call Report ─────────────────────────────────────────

class CallReport(BaseModel):
    call: Call
    transcript: Optional[Transcript] = None
    qc_report: Optional[QCReport] = None
    problems: Optional[ProblemDetection] = None
    insights: Optional[BusinessInsight] = None
    recommended_actions: list[str] = []


# ─── Dashboard KPIs (post-call) ──────────────────────────────

class DashboardKpis(BaseModel):
    total_calls: int = 0
    calls_today: int = 0
    avg_qc_score: float = 0.0
    avg_duration_seconds: float = 0.0
    avg_nps: float = 0.0
    open_issues: int = 0
    churn_risk_calls: int = 0
    upsell_opportunities: int = 0


# ─── Graph (reusable) ────────────────────────────────────────

class GraphNode(BaseModel):
    id: str
    label: str
    name: str
    properties: dict = {}


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ─── Chat / Copilot ──────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str
    data: Optional[dict] = None
