"""Call service — upload, list, detail, analysis."""
import os
import uuid
import shutil
from datetime import datetime
from backend.database.neo4j_connection import Neo4jConnection
from backend.config import get_settings


ALLOWED_EXT = {".wav", ".mp3", ".ogg", ".m4a", ".flac"}


def save_upload_file(upload_file, filename: str) -> str:
    """Save uploaded file to disk. Returns the file path."""
    settings = get_settings()
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type {ext} not allowed. Accepted: {', '.join(ALLOWED_EXT)}")

    call_id = str(uuid.uuid4())
    save_name = f"{call_id}{ext}"
    save_path = os.path.join(settings.upload_dir, save_name)

    os.makedirs(settings.upload_dir, exist_ok=True)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(upload_file, f)

    return call_id, save_path, ext


def save_upload_bytes(data: bytes, filename: str):
    """Save uploaded bytes to disk. Returns (call_id, file_path)."""
    settings = get_settings()
    ext = os.path.splitext(filename)[1].lower() if filename else ".wav"
    if ext not in ALLOWED_EXT:
        ext = ".wav"

    call_id = str(uuid.uuid4())
    save_name = f"{call_id}{ext}"
    save_path = os.path.join(settings.upload_dir, save_name)

    os.makedirs(settings.upload_dir, exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(data)

    return call_id, save_path


def create_call_record(call_id: str, agent_id: str, file_path: str, file_name: str,
                       direction: str = "inbound", language: str = "fa",
                       phone_number: str = None) -> dict:
    """Create a Call node in Neo4j."""
    now = datetime.utcnow().isoformat()
    Neo4jConnection.execute_write("""
        CREATE (c:Call {
            id: $id, agent_id: $agent_id, direction: $direction,
            language: $language, duration_seconds: 0, status: 'uploaded',
            file_path: $file_path, file_name: $file_name,
            phone_number: $phone_number,
            created_at: datetime($created_at), analyzed_at: null
        })
    """, {
        "id": call_id, "agent_id": agent_id, "direction": direction,
        "language": language, "file_path": file_path, "file_name": file_name,
        "phone_number": phone_number, "created_at": now,
    })

    # Link agent to call
    Neo4jConnection.execute_write("""
        MATCH (a:CallAgent {id: $aid}), (c:Call {id: $cid})
        CREATE (a)-[:MADE_CALL]->(c)
    """, aid=agent_id, cid=call_id)

    return get_call_detail(call_id)


def list_calls(status: str = None, language: str = None, limit: int = 50, offset: int = 0) -> list:
    """List calls with optional filters."""
    filters = []
    params = {"limit": limit, "offset": offset}
    if status:
        filters.append("c.status = $status")
        params["status"] = status
    if language:
        filters.append("c.language = $language")
        params["language"] = language

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call)
        {where}
        OPTIONAL MATCH (c)-[:HAS_QC_REPORT]->(q:QCReport)
        OPTIONAL MATCH (c)-[:HAS_INSIGHT]->(b:BusinessInsight)
        RETURN c.id AS id, a.name AS agent_name, c.direction AS direction,
               c.language AS language, c.duration_seconds AS duration_seconds,
               c.status AS status, c.file_name AS file_name,
               c.created_at AS created_at,
               q.overall_score AS overall_qc_score,
               q.sentiment AS sentiment
        ORDER BY c.created_at DESC
        SKIP $offset LIMIT $limit
    """
    results = Neo4jConnection.execute_query(query, params)
    return results


def get_call_detail(call_id: str) -> dict:
    """Get full call detail with transcript, QC, problems, insights."""
    # Call + agent info
    call_query = """
        MATCH (a:CallAgent)-[:MADE_CALL]->(c:Call {id: $cid})
        RETURN c.id AS id, c.agent_id AS agent_id, a.name AS agent_name,
               c.direction AS direction, c.language AS language,
               c.duration_seconds AS duration_seconds, c.status AS status,
               c.file_path AS file_path, c.file_name AS file_name,
               c.phone_number AS phone_number,
               c.created_at AS created_at, c.analyzed_at AS analyzed_at
    """
    call_data = Neo4jConnection.execute_query(call_query, {"cid": call_id})
    if not call_data:
        return None
    result = {"call": call_data[0]}

    # Transcript
    transcript_query = """
        MATCH (c:Call {id: $cid})-[:HAS_TRANSCRIPT]->(t:Transcript)
        RETURN t.id AS id, t.language AS language, t.segments AS segments, t.full_text AS full_text
    """
    t = Neo4jConnection.execute_query(transcript_query, {"cid": call_id})
    result["transcript"] = t[0] if t else None

    # QC Report
    qc_query = """
        MATCH (c:Call {id: $cid})-[:HAS_QC_REPORT]->(q:QCReport)
        RETURN q
    """
    q = Neo4jConnection.execute_query(qc_query, {"cid": call_id})
    result["qc_report"] = q[0]["q"] if q else None

    # Problem Detection
    prob_query = """
        MATCH (c:Call {id: $cid})-[:HAS_PROBLEM]->(p:ProblemDetection)
        RETURN p
    """
    p = Neo4jConnection.execute_query(prob_query, {"cid": call_id})
    result["problems"] = p[0]["p"] if p else None

    # Business Insight
    insight_query = """
        MATCH (c:Call {id: $cid})-[:HAS_INSIGHT]->(b:BusinessInsight)
        RETURN b
    """
    b = Neo4jConnection.execute_query(insight_query, {"cid": call_id})
    result["insights"] = b[0]["b"] if b else None

    return result


def update_call_status(call_id: str, status: str):
    """Update call processing status."""
    Neo4jConnection.execute_write("""
        MATCH (c:Call {id: $cid})
        SET c.status = $status
    """, cid=call_id, status=status)


def store_transcript(call_id: str, language: str, segments: list, full_text: str):
    """Store transcript for a call."""
    tid = str(uuid.uuid4())
    Neo4jConnection.execute_write("""
        MATCH (c:Call {id: $cid})
        CREATE (t:Transcript {
            id: $tid, language: $language,
            segments: $segments, full_text: $full_text
        })
        CREATE (c)-[:HAS_TRANSCRIPT]->(t)
    """, cid=call_id, tid=tid, language=language,
         segments=[f"{s['speaker']}: {s['text']}" for s in segments],
         full_text=full_text)


def store_analysis(call_id: str, qc: dict, problems: dict, insights: dict,
                   recommended_actions: list, training: dict = None):
    """Store full analysis results for a call in a single transaction."""
    now = datetime.utcnow().isoformat()

    queries = [
        # QC Report
        ("""
        MATCH (c:Call {id: $cid})
        CREATE (q:QCReport {
            id: $qid, overall_score: $overall_score,
            empathy: $empathy, problem_solving: $problem_solving,
            communication_clarity: $communication_clarity,
            call_closing: $call_closing, silence_handling: $silence_handling,
            tone: $tone, sentiment: $sentiment,
            sentiment_progression: $sentiment_progression,
            resolution: $resolution,
            silence_total_seconds: $silence_total_seconds
        })
        CREATE (c)-[:HAS_QC_REPORT]->(q)
        """, {"cid": call_id, "qid": str(uuid.uuid4()), **qc}),

        # Problem Detection
        ("""
        MATCH (c:Call {id: $cid})
        CREATE (p:ProblemDetection {
            id: $pid, root_cause: $root_cause,
            issue_category: $issue_category, severity: $severity,
            system_failures: $system_failures,
            repeated_issue: $repeated_issue, impact_scope: $impact_scope
        })
        CREATE (c)-[:HAS_PROBLEM]->(p)
        """, {"cid": call_id, "pid": str(uuid.uuid4()), **problems}),

        # Business Insight
        ("""
        MATCH (c:Call {id: $cid})
        CREATE (b:BusinessInsight {
            id: $bid, upsell_signal: $upsell_signal, churn_risk: $churn_risk,
            customer_intent: $customer_intent,
            revenue_opportunity: $revenue_opportunity,
            conversion_probability: $conversion_probability,
            estimated_nps: $estimated_nps,
            satisfaction_trend: $satisfaction_trend
        })
        CREATE (c)-[:HAS_INSIGHT]->(b)
        """, {"cid": call_id, "bid": str(uuid.uuid4()), **insights}),

        # Mark call as completed
        ("""
        MATCH (c:Call {id: $cid})
        SET c.status = 'completed', c.analyzed_at = datetime($now)
        """, {"cid": call_id, "now": now}),
    ]

    # Add training node if provided
    if training:
        queries.append((
            """
            MATCH (c:Call {id: $cid})
            CREATE (t:TrainingPackage {
                id: $tid, training_title: $training_title,
                weakness_areas: $weakness_areas,
                training_tips: $training_tips,
                behavioral_recommendations: $behavioral_recommendations,
                estimated_improvement_days: $estimated_improvement_days,
                priority: $priority
            })
            CREATE (c)-[:HAS_TRAINING]->(t)
            """,
            {"cid": call_id, "tid": str(uuid.uuid4()), **training},
        ))

    Neo4jConnection.execute_write_transaction(queries)
