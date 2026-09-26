"""Issue aggregation service — clusters ProblemDetection data into aggregated insights."""
import json
from backend.database.neo4j_connection import Neo4jConnection
from backend.services.llm_router import call_llm, parse_json


def _get_problems_from_db() -> list:
    """Fetch all problem detection records from Neo4j."""
    query = """
    MATCH (c:Call)-[:HAS_PROBLEM]->(p:ProblemDetection)
    OPTIONAL MATCH (a:CallAgent)-[:MADE_CALL]->(c)
    RETURN p.root_cause AS root_cause,
           p.issue_category AS category,
           p.severity AS severity,
           p.system_failures AS system_failures,
           p.repeated_issue AS repeated,
           p.impact_scope AS impact_scope,
           a.name AS agent,
           c.created_at AS call_date
    ORDER BY c.created_at DESC
    """
    return Neo4jConnection.execute_query(query)


def _build_aggregation_prompt(problems: list) -> str:
    """Build prompt for LLM aggregation."""
    problems_str = json.dumps(problems[:50], ensure_ascii=False, indent=2)
    return f"""Analyze these call center problem reports and aggregate them into clusters.
Group similar issues together, count occurrences, and determine overall severity.

Problem reports:
{problems_str}

Return a JSON array of aggregated issues:
[
  {{
    "problem": "short description of the grouped issue",
    "count": number_of_occurrences,
    "severity": "low|medium|high|critical",
    "category": "technical|billing|account|product|service",
    "affected_agents": ["agent1", "agent2"],
    "trend": "increasing|stable|decreasing",
    "recommended_action": "brief action item"
  }}
]

Return ONLY the JSON array, nothing else."""


async def aggregate_issues() -> list:
    """Aggregate all problems into clustered insights using LLM."""
    problems = _get_problems_from_db()
    if not problems:
        return []

    try:
        prompt = _build_aggregation_prompt(problems)
        raw = await call_llm(
            "You are a call center analytics expert. Aggregate and cluster problem reports.",
            prompt,
            agent="insight",
        )
        if raw:
            result = parse_json(raw)
            if result:
                return result
    except Exception:
        pass

    return _simple_aggregate(problems)


def _simple_aggregate(problems: list) -> list:
    """Simple aggregation without LLM — group by category."""
    from collections import Counter
    cat_counts = Counter()
    cat_severity = {}
    for p in problems:
        cat = p.get("category", "unknown")
        cat_counts[cat] += 1
        sev = p.get("severity", "low")
        sev_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        if cat not in cat_severity or sev_order.get(sev, 0) > sev_order.get(cat_severity[cat], 0):
            cat_severity[cat] = sev

    return [
        {
            "problem": f"{cat.title()} issues",
            "count": count,
            "severity": cat_severity.get(cat, "low"),
            "category": cat,
            "affected_agents": [],
            "trend": "stable",
            "recommended_action": f"Review {cat} processes",
        }
        for cat, count in cat_counts.most_common()
    ]
