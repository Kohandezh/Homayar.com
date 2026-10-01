"""Voicemail processing (owner Priority-0; AppVision V01, F36, F57, F59, F60).

A voicemail is an ordinary conversation: its audio enters through the
canonical ingestion ledger (``services.ingestion_service``) and is validated,
stored and transcribed by the existing upload path and ``jobs.processor``.
This package only adds what is voicemail-specific on top of that meeting:

* ``models``   - the ``voicemail_messages`` side table (one row per meeting);
* ``ingest``   - idempotent import through the existing ingestion path;
* ``classify`` - a deterministic, versioned rule classifier (pluggable);
* ``pipeline`` - ``on_transcript_ready(session, meeting_id)``: classify,
  prioritise, create the follow-up action and the urgent notification;
* ``search``   - tenant-scoped listing/search and analytics.
"""
