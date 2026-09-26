"""Multi-provider LLM router — distributes agents across multiple APIs."""
import json
import asyncio
import httpx
from typing import Optional
from backend.config import get_settings


def _get_providers() -> list:
    """Return list of available (base_url, api_key, name) providers."""
    settings = get_settings()
    providers = []
    if settings.zai_api_key and settings.zai_api_key != "your-z-ai-api-key-here":
        providers.append((settings.zai_base_url, settings.zai_api_key, "zai"))
    if settings.gapgpt_api_key:
        providers.append((settings.gapgpt_base_url, settings.gapgpt_api_key, "gapgpt"))
    return providers


def _resolve_model(agent: str) -> str:
    """Resolve which model to use for a given agent."""
    settings = get_settings()
    agent_models = {
        "qc": settings.llm_model_qc,
        "problem": settings.llm_model_problem,
        "insight": settings.llm_model_insight,
        "teacher": settings.llm_model_teacher,
        "copilot_intent": settings.llm_model_copilot,
        "copilot_response": settings.llm_model_copilot,
    }
    return agent_models.get(agent, "") or settings.llm_model


def _get_provider_for_agent(agent: str) -> Optional[tuple]:
    """Get the best provider for an agent based on config."""
    settings = get_settings()
    providers = _get_providers()

    if not providers:
        return None

    if settings.llm_provider == "auto":
        # Auto strategy: use GapGPT for heavy analysis, Z.ai for copilot
        gapgpt_agents = {"qc", "problem", "insight", "teacher"}
        if agent in gapgpt_agents:
            # Prefer GapGPT for analysis agents
            for base, key, name in providers:
                if name == "gapgpt":
                    return (base, key)
        # Copilot and others use first available
        return (providers[0][0], providers[0][1])

    elif settings.llm_provider == "gapgpt":
        for base, key, name in providers:
            if name == "gapgpt":
                return (base, key)
        return (providers[0][0], providers[0][1]) if providers else None

    elif settings.llm_provider == "zai":
        for base, key, name in providers:
            if name == "zai":
                return (base, key)
        return (providers[0][0], providers[0][1]) if providers else None

    return (providers[0][0], providers[0][1]) if providers else None


async def call_llm(
    system_prompt: str,
    user_message: str,
    agent: str = "default",
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """Call LLM with multi-provider routing. Returns raw text or None."""
    settings = get_settings()
    provider = _get_provider_for_agent(agent)

    if not provider:
        return None  # Triggers mock fallback

    base_url, api_key = provider
    model = _resolve_model(agent)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        # Try failover to next provider if auto mode
        if settings.llm_provider == "auto":
            providers = _get_providers()
            for fallback_base, fallback_key, fallback_name in providers:
                if (fallback_base, fallback_key) != provider:
                    try:
                        async with httpx.AsyncClient(timeout=90) as client:
                            resp = await client.post(
                                f"{fallback_base}/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {fallback_key}",
                                    "Content-Type": "application/json",
                                },
                                json=payload,
                            )
                            resp.raise_for_status()
                            data = resp.json()
                            return data["choices"][0]["message"]["content"]
                    except Exception:
                        continue
        return None


def parse_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def run_agent(system_prompt: str, user_message: str, agent: str = "default") -> Optional[dict]:
    """Run a single analysis agent. Returns parsed JSON or None."""
    raw = await call_llm(system_prompt, user_message, agent=agent)
    return parse_json(raw)


def get_provider_status() -> dict:
    """Return current provider config status for diagnostics."""
    settings = get_settings()
    providers = _get_providers()
    return {
        "provider_mode": settings.llm_provider,
        "available_providers": [name for _, _, name in providers],
        "default_model": settings.llm_model,
        "agent_models": {
            "qc": _resolve_model("qc"),
            "problem": _resolve_model("problem"),
            "insight": _resolve_model("insight"),
            "teacher": _resolve_model("teacher"),
            "copilot": _resolve_model("copilot_intent"),
        },
    }
