"""AI Copilot service — post-call intelligence assistant."""
from backend.prompts import load_prompt
from backend.services.llm_router import call_llm
from backend.services.dashboard_service import get_kpis

# Predefined Cypher templates keyed by intent
QUERY_TEMPLATES = {
    "call_summary": """
        MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)
        OPTIONAL MATCH (c)-[:HAS_QC_REPORT]->(q:QCReport)
        OPTIONAL MATCH (c)-[:HAS_INSIGHT]->(b:BusinessInsight)
        RETURN c.id AS call_id, a.name AS agent, c.direction AS direction,
               c.duration_seconds AS duration, c.status AS status,
               q.overall_score AS qc_score, q.sentiment AS sentiment,
               q.resolution AS resolution, b.churn_risk AS churn_risk,
               b.estimated_nps AS nps
        ORDER BY c.created_at DESC LIMIT 5
    """,
    "agent_performance": """
        MATCH (a:CallAgent)
        OPTIONAL MATCH (a)-[:MADE_CALL]->(c:Call)-[:HAS_QC_REPORT]->(q:QCReport)
        RETURN a.name AS agent, a.team AS team, a.avg_qc_score AS avg_qc,
               a.total_calls AS total_calls,
               count(c) AS analyzed_calls,
               avg(q.overall_score) AS recent_avg_qc,
               avg(q.empathy) AS avg_empathy
        ORDER BY a.avg_qc_score DESC
    """,
    "quality_issues": """
        MATCH (c:Call)-[:HAS_PROBLEM]->(p:ProblemDetection)
        WHERE p.severity IN ['high', 'critical']
        OPTIONAL MATCH (a:CallAgent)-[:MADE_CALL]->(c)
        RETURN a.name AS agent, p.root_cause AS root_cause,
               p.issue_category AS category, p.severity AS severity,
               p.system_failures AS system_failures, p.repeated_issue AS repeated
        ORDER BY CASE p.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 END
        LIMIT 10
    """,
    "sentiment_analysis": """
        MATCH (c:Call)-[:HAS_QC_REPORT]->(q:QCReport)
        RETURN q.sentiment AS sentiment, count(*) AS count,
               avg(q.overall_score) AS avg_qc
        ORDER BY count DESC
    """,
    "churn_risk": """
        MATCH (c:Call)-[:HAS_INSIGHT]->(b:BusinessInsight)
        WHERE b.churn_risk IN ['medium', 'high']
        OPTIONAL MATCH (a:CallAgent)-[:MADE_CALL]->(c)
        OPTIONAL MATCH (c)-[:HAS_QC_REPORT]->(q:QCReport)
        RETURN a.name AS agent, b.churn_risk AS risk, q.sentiment AS sentiment,
               b.customer_intent AS intent, c.created_at AS call_time
        ORDER BY CASE b.churn_risk WHEN 'high' THEN 0 WHEN 'medium' THEN 1 END
        LIMIT 10
    """,
    "upsell_opportunities": """
        MATCH (c:Call)-[:HAS_INSIGHT]->(b:BusinessInsight)
        WHERE b.upsell_signal = true
        OPTIONAL MATCH (a:CallAgent)-[:MADE_CALL]->(c)
        RETURN a.name AS agent, b.revenue_opportunity AS opportunity,
               b.conversion_probability AS probability,
               b.customer_intent AS intent
        LIMIT 10
    """,
    "category_breakdown": """
        MATCH (c:Call)-[:HAS_PROBLEM]->(p:ProblemDetection)
        RETURN p.issue_category AS category, count(*) AS count,
               avg(CASE p.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                   WHEN 'medium' THEN 2 ELSE 1 END) AS avg_severity
        ORDER BY count DESC
    """,
    "agent_comparison": """
        MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)-[:HAS_QC_REPORT]->(q:QCReport)
        RETURN a.name AS agent, count(c) AS calls,
               avg(q.overall_score) AS avg_qc,
               avg(q.empathy) AS empathy,
               avg(q.problem_solving) AS problem_solving,
               avg(q.communication_clarity) AS clarity
        ORDER BY avg_qc DESC
    """,
}

INTENT_SYSTEM_PROMPT = load_prompt("copilot_intent")
RESPONSE_SYSTEM_PROMPT = load_prompt("copilot_response")


async def classify_intent(message: str) -> str:
    raw = await call_llm(
        INTENT_SYSTEM_PROMPT, message,
        agent="copilot_intent",
        max_tokens=20,
    )
    if not raw:
        return "general"
    intent = raw.strip().lower()
    if intent not in QUERY_TEMPLATES:
        intent = "general"
    return intent


def run_intent_query(intent: str) -> list:
    from backend.database.neo4j_connection import Neo4jConnection
    query = QUERY_TEMPLATES.get(intent)
    if not query:
        return [{"info": "No specific query matched. Showing current KPIs.", "kpis": get_kpis()}]
    return Neo4jConnection.execute_query(query)


async def generate_response(message: str, data: list) -> str:
    data_str = str(data[:20]) if data else "No data available"
    result = await call_llm(
        RESPONSE_SYSTEM_PROMPT,
        f"User question: {message}\n\nSystem data:\n{data_str}",
        agent="copilot_response",
    )
    return result or "I couldn't process that request. Please try again."


async def process_question(message: str) -> dict:
    intent = await classify_intent(message)
    data = run_intent_query(intent)
    response = await generate_response(message, data)
    return {"response": response, "intent": intent, "data": data}
