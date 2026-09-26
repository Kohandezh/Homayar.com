# Operator model manifests

Mounted read-only at `/app/manifests` in the API, worker and scheduler
containers (`MODEL_MANIFEST_DIR`, default this directory). List the files to
load in `MODEL_MANIFEST_PATHS` (comma separated, container paths such as
`/app/manifests/operator.json`). Nothing here is loaded by default.

Qualify a manifest with `python -m backend.jobs.model_preflight` and register
it with `--register` after deployment (ADR 0026). Never put credentials in a
manifest: endpoints and model identities only.
