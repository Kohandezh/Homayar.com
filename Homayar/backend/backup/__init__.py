"""Backup, isolated restore and recovery of a Homayar deployment (WP20, ADR 0035).

``create`` -> ``verify`` -> ``replicate`` on the backup side;
``restore`` (staging database) -> ``reconcile`` (tombstones, tenants, RLS,
objects) -> ``promote`` on the recovery side.  Operator entry point:
``python -m backend.backup``; runbook: ``docs/operations/backup-restore.md``.
"""
