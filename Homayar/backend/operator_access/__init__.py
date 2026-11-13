"""Host-side operator commands for account cut-off and break-glass access (HOM-129, ADR 0076).

They run on the server host only (``python -m backend.operator_access``), against the
same database as the API; no HTTP route offers them.
"""
