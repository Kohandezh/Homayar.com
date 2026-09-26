"""Asanak SMS adapter using an injected endpoint and credential."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from pydantic import SecretStr

from backend.providers.base import SMSProvider
from backend.providers.schemas import (
    DeliveryStatus,
    ProviderIssue,
    SMSRequest,
    SMSResult,
)


class AsanakSMSProvider(SMSProvider):
    """Submit one SMS to an injected Asanak endpoint.

    Message bodies and the API token are never logged or included in public errors.
    Redirect following is disabled so credentials cannot cross host boundaries.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        sender: str,
        client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 10.0,
        auth_header: str = "Authorization",
        auth_scheme: Optional[str] = "Bearer",
    ) -> None:
        parsed_url = httpx.URL(url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.host
            or bool(parsed_url.username)
            or bool(parsed_url.password)
        ):
            raise ValueError("Asanak URL must be an absolute HTTP(S) URL")
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Asanak token is required")
        if not sender.strip() or "\r" in sender or "\n" in sender:
            raise ValueError("Asanak sender is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not auth_header or any(character in auth_header for character in "\r\n:"):
            raise ValueError("auth_header is invalid")
        if auth_scheme is not None and any(character in auth_scheme for character in "\r\n"):
            raise ValueError("auth_scheme is invalid")

        self._url = str(parsed_url)
        self._token = SecretStr(token)
        self._sender = sender.strip()
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme

    def _headers(self, request: SMSRequest) -> Dict[str, str]:
        token = self._token.get_secret_value()
        credential = f"{self._auth_scheme} {token}" if self._auth_scheme else token
        headers = {
            "Accept": "application/json",
            self._auth_header: credential,
        }
        if request.idempotency_key:
            headers["Idempotency-Key"] = request.idempotency_key
        return headers

    async def _post(self, request: SMSRequest) -> httpx.Response:
        payload = {
            "source": self._sender,
            "destination": request.to,
            "message": request.body.get_secret_value(),
        }
        kwargs = {
            "headers": self._headers(request),
            "json": payload,
            "timeout": self._timeout_seconds,
            "follow_redirects": False,
        }
        if self._client is not None:
            return await self._client.post(self._url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(self._url, **kwargs)

    @staticmethod
    def _message_id(value: Any) -> Optional[str]:
        if isinstance(value, dict):
            for key in ("message_id", "messageId", "msgid", "id"):
                candidate = value.get(key)
                if candidate is not None:
                    return str(candidate)
            for key in ("data", "result"):
                if key in value:
                    candidate = AsanakSMSProvider._message_id(value[key])
                    if candidate is not None:
                        return candidate
        elif isinstance(value, list) and value:
            return AsanakSMSProvider._message_id(value[0])
        elif isinstance(value, (str, int)):
            return str(value)
        return None

    @staticmethod
    def _api_accepted(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        meta = payload.get("meta")
        if isinstance(meta, dict) and "status" in meta:
            return str(meta["status"]).lower() in {"200", "ok", "success", "accepted"}
        if "success" in payload:
            return payload["success"] is True
        if "status" in payload:
            return str(payload["status"]).lower() in {
                "200",
                "ok",
                "success",
                "accepted",
                "queued",
            }
        return AsanakSMSProvider._message_id(payload) is not None

    @staticmethod
    def _failure(code: str, message: str, *, retryable: bool) -> SMSResult:
        return SMSResult(
            provider="asanak",
            status=DeliveryStatus.FAILED,
            issue=ProviderIssue(code=code, message=message, retryable=retryable),
        )

    async def send(self, request: SMSRequest) -> SMSResult:
        try:
            response = await self._post(request)
        except httpx.TimeoutException:
            return self._failure(
                "sms_provider_timeout",
                "SMS provider timed out",
                retryable=True,
            )
        except httpx.TransportError:
            return self._failure(
                "sms_provider_unavailable",
                "SMS provider is unavailable",
                retryable=True,
            )

        if not 200 <= response.status_code < 300:
            retryable = response.status_code == 429 or response.status_code >= 500
            return self._failure(
                "sms_provider_http_error",
                "SMS provider rejected the request",
                retryable=retryable,
            )

        try:
            payload = response.json()
        except ValueError:
            return self._failure(
                "sms_provider_invalid_response",
                "SMS provider returned an invalid response",
                retryable=False,
            )

        if not self._api_accepted(payload):
            return SMSResult(
                provider="asanak",
                status=DeliveryStatus.REJECTED,
                issue=ProviderIssue(
                    code="sms_provider_rejected",
                    message="SMS provider did not accept the message",
                    retryable=False,
                ),
            )

        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        return SMSResult(
            provider="asanak",
            status=DeliveryStatus.ACCEPTED,
            message_id=self._message_id(data),
            accepted_at=datetime.now(timezone.utc),
        )


AsanakSMSAdapter = AsanakSMSProvider
