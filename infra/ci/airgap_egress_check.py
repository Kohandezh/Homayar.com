"""Air-gapped egress gate check (WP08-T06 network layer, ADR 0031).

Runs inside the API image. It proves that the operator preflight
(`python -m backend.jobs.model_preflight --egress-allowlist --require-no-cloud`)
passes an on-premise configuration whose only networked AI endpoint is a
manifest-declared private LLM, and refuses (exit 3) as soon as a cloud endpoint
is configured. The private manifest is derived from the built adapter's own
identity, so prompt changes do not break the check; no provider is contacted.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

os.environ.update(
    {
        "ENVIRONMENT": "development",
        "DEPLOYMENT_MODE": "onprem",
        "INTELLIGENCE_PROVIDER": "openai_compatible",
        "LOCAL_AI_BASE_URL": "http://llm.internal:8000/v1",
        "LLM_MODEL": "local-model",
    }
)

from backend.config import get_settings, reset_settings_cache  # noqa: E402
from backend.jobs import model_preflight  # noqa: E402
from backend.providers.factory import create_provider_bundle  # noqa: E402
from backend.providers.registry import manifest_skeleton  # noqa: E402


def run() -> tuple[int, str]:
    reset_settings_cache()
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = model_preflight.main(["--egress-allowlist", "--require-no-cloud"])
    return code, out.getvalue()


failures = []
identity = create_provider_bundle(get_settings()).intelligence.identity()
manifest = manifest_skeleton("intelligence", identity)
manifest.update(endpoint_kind="private", region="customer-dc", manifest_id="ci.llm.private")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
    json.dump({"schema": "homayar.model-manifests/1", "manifests": [manifest]}, handle)
os.environ["MODEL_MANIFEST_PATHS"] = handle.name

code, out = run()
print(f"private-only configuration: exit {code}; allowlist: {out.strip()!r}")
if code != 0 or not out.startswith("llm.internal:8000") or "cloud" in out:
    failures.append("a private-only on-prem configuration must pass with llm.internal:8000 only")

os.environ["LLM_FALLBACK_BASE_URL"] = "https://api.cloud-llm.example/v1"
os.environ["LLM_FALLBACK_MODEL"] = "cloud-model"
code, out = run()
print(f"with a cloud fallback configured: exit {code}")
if code != model_preflight.EGRESS_CLOUD_REFUSED:
    failures.append("a configured cloud endpoint must be refused with exit 3")

if failures:
    print("FAILED: " + "; ".join(failures))
    sys.exit(1)
print("air-gapped egress gate: ok")
