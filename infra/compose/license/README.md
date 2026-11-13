# Default offline licence folder

The `api` and `worker` services of `compose.onprem.yaml` mount this folder
read-only at `/app/license` when `OFFLINE_LICENSE_DIR` is not set. It ships
in every package (`pack` copies `infra/compose`), so Docker never creates a
host folder for the bind.

It holds no licence. With `OFFLINE_LICENSE_FILE` empty the licence is not
enforced. To enforce one, put `license.json`, `license.json.sig` and
`keys/*.pem` under `$HOMAYAR_HOME/config/license` and set
`OFFLINE_LICENSE_DIR` and `OFFLINE_LICENSE_FILE` in `config/onprem.env`
(`docs/operations/onprem-lifecycle.md`, "Offline licence"; ADR 0073).
