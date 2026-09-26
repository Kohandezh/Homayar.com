"""Dashboard API router — post-call metrics."""
from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.services.dashboard_service import get_kpis, get_qc_trend, get_sentiment_distribution, get_issues_by_category, get_recent_calls, get_call_problems_bulk, get_call_qc_details_bulk
from backend.services.issue_aggregation_service import aggregate_issues

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/kpis")
async def kpis():
    return get_kpis()


@router.get("/qc-trend")
async def qc_trend(days: int = 7):
    return get_qc_trend(days)


@router.get("/sentiment")
async def sentiment_distribution():
    return get_sentiment_distribution()


@router.get("/issues")
async def issues_by_category():
    return get_issues_by_category()


@router.get("/aggregated-issues")
async def aggregated_issues():
    """AI-clustered issue aggregation."""
    return await aggregate_issues()


@router.get("/recent-calls")
async def recent_calls(limit: int = 20):
    return get_recent_calls(limit)


@router.get("/call-problems")
async def call_problems():
    """Bulk fetch all calls with their problems — avoids N+1 queries."""
    return get_call_problems_bulk()


@router.get("/call-qc-details")
async def call_qc_details():
    """Bulk fetch all completed calls with QC details — for analytics."""
    return get_call_qc_details_bulk()
