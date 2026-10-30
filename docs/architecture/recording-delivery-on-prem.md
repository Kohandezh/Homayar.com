# Recording delivery on-prem: stream through the API

Status: accepted for HOM-340 (owner direction: option 1 of the ticket). Date: 2026-10-30.

## Problem

Three routes send recording bytes:

- `GET /api/v1/meetings/{id}/recording` (download, `routers/v1/meetings.py`)
- `GET /api/v1/workspaces/{ws}/meetings/{id}/recording` (inline playback, the console's
  `media_url`)
- `GET /api/v1/shares/{token}/recording` (public share link, `routers/v1/shares.py`)

All three call `deliver_recording` (`Homayar/backend/services/download_service.py`). With S3
storage, it answered `307` to a presigned URL on the storage endpoint. On-prem that endpoint is the
bundled MinIO at `http://minio:9000` (`STORAGE_ENDPOINT_URL` in `infra/compose/compose.onprem.yaml`).
A browser cannot resolve `minio`, and an `https://` page cannot load an `http://` media URL. So
download, playback and share links did not work on-prem.

There was a second gap. Without presigning, the code streamed from `open_reader`. The S3 body from
botocore cannot seek, so a `Range` request got the whole object with status `200`. A player could
not seek.

## Decision

On-prem, the API streams recordings itself. It never redirects to MinIO.

- New setting `STORAGE_PRESIGNED_DELIVERY` (`storage_presigned_delivery`, default `true`).
  `true` keeps today's cloud behavior: `307` to a presigned URL, `X-Homayar-Revocation:
  expiry-bounded`. `false` streams through the API.
- `compose.onprem.yaml` sets `STORAGE_PRESIGNED_DELIVERY: "false"` in `x-app-environment`, next to
  `STORAGE_ENDPOINT_URL`. `compose.onprem.app-server.yaml` does not override it.
- `create_object_storage` passes the setting to `S3ObjectStorage(presigned_delivery=...)`.
  `deliver_recording` redirects only when `storage.supports_presigned_access` and
  `storage.presigned_delivery` are both true. The flag travels with the storage that the route
  builds from its own request settings. The routers do not change.
- New storage method `open_range_reader(tenant_id, key, start=, end=)`. S3 sends one `GetObject`
  with `Range: bytes=start-end`, so only those bytes leave MinIO. The base class (used by local
  storage) seeks. A reader that cannot seek is read forward one chunk at a time.
- `Range` rules are unchanged (`parse_range`): `bytes=a-b`, `bytes=a-`, `bytes=-n`. A range that
  cannot be served is still `416` with `Content-Range: bytes */<size>`, and storage is not read.
- The response keeps today's headers: `Content-Disposition` (attachment or inline),
  `Accept-Ranges: bytes`, `Cache-Control: no-store`, `X-Homayar-Revocation: immediate`. It has no
  `Location` and no storage address.
- Bytes go out in chunks of 1 MiB (`CHUNK_BYTES`). The API never holds the whole object in memory.
- Authorization and the share access audit run before `deliver_recording`, as before. Each `Range`
  request is authorized again, so revocation is immediate on-prem (a presigned URL stays valid
  until it expires).

### Options not taken

- **A public presign endpoint, or nginx routes to MinIO.** MinIO would face the internet, and
  `http://` between nginx and MinIO stays plaintext (HOM-196). The owner ruled it out.
- **A published MinIO port.** Same reasons. MinIO must never get a published port.

## Cost

- **Bandwidth.** Every recording byte now passes through the API, the stack nginx, the host TLS
  front, frpc and the relay VPS on `app.homayar.com`. A browser reached none of these for the bytes
  before, but that path never worked on-prem anyway. Plan relay traffic as about the total size of
  the recordings people play or download. Recordings are at most `MAX_UPLOAD_SIZE_MB` (250 MB by
  default).
- **API.** Each open stream holds one connection and one chunk in memory. The sync generator runs
  one threadpool call per chunk, so a slow client does not hold a thread between chunks. The API
  runs `UVICORN_WORKERS` workers (default 2, `compose.onprem.yaml:220`).
- **Seeking.** A player that seeks sends a new `Range` request. Each one is one ranged
  `GetObject`, not a read from byte 0.

## nginx and relay check for large streamed responses

The stack's nginx (`infra/nginx/`):

| Fact | Where | Effect on a large streamed recording |
| --- | --- | --- |
| `location ^~ /api/v1/` sets no `proxy_buffering`, so nginx buffers. `proxy_max_temp_file_size` has its default of 1024m. | `infra/nginx/conf.d/homayar.conf:78-83` | When the client reads slower than the API sends, nginx writes the rest of the response to temp files. |
| Temp files go to `/tmp/proxy`. | `infra/nginx/nginx.conf:41` | |
| `/tmp` is a tmpfs of 128m. The app-server overlay limits the proxy to 256m, tmpfs included. | `infra/compose/compose.onprem.yaml:176-177`, `infra/compose/compose.onprem.app-server.yaml:10-16` | One 250 MB recording, or a few smaller ones at once, fill the tmpfs. nginx then cannot write the temp file and the response breaks. A media element that asks `bytes=0-` and stops reading once it has enough makes this likely. **This would break large recordings.** |
| `proxy_buffer_size 16k`, `proxy_buffers 16 16k`, `proxy_busy_buffers_size 32k` | `infra/nginx/includes/proxy-common.conf:11-13` | Memory buffers per response, about 272k. Fine. |
| `send_timeout 60s` | `infra/nginx/nginx.conf:35` | A player paused for more than 60 s loses the connection and asks again with `Range`. Fine. |
| `proxy_read_timeout`: not set, default 60s | `proxy-common.conf` has none | Only counts while nginx waits for API data. The API sends without pauses. Fine. |
| `client_max_body_size 16m` | `homayar.conf:79` | Request body only. GET has none. Fine. |
| `limit_req zone=api_per_ip burst=40` at 20r/s, `limit_conn per_ip 20` | `nginx.conf:70,73`, `homayar.conf:80-81` | A player sends a few `Range` requests. Fine. |
| `gzip_types` has no `audio/*` or `video/*` | `nginx.conf:51-58` | Recordings are not compressed, so `Content-Length` and `Range` stay correct. |

**Fix (in this change).** A nested location for exactly the three recording routes, inside
`location ^~ /api/v1/` (`homayar.conf:91-98`). It has the same `client_max_body_size`, `limit_req`,
`limit_conn`, proxy include and `proxy_pass`, plus `proxy_max_temp_file_size 0`. nginx keeps its
memory buffers but writes no temp file. It then reads from the API only as fast as the client takes
the bytes, and back-pressure reaches the API. The location is nested because `^~` stops nginx from
checking server-level regex locations, while a prefix location's own regex locations are still
checked. A throwaway run of the pinned nginx image showed the three recording URLs served by the
new block, and `/playback`, `/recording/extra` and `/recordings` by the old one.

The host TLS front of `app.homayar.com` (`docs/operations/app-server.md`, the `server` block for
`127.0.0.1:10443`):

- `location /` (`app-server.md:555-557`) uses the default buffering, with temp files on the
  host's disk up to 1024m for each response. It does not break, because 1024m is more than one
  recording. But each slow download could hold up to 250 MB of host disk.
- **Fix (in this change, applied later):** the same regex location with
  `proxy_max_temp_file_size 0` (`app-server.md:550-553`). It is a server-level regex location, so
  it wins over `location /` (a plain prefix). It inherits the server's `proxy_set_header` lines,
  `client_max_body_size 16m` and timeouts.

The relay (frps on the VPS, frpc on the server, `docs/operations/relay-tunnel.md`) forwards raw TCP.
It has no HTTP buffer or body limit. Its only cost is bandwidth.

## Production rollout steps

Written here, run later by a separate reviewed script. Do not run them by hand. Each step that
restarts something needs homlead's go first.

1. Merge the PR after CI is green. Note the merge commit `<sha>`.
2. Before anything is pulled or built, check the MinIO keys of the production env, as counts
   only. Never print a key. The HOM-340 bootstrap refuses these characters, and then `api` and
   `worker` do not start. Each count must be 0: the number of `:`, line breaks and carriage
   returns in `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`, and of line breaks and carriage returns
   in `STORAGE_ACCESS_KEY` and `STORAGE_SECRET_KEY`. For a key stored as one file, for example:
   `tr -cd ':\r' < <key file> | wc -c` prints 0, and `wc -l < <key file>` prints at most 1 (one
   final newline). If a count is not 0, stop and rotate the key first.
3. On the server, prepare the new release folder and images as in `app-server.md`, "Update to a
   New Commit", steps 1 and 2. `Homayar/` changed, so build both images with the new tag (no
   re-tag).
4. Before the switch, check the new release's Compose file, not the rendered config (never run
   `docker compose config` without `--quiet`: it prints every secret, `app-server.md`, "Never
   print a secret"):
   `grep -c 'STORAGE_PRESIGNED_DELIVERY: "false"' ~/homayar/releases/$S12/infra/compose/compose.onprem.yaml`
   prints `1`. The value is fixed in `x-app-environment`, not read from the env file, and the
   app-server overlay does not override it. Then `~/homayar/hc config --quiet` exits 0.
5. Switch `current` and run `~/homayar/hc up -d --no-build --pull never` (steps 4 and 6 of the
   runbook). This recreates `api`, `worker` and `proxy`. The proxy gets the new `homayar.conf`.
   `minio-bootstrap` runs again (it is idempotent). After `up`, check the running values (each
   prints only `false`): `~/homayar/hc exec -T api printenv STORAGE_PRESIGNED_DELIVERY` and the
   same for `worker`.
6. Host TLS front: put the new regex location from `app-server.md` into
   `/etc/nginx/sites-available/app.homayar.com.conf`, run `nginx -t`, then a graceful reload. This
   is an nginx reload on the shared host. Announce it first; open connections stay. Then measure
   the file's new sha256 and update it in `app-server.md` ("Host nginx and certbot" table).
7. Check through the public name, as a test account with a short recording:
   - `curl -sS -o /dev/null -D - -b <cookie file> -H 'Range: bytes=0-9' <download URL> | grep -iE '^(HTTP|content-range|content-length|location)'`
     shows `206`, `Content-Range: bytes 0-9/<size>`, and no `Location` line. The session cookie
     stays in a file (mode 600), never on the command line, where other users of the host can
     read it. Then `... | grep -ci minio` on the same headers prints 0.
   - Without `Range`: `200` and the full size.
   - In the console, play a recording and seek. Open a share link with audio and play it.
8. Watch for one day: the proxy log has no `pwrite()` or `No space left on device` lines
   (`~/homayar/hc logs proxy | grep -c 'No space left'` prints 0). The relay traffic matches the
   expected recording traffic.

**Rollback.** Switch `current` back to the old release and run `up -d` (runbook "Rollback"). No
migration is involved. Downloads and playback then fail again as before. For the host front, remove
the location and reload. Leaving it in place is also safe.
