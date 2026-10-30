"""Minimal Python client for the Homayar ingestion API (OpenAPI contract v1).

The contract is ``Homayar/backend/contracts/ingestion-v1.openapi.json``.
The client's major version follows the contract's major version.
``verify_webhook`` checks the signed webhooks Homayar sends to your endpoint.
"""

from homayar_client.client import TERMINAL_JOB_STATES, HomayarClient, new_idempotency_key
from homayar_client.errors import HomayarAPIError
from homayar_client.retry import RetryPolicy
from homayar_client.webhooks import WebhookDeduplicator, WebhookVerificationError, verify_webhook

__version__ = "1.0.0"
CONTRACT_VERSION = "1.0.0"

__all__ = [
    "CONTRACT_VERSION",
    "TERMINAL_JOB_STATES",
    "HomayarAPIError",
    "HomayarClient",
    "RetryPolicy",
    "WebhookDeduplicator",
    "WebhookVerificationError",
    "new_idempotency_key",
    "verify_webhook",
]
