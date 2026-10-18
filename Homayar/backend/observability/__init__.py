"""Operational observability, audit sink and release identity (WP20, WP01; ADR 0036).

* ``correlation`` – one correlation id per request, job attempt or sweep run,
  carried into logs and audit events.
* ``scrub`` – removes credential-shaped values from log lines.
* ``heartbeats`` – last run / success / outcome counters of every periodic
  sweep, kept in Redis so the API can expose them.
* ``metrics`` – the Prometheus registry served at ``/metrics``: bounded
  labels only, no tenant ids, no content.
* ``release`` – build information baked into the image and the migration head.
"""
