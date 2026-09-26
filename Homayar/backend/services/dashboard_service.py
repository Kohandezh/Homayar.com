"""Dashboard service — post-call KPIs and metrics."""
from backend.database.neo4j_connection import Neo4jConnection


def get_kpis() -> dict:
    query = """
    MATCH (c:Call)
    WITH count(c) AS total_calls,
         count(CASE WHEN c.status = 'completed' THEN 1 END) AS completed_calls,
         avg(c.duration_seconds) AS avg_duration
    OPTIONAL MATCH (c2:Call)
    WHERE c2.status = 'completed' AND c2.created_at > datetime() - duration({days: 1})
    WITH total_calls, completed_calls, avg_duration,
         count(c2) AS calls_today
    OPTIONAL MATCH (q:QCReport)
    WITH total_calls, completed_calls, avg_duration, calls_today,
         avg(q.overall_score) AS avg_qc_score,
         avg(CASE WHEN q.sentiment = 'negative' THEN 1.0 ELSE 0.0 END) AS negative_ratio
    OPTIONAL MATCH (p:ProblemDetection)
    WHERE p.severity IN ['high', 'critical']
    WITH total_calls, completed_calls, avg_duration, calls_today, avg_qc_score,
         count(p) AS open_issues
    OPTIONAL MATCH (b:BusinessInsight)
    WITH total_calls, completed_calls, avg_duration, calls_today, avg_qc_score,
         open_issues,
         count(CASE WHEN b.churn_risk = 'high' THEN 1 END) AS churn_risk_calls,
         count(CASE WHEN b.upsell_signal = true THEN 1 END) AS upsell_opportunities,
         avg(b.estimated_nps) AS avg_nps
    RETURN total_calls, calls_today, avg_qc_score, avg_duration,
           COALESCE(avg_nps, 0) AS avg_nps,
           open_issues, churn_risk_calls, upsell_opportunities
    """
    result = Neo4jConnection.execute_query(query)
    if result:
        r = result[0]
        return {
            "total_calls": r.get("total_calls", 0) or 0,
            "calls_today": r.get("calls_today", 0) or 0,
            "avg_qc_score": round(r.get("avg_qc_score", 0) or 0, 3),
            "avg_duration_seconds": round(r.get("avg_duration", 0) or 0, 1),
            "avg_nps": round(r.get("avg_nps", 0) or 0, 1),
            "open_issues": r.get("open_issues", 0) or 0,
            "churn_risk_calls": r.get("churn_risk_calls", 0) or 0,
            "upsell_opportunities": r.get("upsell_opportunities", 0) or 0,
        }
    return {"total_calls": 0, "calls_today": 0, "avg_qc_score": 0, "avg_duration_seconds": 0,
            "avg_nps": 0, "open_issues": 0, "churn_risk_calls": 0, "upsell_opportunities": 0}


def get_qc_trend(days: int = 7) -> list:
    """Average QC score per day over the last N days."""
    query = """
    MATCH (c:Call)-[:HAS_QC_REPORT]->(q:QCReport)
    WHERE c.analyzed_at > datetime() - duration({days: $days})
    WITH c.analyzed_at AS ts, q.overall_score AS score
    ORDER BY ts
    RETURN ts, score
    LIMIT 200
    """
    results = Neo4jConnection.execute_query(query, {"days": days})
    return [{"timestamp": str(r["ts"]), "quality": r["score"]} for r in results]


def get_sentiment_distribution() -> list:
    """Count of calls by sentiment."""
    query = """
    MATCH (q:QCReport)
    RETURN q.sentiment AS sentiment, count(*) AS count
    ORDER BY count DESC
    """
    results = Neo4jConnection.execute_query(query)
    return [{"sentiment": r["sentiment"], "count": r["count"]} for r in results]


def get_issues_by_category() -> list:
    """Count of problems by issue category."""
    query = """
    MATCH (p:ProblemDetection)
    RETURN p.issue_category AS category, count(*) AS count,
           count(CASE WHEN p.severity IN ['high', 'critical'] THEN 1 END) AS critical
    ORDER BY count DESC
    """
    results = Neo4jConnection.execute_query(query)
    return [{"category": r["category"], "count": r["count"], "critical": r["critical"]} for r in results]


def get_recent_calls(limit: int = 20) -> list:
    """Recent calls with QC score and sentiment."""
    query = """
    MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)
    OPTIONAL MATCH (c)-[:HAS_QC_REPORT]->(q:QCReport)
    OPTIONAL MATCH (c)-[:HAS_INSIGHT]->(b:BusinessInsight)
    RETURN c.id AS id, a.name AS agent_name, c.direction AS direction,
           c.language AS language, c.duration_seconds AS duration_seconds,
           c.status AS status, c.file_name AS file_name,
           c.created_at AS created_at,
           q.overall_score AS overall_qc_score,
           q.sentiment AS sentiment,
           b.churn_risk AS churn_risk,
           b.upsell_signal AS upsell_signal
    ORDER BY c.created_at DESC
    LIMIT $limit
    """
    return Neo4jConnection.execute_query(query, {"limit": limit})


def get_call_problems_bulk() -> list:
    """Bulk fetch all completed calls with their problems — single query."""
    query = """
    MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)
    MATCH (c)-[:HAS_PROBLEM]->(p:ProblemDetection)
    WHERE c.status = 'completed'
    RETURN c.id AS call_id, a.name AS agent_name, c.created_at AS created_at,
           c.language AS language,
           p.root_cause AS root_cause, p.issue_category AS issue_category,
           p.severity AS severity, p.system_failures AS system_failures,
           p.repeated_issue AS repeated_issue, p.impact_scope AS impact_scope
    ORDER BY c.created_at DESC
    """
    results = Neo4jConnection.execute_query(query)
    return [
        {
            "call_id": r["call_id"],
            "agent_name": r["agent_name"],
            "created_at": r["created_at"],
            "language": r["language"],
            "incident": {
                "root_cause": r["root_cause"],
                "issue_category": r["issue_category"],
                "severity": r["severity"],
                "system_failures": r["system_failures"],
                "repeated_issue": r["repeated_issue"],
                "impact_scope": r["impact_scope"],
            },
        }
        for r in results
    ]


def get_call_qc_details_bulk() -> list:
    """Bulk fetch all completed calls with QC details — for analytics."""
    query = """
    MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)
    MATCH (c)-[:HAS_QC_REPORT]->(q:QCReport)
    WHERE c.status = 'completed'
    RETURN c.id AS call_id, a.name AS agent_name, c.language AS language,
           q.overall_score AS overall_score, q.empathy AS empathy,
           q.problem_solving AS problem_solving,
           q.communication_clarity AS communication_clarity,
           q.sentiment AS sentiment
    ORDER BY a.name
    """
    return Neo4jConnection.execute_query(query)
