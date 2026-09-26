"""PDF report generation for call reports."""
import io
from datetime import datetime
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from backend.database.neo4j_connection import Neo4jConnection

ACCENT = HexColor("#6366f1")
DARK = HexColor("#1a1a2e")
GRAY = HexColor("#6b7280")
GREEN = HexColor("#22c55e")
YELLOW = HexColor("#eab308")
RED = HexColor("#ef4444")


def _score_color(score: float) -> HexColor:
    pct = score * 100
    if pct >= 70:
        return GREEN
    if pct >= 50:
        return YELLOW
    return RED


def _severity_color(severity: str) -> HexColor:
    return {"critical": RED, "high": HexColor("#f97316"), "medium": YELLOW}.get(severity, GRAY)


def generate_call_pdf(call_id: str) -> bytes:
    """Generate a PDF report for a call. Returns bytes."""
    # Fetch call data
    from backend.services.call_service import get_call_detail
    report = get_call_detail(call_id)
    if not report:
        raise ValueError("Call not found")

    call = report.get("call", {})
    qc = report.get("qc_report")
    problems = report.get("problems")
    insights = report.get("insights")
    transcript = report.get("transcript")
    actions = report.get("recommended_actions", [])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=15*mm, bottomMargin=15*mm)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Title2", parent=styles["Title"], fontSize=18, textColor=DARK, spaceAfter=2*mm))
    styles.add(ParagraphStyle("Subtitle2", parent=styles["Normal"], fontSize=10, textColor=GRAY, spaceAfter=6*mm))
    styles.add(ParagraphStyle("Section", parent=styles["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=6*mm, spaceAfter=3*mm))
    styles.add(ParagraphStyle("Body2", parent=styles["Normal"], fontSize=9, textColor=DARK, leading=13))
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=GRAY, leading=11))

    elements = []

    # Header
    elements.append(Paragraph("Homayar — Call Intelligence Report", styles["Title2"]))
    elements.append(Paragraph(
        f"Call ID: {call_id}  |  Agent: {call.get('agent_name', 'N/A')}  |  "
        f"Date: {call.get('created_at', 'N/A')}  |  Direction: {call.get('direction', 'N/A')}",
        styles["Subtitle2"]
    ))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#e5e7eb")))

    # Summary table
    summary_data = [
        ["Duration", f"{call.get('duration_seconds', 0)}s"],
        ["Language", call.get("language", "N/A")],
        ["Resolution", qc.get("resolution", "N/A") if qc else "N/A"],
        ["Sentiment", qc.get("sentiment", "N/A") if qc else "N/A"],
        ["Severity", problems.get("severity", "N/A") if problems else "N/A"],
        ["NPS", str(insights.get("estimated_nps", "N/A")) if insights else "N/A"],
    ]
    t = Table(summary_data, colWidths=[50*mm, 80*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, HexColor("#e5e7eb")),
    ]))
    elements.append(t)

    # QC Metrics
    if qc:
        elements.append(Paragraph("Quality Control Metrics", styles["Section"]))
        metrics = [
            ("Overall", qc.get("overall_score", 0)),
            ("Empathy", qc.get("empathy", 0)),
            ("Problem Solving", qc.get("problem_solving", 0)),
            ("Communication", qc.get("communication_clarity", 0)),
            ("Call Closing", qc.get("call_closing", 0)),
            ("Silence Handling", qc.get("silence_handling", 0)),
            ("Tone", qc.get("tone", 0)),
        ]
        qc_data = [["Metric", "Score", "Rating"]]
        for name, val in metrics:
            pct = round(val * 100)
            rating = "Good" if pct >= 70 else "Fair" if pct >= 50 else "Poor"
            qc_data.append([name, f"{pct}%", rating])

        t2 = Table(qc_data, colWidths=[50*mm, 30*mm, 40*mm])
        t2.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f3f4f6")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e5e7eb")),
        ]))
        elements.append(t2)
        elements.append(Paragraph(f"Silence Total: {qc.get('silence_total_seconds', 0)}s  |  Sentiment Progression: {qc.get('sentiment_progression', 'N/A')}", styles["Small"]))

    # Problem Analysis
    if problems:
        elements.append(Paragraph("Problem Analysis", styles["Section"]))
        prob_data = [
            ["Root Cause", problems.get("root_cause", "N/A")],
            ["Category", problems.get("issue_category", "N/A")],
            ["Severity", problems.get("severity", "N/A")],
            ["Repeated Issue", "Yes" if problems.get("repeated_issue") else "No"],
        ]
        if problems.get("impact_scope"):
            prob_data.append(["Impact Scope", problems["impact_scope"]])
        if problems.get("system_failures"):
            prob_data.append(["System Failures", ", ".join(problems["system_failures"])])

        t3 = Table(prob_data, colWidths=[40*mm, 100*mm])
        t3.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e5e7eb")),
        ]))
        elements.append(t3)

    # Business Insight
    if insights:
        elements.append(Paragraph("Business Insight", styles["Section"]))
        biz_data = [
            ["Upsell Signal", "Yes" if insights.get("upsell_signal") else "No"],
            ["Churn Risk", insights.get("churn_risk", "N/A")],
            ["Customer Intent", insights.get("customer_intent", "N/A")],
            ["Revenue Opportunity", insights.get("revenue_opportunity", "None")],
            ["Satisfaction Trend", insights.get("satisfaction_trend", "N/A")],
        ]
        t4 = Table(biz_data, colWidths=[45*mm, 95*mm])
        t4.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, HexColor("#e5e7eb")),
        ]))
        elements.append(t4)

    # Recommended Actions
    if actions:
        elements.append(Paragraph("Recommended Actions", styles["Section"]))
        for i, action in enumerate(actions, 1):
            elements.append(Paragraph(f"{i}. {action}", styles["Body2"]))
        elements.append(Spacer(1, 3*mm))

    # Transcript
    if transcript and transcript.get("segments"):
        elements.append(Paragraph("Transcript", styles["Section"]))
        for seg in transcript["segments"]:
            elements.append(Paragraph(seg, styles["Body2"]))
        elements.append(Spacer(1, 3*mm))

    # Footer
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#e5e7eb")))
    elements.append(Paragraph(f"Generated by Homayar on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", styles["Small"]))

    doc.build(elements)
    return buf.getvalue()
