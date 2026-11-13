"""The retrieval index of the conversation library (HOM-93, WP17-T02, F57/F58).

One entry is one text-view segment of one revision (ADR 0068).  This package
fills and reads ``retrieval_entries`` and ``retrieval_embeddings``:

* :mod:`backend.retrieval.index` writes one entry per non-empty text-view
  segment next to the revision the processor derives, supersedes the entries of
  revisions that stopped being current, removes the entries of views under
  another redaction policy, and re-syncs the ACL snapshot when a call's team or
  agent changes;
* :mod:`backend.retrieval.embeddings` names the embedding space with a
  versioned digest, packs vectors as portable float32 bytes and writes one
  embedding per entry and version through an injected ``EmbeddingProvider``;
* :mod:`backend.retrieval.lexical` and :mod:`backend.retrieval.vector_store`
  read the index under the live record access, the live revision pointer and
  the workspace's current default reader view, never the stored snapshot
  alone (HOM-94 owns the evidence read path that sits on top);
* :mod:`backend.retrieval.evidence` re-resolves membership and record access
  from live rows before every search and every evidence fetch, filters
  before any candidate is scored, and serves id-asked fetches with the same
  checks (HOM-94, ADR 0077).
"""
