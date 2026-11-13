"""Sanitized support bundle for on-prem installs (HOM-110, ADR 0064).

``collect`` reports container-side facts with an allowlist-only settings
view; ``scan`` looks for secrets, transcripts and identities in a directory
tree so the shell command can refuse to write a bundle that holds any.
"""
