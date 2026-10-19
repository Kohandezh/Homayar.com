"""Minimal Python client for the Homayar ingestion API (OpenAPI contract v1).

The contract is ``Homayar/backend/contracts/ingestion-v1.openapi.json``.
The client's major version follows the contract's major version.
"""

from homayar_client.client import TERMINAL_JOB_STATES, HomayarClient, new_idempotency_key
from homayar_client.errors import HomayarAPIError
from homayar_client.retry import RetryPolicy

__version__ = "1.0.0"
CONTRACT_VERSION = "1.0.0"

__all__ = [
    "CONTRACT_VERSION",
    "TERMINAL_JOB_STATES",
    "HomayarAPIError",
    "HomayarClient",
    "RetryPolicy",
    "new_idempotency_key",
]
