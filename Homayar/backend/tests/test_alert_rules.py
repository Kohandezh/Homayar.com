"""Prometheus alert rules against the code that feeds them (HOM-126, ADR 0036).

The rules live in ``infra/observability/prometheus``. promtool proves that each alert fires
on its failure and stays silent in a normal state; these tests prove that the rules query
series and label values the API really emits (a sample, not only HELP/TYPE lines), that the
scrape jobs they name are the ones ``prometheus.example.yml`` configures, and that every alert
is routable (``severity``), explained (``summary``) and documented (a runbook section).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from prometheus_client.parser import text_string_to_metric_families

from backend.config import Settings
from backend.database.saas_models import JobState, ProcessingJob, ProviderHealth
from backend.jobs.checkpoints import sweep_abandoned_artifacts
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.jobs.retention_purge import run_retention_purge
from backend.observability import heartbeats
from backend.observability.metrics import (
    FAILURE_CATEGORIES,
    FINISHED_STATES,
    PROVIDER_STAGES,
    SOURCE_TIMEOUT_SECONDS,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_job_processor import _queued_upload

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMETHEUS_DIR = REPO_ROOT / "infra/observability/prometheus"
RULES_FILE = PROMETHEUS_DIR / "homayar.rules.yml"
RULE_TESTS_FILE = PROMETHEUS_DIR / "tests/homayar.rules.test.yml"
EXAMPLE_CONFIG = PROMETHEUS_DIR / "prometheus.example.yml"
RUNBOOK = "docs/operations/alerts.md"
PROMETHEUS_IMAGE = "prom/prometheus:v3.5.0"

# Not served by the API: the backup tooling (Lane C) writes this series to a node_exporter
# textfile, one sample per target.
EXTERNAL_METRICS = frozenset({"homayar_backup_last_success_timestamp_seconds"})
# Series of Prometheus itself and of node_exporter's textfile collector.
EXTERNAL_SERIES = frozenset({"up", "node_textfile_scrape_error"})
BACKUP_TARGETS = frozenset({"postgres", "objects", "offsite"})
SEVERITIES = frozenset({"warning", "critical"})
# collect_jobs reports failed and dead-lettered jobs only.
FAILED_OUTCOMES = frozenset({"failed", "dead_letter"})
STATUS_CLASSES = frozenset({*(f"{n}xx" for n in range(1, 6)), "other"})

_METRIC = re.compile(r"(?<![\w\"])(homayar[_:][A-Za-z0-9_:]*)")
_SELECTOR = re.compile(r"(?<![\w\":])([A-Za-z_:][A-Za-z0-9_:]*)\s*\{([^}]*)\}")
_MATCHER = re.compile(r'(\w+)\s*(=~|!~|!=|=)\s*"([^"]*)"')
_ALTERNATION = re.compile(r"[a-z0-9_]+(\|[a-z0-9_]+)*")


def _rules() -> list[dict[str, Any]]:
    document = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    return [rule for group in document["groups"] for rule in group["rules"]]


def _alerts() -> list[dict[str, Any]]:
    return [rule for rule in _rules() if "alert" in rule]


def _scrape_jobs() -> set[str]:
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    return {job["job_name"] for job in config["scrape_configs"]}


def _github_anchor(heading: str) -> str:
    """The fragment GitHub renders for a Markdown heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


# --- (a, b) the rules query what the API exposes ------------------------------------------


@dataclass
class Exposition:
    names: set[str]
    labels: dict[str, list[dict[str, str]]]

    def values(self, metric: str, label: str, **where: str) -> set[str]:
        return {
            sample[label]
            for sample in self.labels.get(metric, [])
            if label in sample and all(sample.get(k) == v for k, v in where.items())
        }


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"metrics_cache_seconds": 0})


@pytest.fixture
def heartbeat_store():
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    yield store
    heartbeats.use_heartbeat_store(None)


@pytest.fixture
def exposition(client, email_outbox, settings, monkeypatch, heartbeat_store) -> Exposition:
    """One scrape of a running API with every source the rules read populated."""
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:  # DBA write: park the job, open a circuit
        job = db.get(ProcessingJob, job_id)
        job.state = JobState.DEAD_LETTER
        job.failure_class = "transient"
        job.failure_code = "asr_unavailable"
        job.attempt_count = 3
        job.completed_at = now
        db.add(
            ProviderHealth(
                endpoint_key="a" * 64,
                stage="asr",
                provider="openai_compatible",
                model="alert-rules-model",
                consecutive_failures=5,
                open_until=now + timedelta(minutes=1),
                last_failure_code="asr_unavailable",
                last_failure_at=now,
            )
        )
        db.commit()

    # The real sweeps, recorded the way the worker records them: their result keys become
    # the ``field`` values of homayar_periodic_task_result.
    heartbeats.run_with_heartbeat(
        "retention_purge", lambda: run_retention_purge(settings, workspace_ids=[])
    )
    heartbeats.run_with_heartbeat(
        "media_reconciliation", lambda: reconcile_media_objects(settings, workspace_ids=[])
    )
    heartbeats.run_with_heartbeat("artifact_cleanup", lambda: sweep_abandoned_artifacts(settings))

    def broken() -> dict[str, int]:
        raise RuntimeError("storage unreachable")

    with pytest.raises(RuntimeError):
        heartbeats.run_with_heartbeat("media_reconciliation", broken)

    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    names: set[str] = set()
    labels: dict[str, list[dict[str, str]]] = {}
    for family in text_string_to_metric_families(response.text):
        # Only samples count: a labelled counter that nothing increments (or creates at 0) is
        # announced by HELP/TYPE lines alone, and an alert on it could never fire.
        for sample in family.samples:
            names.add(sample.name)
            labels.setdefault(sample.name, []).append(dict(sample.labels))
    return Exposition(names=names, labels=labels)


def test_rules_query_only_series_the_api_exposes(exposition):
    recorded = {rule["record"] for rule in _rules() if "record" in rule}
    missing: dict[str, set[str]] = {}
    for rule in _rules():
        name = rule.get("alert") or rule["record"]
        for metric in _METRIC.findall(rule["expr"]):
            if metric.startswith("homayar:"):
                known = metric in recorded
            else:
                known = metric in exposition.names or metric in EXTERNAL_METRICS
            if not known:
                missing.setdefault(name, set()).add(metric)
        for metric, _ in _selectors(rule["expr"]):
            if not metric.startswith("homayar") and metric not in EXTERNAL_SERIES:
                missing.setdefault(name, set()).add(metric)
    assert not missing, f"rules query series that nothing exposes: {missing}"


def _vocabulary(
    metric: str, label: str, matchers: dict[str, list[str]], exposition: Exposition
) -> set[str] | None:
    if label == "task":
        # The operator jobs' alerts arrive with their producer (HOM-115).
        return set(heartbeats.PERIODIC_TASKS)
    if label == "field" and metric == "homayar_periodic_task_result":
        tasks = matchers.get("task") or []
        assert tasks, f"{metric} matches field without naming its task"
        return set.intersection(*(exposition.values(metric, "field", task=task) for task in tasks))
    if label == "outcome" and metric == "homayar_jobs_failed_15m":
        return set(FAILED_OUTCOMES)
    if label == "outcome" and metric == "homayar_jobs_finished_15m":
        return set(FINISHED_STATES)
    if label == "category" and metric == "homayar_jobs_failed_15m":
        return set(FAILURE_CATEGORIES)
    if label == "state" and metric == "homayar_jobs":
        return exposition.values(metric, "state")
    if label == "dependency" and metric == "homayar_dependency_up":
        return exposition.values(metric, "dependency")
    if label == "stage" and metric.startswith("homayar_provider_"):
        return set(PROVIDER_STAGES)
    if label == "status_class" and metric in (
        "homayar_http_requests_total",
        "homayar:http_requests:rate5m",
    ):
        return set(STATUS_CLASSES)
    if label == "source" and metric in (
        "homayar_metrics_collection_errors_total",
        "homayar:metrics_collection_errors:increase15m",
    ):
        return exposition.values("homayar_metrics_collection_errors_total", "source")
    if label == "target" and metric in EXTERNAL_METRICS:
        return set(BACKUP_TARGETS)
    if label == "job" and metric in EXTERNAL_SERIES:
        return _scrape_jobs()
    return None


def _selectors(expr: str) -> list[tuple[str, dict[str, list[str]]]]:
    selectors = []
    for metric, body in _SELECTOR.findall(expr):
        matchers: dict[str, list[str]] = {}
        for label, operator, value in _MATCHER.findall(body):
            if operator in ("=~", "!~"):
                assert _ALTERNATION.fullmatch(value), (
                    f"{metric}: use a plain alternation in {label}{operator}{value!r}"
                )
                values = value.split("|")
            else:
                values = [value]
            matchers.setdefault(label, []).extend(values)
        selectors.append((metric, matchers))
    return selectors


def test_rule_label_matchers_use_values_the_code_can_emit(exposition):
    # Negative matchers count too: a typo in != or !~ silently matches everything.
    wrong: list[str] = []
    for rule in _rules():
        name = rule.get("alert") or rule["record"]
        for metric, matchers in _selectors(rule["expr"]):
            for label, values in matchers.items():
                allowed = _vocabulary(metric, label, matchers, exposition)
                if allowed is None:
                    wrong.append(f"{name}: no vocabulary for {metric}{{{label}}} in this test")
                    continue
                assert allowed, f"{name}: {metric}{{{label}}} has no value in the exposition"
                wrong += [
                    f"{name}: {metric}{{{label}={value!r}}}"
                    for value in values
                    if value not in allowed
                ]
    assert not wrong, "label values no code emits:\n" + "\n".join(wrong)


def test_the_example_prometheus_config_matches_the_rules():
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert config["rule_files"] == [RULES_FILE.name]
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert {"homayar-api", "homayar-node"} <= set(jobs)
    assert jobs["homayar-api"]["metrics_path"] == "/metrics"
    # A refresh runs two stages of source deadlines; a shorter timeout turns a slow dependency
    # into HomayarApiScrapeFailing instead of its own alert.
    timeout = config["global"]["scrape_timeout"]
    assert timeout.endswith("s"), timeout
    assert float(timeout[:-1]) > 2 * SOURCE_TIMEOUT_SECONDS


# --- (c) every alert is routable and documented ---------------------------------------


def test_every_alert_has_a_severity_a_summary_and_a_runbook_section():
    alerts = _alerts()
    names = [alert["alert"] for alert in alerts]
    assert len(names) == len(set(names)), "alert names must be unique"
    runbook = (REPO_ROOT / RUNBOOK).read_text(encoding="utf-8")
    sections = {_github_anchor(h) for h in re.findall(r"^## (.+)$", runbook, re.MULTILINE)}
    for alert in alerts:
        name, labels = alert["alert"], alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        assert labels.get("severity") in SEVERITIES, name
        assert str(annotations.get("summary", "")).strip(), name
        path, _, anchor = str(annotations.get("runbook", "")).partition("#")
        assert path == RUNBOOK, name
        assert anchor in sections, f"{name}: no '## ' section for #{anchor} in {RUNBOOK}"
    # The other direction: a renamed alert does not leave its old section behind.
    assert sections <= {name.lower() for name in names}, sections - {n.lower() for n in names}


# --- (d) promtool covers every alert --------------------------------------------------


def test_every_alert_has_a_firing_and_a_silent_promtool_case():
    document = yaml.safe_load(RULE_TESTS_FILE.read_text(encoding="utf-8"))
    assert document["rule_files"] == ["../homayar.rules.yml"]
    # promtool orders unlisted groups at random: list them all, recording rules first.
    groups = [g["name"] for g in yaml.safe_load(RULES_FILE.read_text("utf-8"))["groups"]]
    assert sorted(document["group_eval_order"]) == sorted(groups)
    assert document["group_eval_order"][0] == "homayar-recording"
    firing: set[str] = set()
    silent: set[str] = set()
    for case in document["tests"]:
        for check in case.get("alert_rule_test", []):
            (firing if check.get("exp_alerts") else silent).add(check["alertname"])
    names = {alert["alert"] for alert in _alerts()}
    # A misspelt alertname with ``exp_alerts: []`` passes promtool without testing anything.
    assert (firing | silent) <= names, (firing | silent) - names
    assert names <= firing, f"no promtool case where these fire: {sorted(names - firing)}"
    assert names <= silent, f"no promtool case where these stay silent: {sorted(names - silent)}"


# --- (e) promtool itself, when docker and the image are available ---------------------


@pytest.fixture(scope="module")
def promtool() -> Callable[..., subprocess.CompletedProcess[str]]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv
            [docker, "image", "inspect", PROMETHEUS_IMAGE],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker is not usable ({type(exc).__name__})")
    if probe.returncode != 0:
        reason = (probe.stderr.strip().splitlines() or ["no output"])[-1][:200]
        pytest.skip(f"{PROMETHEUS_IMAGE} is not available locally: {reason}")

    command = [docker, "run", "--rm", "--network", "none", "--entrypoint", "promtool"]
    command += ["--volume", f"{PROMETHEUS_DIR}:/rules:ro", "--workdir", "/rules/tests"]

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, read-only mount, no network
            [*command, PROMETHEUS_IMAGE, *args],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

    return run


@pytest.mark.parametrize(
    "args",
    [
        ("check", "rules", "/rules/homayar.rules.yml"),
        ("test", "rules", "homayar.rules.test.yml"),
        ("check", "config", "--syntax-only", "/rules/prometheus.example.yml"),
    ],
    ids=["check-rules", "test-rules", "check-config"],
)
def test_promtool_accepts_the_rules_and_their_unit_tests(promtool, args):
    result = promtool(*args)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SUCCESS" in output, output
