"""Privacy gate harness: corpora, gate runner and smoke test (HOM-321).

Every ``text_pii`` privacy ticket rebuilt the same gate harness from scratch
and mission cleanup then deleted it.  This package keeps one harness in the
repo, so the next privacy ticket runs one command instead of building its
own.  See ``README.md`` for the commands and the gates.
"""
