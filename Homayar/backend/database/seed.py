"""Seed script — post-call voice intelligence domain.

Development only. Run from `Homayar/` with `uv run python -m backend.database.seed`. The Neo4j
URI, user and password come from Settings built when the seed runs (NEO4J_URI, NEO4J_USER and
NEO4J_PASSWORD or NEO4J_PASSWORD_FILE, from the environment or the .env file). The seed deletes
the whole graph first, so it refuses to run unless ENVIRONMENT is development or test, and it
refuses to run without a password.
"""
import random
import sys
import uuid
from datetime import datetime, timedelta

from neo4j import GraphDatabase

from backend.config import Settings


class SeedConfigurationError(RuntimeError):
    """The seed refuses to run: the Neo4j credential is not configured."""


def neo4j_connection(settings: Settings | None = None) -> tuple[str, str, str]:
    """Neo4j URI, user and password from Settings read now.

    Refused (before any driver exists) outside development/test and without a password.
    """
    settings = Settings() if settings is None else settings
    if settings.environment not in ("development", "test"):
        raise SeedConfigurationError(
            f"ENVIRONMENT is {settings.environment!r}; the seed deletes the whole Neo4j graph "
            "and runs only when ENVIRONMENT is development or test"
        )
    if not settings.neo4j_password.strip():
        raise SeedConfigurationError(
            "NEO4J_PASSWORD (or NEO4J_PASSWORD_FILE) must be set; the seed refuses to run "
            "without a Neo4j password"
        )
    return settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password


# ─── Call Center Agents ──────────────────────────────────────

AGENTS = [
    {"id": str(uuid.uuid4()), "name": "سارا احمدی", "email": "sara@homayar.io", "team": "پشتیبانی", "language": "fa", "avg_qc_score": 0.88, "total_calls": 1203},
    {"id": str(uuid.uuid4()), "name": "علی رضایی", "email": "ali@homayar.io", "team": "فروش", "language": "fa", "avg_qc_score": 0.82, "total_calls": 876},
    {"id": str(uuid.uuid4()), "name": "مریم کریمی", "email": "maryam@homayar.io", "team": "پشتیبانی", "language": "fa", "avg_qc_score": 0.91, "total_calls": 1542},
    {"id": str(uuid.uuid4()), "name": "John Smith", "email": "john@homayar.io", "team": "Support", "language": "en", "avg_qc_score": 0.85, "total_calls": 987},
    {"id": str(uuid.uuid4()), "name": "Emma Wilson", "email": "emma@homayar.io", "team": "Sales", "language": "en", "avg_qc_score": 0.79, "total_calls": 654},
    {"id": str(uuid.uuid4()), "name": "رضا محمدی", "email": "reza@homayar.io", "team": "فنی", "language": "fa", "avg_qc_score": 0.73, "total_calls": 432},
]

# ─── Call Data ────────────────────────────────────────────────

CALL_TEMPLATES_FA = [
    {"direction": "inbound", "issue": "اشتراک فعال نشده", "category": "technical", "sentiment": "negative", "resolution": "successful",
     "segments": [
         {"speaker": "agent", "text": "سلام، بخش پشتیبانی هومایار. من سارا هستم. چطور می‌تونم کمکتون کنم؟"},
         {"speaker": "customer", "text": "سلام، سه روز پیش پلن پریمیوم رو خریدید ولی هنوز فعال نشده."},
         {"speaker": "agent", "text": "بله، شماره حسابتون رو بفرمایید تا بررسی کنم."},
         {"speaker": "customer", "text": "09121234567"},
         {"speaker": "agent", "text": "بله، می‌بینم که پرداخت انجام شده ولی یک مشکل فنی در وب‌هوک بوده. الان فعال می‌کنم."},
         {"speaker": "customer", "text": "ممنون، ولی چرا باید سه روز منتظر بمونم؟"},
         {"speaker": "agent", "text": "حق با شماست و عذرخواهی می‌کنم. برای جبران یک ماه اعتبار اضافه به حسابتون اضافه کردم."},
         {"speaker": "customer", "text": "خیلی ممنون، این خیلی خوبه."},
         {"speaker": "agent", "text": "خواهش می‌کنم. آیا کمکی دیگه‌ای نیاز دارید؟"},
         {"speaker": "customer", "text": "نه ممنون، خداحافظ."},
         {"speaker": "agent", "text": "خداحافظ، روز خوبی داشته باشید."},
     ]},
    {"direction": "inbound", "issue": "مشکل در پرداخت", "category": "billing", "sentiment": "negative", "resolution": "partial",
     "segments": [
         {"speaker": "agent", "text": "سلام، هومایار. من علی هستم."},
         {"speaker": "customer", "text": "سلام، من قبض ماه گذشته دو بار کسر شده."},
         {"speaker": "agent", "text": "بله، بررسی می‌کنم. یک لحظه صبر کنید."},
         {"speaker": "customer", "text": "چرا اینقدر طول می‌کشه؟"},
         {"speaker": "agent", "text": "عذرخواهی می‌کنم. بله، یک خطای سیستمی بوده. درخواست برگشت داده می‌شه."},
         {"speaker": "customer", "text": "کی برمی‌گرده به حسابم؟"},
         {"speaker": "agent", "text": "حداقل ۴۸ ساعت کاری طول می‌کشه."},
         {"speaker": "customer", "text": "باشه ممنون."},
     ]},
    {"direction": "outbound", "issue": "پیشنهاد ارتقا", "category": "upsell", "sentiment": "positive", "resolution": "successful",
     "segments": [
         {"speaker": "agent", "text": "سلام، من مریم از تیم فروش هومایار هستم. تماس گرفتم تا پیشنهاد ویژه‌ای براتون داشته باشم."},
         {"speaker": "customer", "text": "بله بفرمایید."},
         {"speaker": "agent", "text": "همین الان پلن Growth رو اضافه کردیم که شامل تحلیل پیشرفته تماس‌ها هست."},
         {"speaker": "customer", "text": "قیمتش چقدره؟"},
         {"speaker": "agent", "text": "برای مشتریان فعلی ۲۰ درصد تخفیف داره. ماهانه ۴۹۰ هزار تومان."},
         {"speaker": "customer", "text": "خوبه، ثبتش کنید."},
         {"speaker": "agent", "text": "عالیه! الان فعال می‌کنم."},
     ]},
    {"direction": "inbound", "issue": "خطای سیستمی", "category": "technical", "sentiment": "negative", "resolution": "unsuccessful",
     "segments": [
         {"speaker": "agent", "text": "سلام، پشتیبانی فنی. من رضا هستم."},
         {"speaker": "customer", "text": "سیستم شما از صبح کار نمی‌کنه! گزارش‌ها لود نمیشه!"},
         {"speaker": "agent", "text": "ببخشید، کدوم بخش رو می‌گید؟"},
         {"speaker": "customer", "text": "بخش داشبورد. صفحه سفید میاد."},
         {"speaker": "agent", "text": "بله... الان چک می‌کنم... به نظر یک مشکل سروری هست. به تیم فنی اطلاع می‌دم."},
         {"speaker": "customer", "text": "کی درست می‌شه؟ کارم گیره!"},
         {"speaker": "agent", "text": "تلاش می‌کنیم تا یک ساعت درست بشه."},
         {"speaker": "customer", "text": "یعتی یک ساعت باید منتظر بمونم؟ باشه."},
     ]},
]

CALL_TEMPLATES_EN = [
    {"direction": "inbound", "issue": "Subscription not activated", "category": "technical", "sentiment": "negative", "resolution": "successful",
     "segments": [
         {"speaker": "agent", "text": "Hi, Homayar support. This is John. How can I help you?"},
         {"speaker": "customer", "text": "Hi, I upgraded to premium two days ago but nothing changed."},
         {"speaker": "agent", "text": "Let me check your account. Can I get your email?"},
         {"speaker": "customer", "text": "customer@example.com"},
         {"speaker": "agent", "text": "I see the payment went through but there was a webhook delay. I'll activate it manually right now."},
         {"speaker": "customer", "text": "Great, thanks for the quick fix."},
         {"speaker": "agent", "text": "You're welcome. I've also added a free week as compensation. Anything else?"},
         {"speaker": "customer", "text": "No that's perfect, bye."},
     ]},
    {"direction": "outbound", "issue": "Renewal follow-up", "category": "sales", "sentiment": "neutral", "resolution": "successful",
     "segments": [
         {"speaker": "agent", "text": "Hi, this is Emma from Homayar. I'm calling about your subscription renewal coming up next week."},
         {"speaker": "customer", "text": "Oh right, yeah I've been meaning to look at that."},
         {"speaker": "agent", "text": "We have a loyalty discount if you renew for the annual plan — saves about 25%."},
         {"speaker": "customer", "text": "That sounds reasonable. Can you send me the details by email?"},
         {"speaker": "agent", "text": "Of course. I'll send that right after this call. Thanks!"},
     ]},
]


def create_calls(templates, agents, count):
    """Generate call records from templates."""
    calls = []
    now = datetime.utcnow()
    for i in range(count):
        tpl = random.choice(templates)
        agent = random.choice(agents)
        call_time = now - timedelta(hours=random.randint(1, 168))
        duration = random.uniform(45, 300)

        # QC scores influenced by agent quality
        base = agent["avg_qc_score"] + random.uniform(-0.15, 0.10)
        base = max(0.3, min(1.0, base))

        qc = {
            "overall_score": round(base, 2),
            "empathy": round(base + random.uniform(-0.1, 0.1), 2),
            "problem_solving": round(base + random.uniform(-0.15, 0.1), 2),
            "communication_clarity": round(base + random.uniform(-0.08, 0.08), 2),
            "call_closing": round(base + random.uniform(-0.12, 0.1), 2),
            "silence_handling": round(base + random.uniform(-0.15, 0.05), 2),
            "tone": round(base + random.uniform(-0.1, 0.1), 2),
        }

        sentiment = tpl["sentiment"]
        if tpl["resolution"] == "successful":
            sentiment_prog = f"{sentiment} → positive"
        else:
            sentiment_prog = f"{sentiment} → {sentiment}"

        silence = random.uniform(3, 25)

        problem_severity = random.choice(["low", "medium", "high", "critical"])
        if tpl["category"] in ["technical"]:
            problem_severity = random.choice(["medium", "high", "critical"])
        elif tpl["category"] == "billing":
            problem_severity = random.choice(["medium", "high"])
        elif tpl["category"] in ["upsell", "sales"]:
            problem_severity = "low"

        upsell = tpl["category"] in ["upsell", "sales"] or random.random() > 0.7
        churn = "high" if tpl["resolution"] == "unsuccessful" else random.choice(["low", "low", "medium"])
        nps = random.randint(-20, 80) if sentiment == "negative" else random.randint(20, 90)

        actions = []
        if qc["silence_handling"] < 0.7:
            actions.append("آموزش مدیریت سکوت به اپراتور")
        if problem_severity in ["high", "critical"]:
            actions.append("بررسی و رفع علت ریشه‌ای")
        if churn == "high":
            actions.append("پیگیری مشتری ظرف ۲۴ ساعت")
        if upsell:
            actions.append("ارسال پیشنهاد ارتقا به مشتری")

        calls.append({
            "id": str(uuid.uuid4()),
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "direction": tpl["direction"],
            "language": agent["language"],
            "duration_seconds": round(duration, 1),
            "status": "completed",
            "file_name": f"call_{i+1:04d}.wav",
            "phone_number": f"0912{random.randint(1000000, 9999999)}",
            "created_at": call_time.isoformat(),
            "analyzed_at": (call_time + timedelta(minutes=random.randint(2, 15))).isoformat(),
            "issue": tpl["issue"],
            "category": tpl["category"],
            "segments": tpl["segments"],
            "qc": qc,
            "sentiment": sentiment,
            "sentiment_progression": sentiment_prog,
            "resolution": tpl["resolution"],
            "silence_seconds": round(silence, 1),
            "problem_severity": problem_severity,
            "root_cause": tpl["issue"],
            "system_failures": [] if tpl["category"] != "technical" else ["webhook delay", "CRM sync error"][:random.randint(0, 2)],
            "repeated_issue": random.random() > 0.7,
            "impact_scope": random.choice(["all users", "premium users", "new users", "specific region"]) if problem_severity != "low" else None,
            "upsell_signal": upsell,
            "churn_risk": churn,
            "customer_intent": tpl["issue"],
            "revenue_opportunity": "Upgrade to Growth plan" if upsell else None,
            "conversion_probability": "high" if upsell else None,
            "estimated_nps": nps,
            "satisfaction_trend": random.choice(["improving", "stable", "declining"]),
            "recommended_actions": actions,
        })
    return calls


def seed(settings: Settings | None = None):
    uri, user, password = neo4j_connection(settings)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        print("Cleared existing data")

        # ─── Create unique constraints ────────────────────────
        for label in ["CallAgent", "Call", "Transcript", "QCReport", "ProblemDetection", "BusinessInsight"]:
            session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")

        # ─── Create Agents ────────────────────────────────────
        for a in AGENTS:
            session.run("""
                CREATE (a:CallAgent {
                    id: $id, name: $name, email: $email, team: $team,
                    language: $language, avg_qc_score: $avg_qc_score,
                    total_calls: $total_calls
                })
            """, a)
        print(f"  CallAgents: {len(AGENTS)}")

        # ─── Create Calls + Transcript + QC + Problem + Insight ─
        fa_calls = create_calls(CALL_TEMPLATES_FA, [a for a in AGENTS if a["language"] == "fa"], 16)
        en_calls = create_calls(CALL_TEMPLATES_EN, [a for a in AGENTS if a["language"] == "en"], 6)
        all_calls = fa_calls + en_calls

        for c in all_calls:
            # Call node
            session.run("""
                CREATE (c:Call {
                    id: $id, agent_id: $agent_id, direction: $direction,
                    language: $language, duration_seconds: $duration_seconds,
                    status: $status, file_name: $file_name,
                    phone_number: $phone_number,
                    created_at: datetime($created_at),
                    analyzed_at: datetime($analyzed_at)
                })
            """, {k: c[k] for k in ["id", "agent_id", "direction", "language", "duration_seconds", "status", "file_name", "phone_number", "created_at", "analyzed_at"]})

            # Agent → Call relationship
            session.run("""
                MATCH (a:CallAgent {id: $aid}), (c:Call {id: $cid})
                CREATE (a)-[:MADE_CALL]->(c)
            """, aid=c["agent_id"], cid=c["id"])

            # Transcript node
            transcript_id = str(uuid.uuid4())
            full_text = " ".join(s["text"] for s in c["segments"])
            session.run("""
                MATCH (c:Call {id: $cid})
                CREATE (t:Transcript {
                    id: $tid, language: $language,
                    segments: $segments, full_text: $full_text
                })
                CREATE (c)-[:HAS_TRANSCRIPT]->(t)
            """, cid=c["id"], tid=transcript_id, language=c["language"],
                 segments=[f"{s['speaker']}: {s['text']}" for s in c["segments"]],
                 full_text=full_text)

            # QC Report node
            session.run("""
                MATCH (c:Call {id: $cid})
                CREATE (q:QCReport {
                    id: $qid, overall_score: $overall_score,
                    empathy: $empathy, problem_solving: $problem_solving,
                    communication_clarity: $communication_clarity,
                    call_closing: $call_closing, silence_handling: $silence_handling,
                    tone: $tone, sentiment: $sentiment,
                    sentiment_progression: $sentiment_progression,
                    resolution: $resolution,
                    silence_total_seconds: $silence_seconds
                })
                CREATE (c)-[:HAS_QC_REPORT]->(q)
            """, cid=c["id"], qid=str(uuid.uuid4()), **c["qc"],
                 sentiment=c["sentiment"], sentiment_progression=c["sentiment_progression"],
                 resolution=c["resolution"], silence_seconds=c["silence_seconds"])

            # Problem Detection node
            session.run("""
                MATCH (c:Call {id: $cid})
                CREATE (p:ProblemDetection {
                    id: $pid, root_cause: $root_cause,
                    issue_category: $category, severity: $severity,
                    system_failures: $system_failures,
                    repeated_issue: $repeated, impact_scope: $impact_scope
                })
                CREATE (c)-[:HAS_PROBLEM]->(p)
            """, cid=c["id"], pid=str(uuid.uuid4()),
                 root_cause=c["root_cause"], category=c["category"],
                 severity=c["problem_severity"], system_failures=c["system_failures"],
                 repeated=c["repeated_issue"], impact_scope=c["impact_scope"])

            # Business Insight node
            session.run("""
                MATCH (c:Call {id: $cid})
                CREATE (b:BusinessInsight {
                    id: $bid, upsell_signal: $upsell, churn_risk: $churn,
                    customer_intent: $intent, revenue_opportunity: $revenue,
                    conversion_probability: $conversion, estimated_nps: $nps,
                    satisfaction_trend: $trend
                })
                CREATE (c)-[:HAS_INSIGHT]->(b)
            """, cid=c["id"], bid=str(uuid.uuid4()),
                 upsell=c["upsell_signal"], churn=c["churn_risk"],
                 intent=c["customer_intent"], revenue=c["revenue_opportunity"],
                 conversion=c["conversion_probability"], nps=c["estimated_nps"],
                 trend=c["satisfaction_trend"])

        print(f"  Calls: {len(all_calls)} (FA: {len(fa_calls)}, EN: {len(en_calls)})")

        # ─── Verify ───────────────────────────────────────────
        for label in ["CallAgent", "Call", "Transcript", "QCReport", "ProblemDetection", "BusinessInsight"]:
            result = session.run(f"MATCH (n:{label}) RETURN count(n) as count")
            count = result.single()["count"]
            print(f"    {label}: {count}")

        result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
        print(f"    Relationships: {result.single()['count']}")

    driver.close()
    print("\nSeed complete!")


if __name__ == "__main__":
    try:
        seed()
    except SeedConfigurationError as exc:
        sys.exit(f"seed refused: {exc}")
