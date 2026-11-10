#!/usr/bin/env bash
# HOM-383: the host nginx site of demo.homayar.com (run by the owner with sudo).
#
#   sudo bash infra/scripts/demo-site-nginx.sh --check
#   sudo bash infra/scripts/demo-site-nginx.sh --apply
#   sudo bash infra/scripts/demo-site-nginx.sh --undo
#
# Runbook: docs/operations/demo-site.md. It writes exactly one file,
# /etc/nginx/sites-available/demo.homayar.com.conf, and one symlink in
# sites-enabled, nothing else: no other file under /etc/nginx or
# /etc/letsencrypt is ever touched. The site terminates TLS (the certificate
# of demo.homayar.com) on the relay tunnel listener 127.0.0.1:10443 and
# proxies to the demo stack on 127.0.0.1:8090. There is no port 80 server
# (dead since HOM-374).
#
# --apply requires the certificate and the shared snippet first. It backs up
# an existing demo site file, writes file and symlink, and gates every change
# on nginx -t: on failure it restores the exact previous state and exits 1
# with no reload; on success it runs systemctl reload nginx (never restart,
# never stop), then systemctl is-active nginx. Tell the owner before any
# reload. --undo removes the symlink and the demo site file (keeping a backup
# copy), gated the same way.
#
# Exit codes: 0 ok, 1 the check failed or nginx -t failed (previous state
# restored, no reload), 2 usage error or not run as root.
#
# Test seam (test-only, never set on the server): DEMO_NGINX_TEST_ROOT
# prefixes every /etc/... and /var/log/... path, so the selftest can run
# without root against a fake tree. nginx, systemctl, id and ss are stubs
# there.
set -euo pipefail
export LC_ALL=C

ROOT_PREFIX="${DEMO_NGINX_TEST_ROOT:-}"
NGINX_ETC="$ROOT_PREFIX/etc/nginx"
SITES_AVAIL="$NGINX_ETC/sites-available"
SITES_EN="$NGINX_ETC/sites-enabled"
SNIPPET="$NGINX_ETC/snippets/relay-tunnel-realip.conf"
WSMAP="$NGINX_ETC/conf.d/00-websocket-map.conf"
CERT_DIR="$ROOT_PREFIX/etc/letsencrypt/live/demo.homayar.com"
CERT_PEM="$CERT_DIR/fullchain.pem"
KEY_PEM="$CERT_DIR/privkey.pem"
SITE_NAME=demo.homayar.com
SITE_FILE="$SITES_AVAIL/$SITE_NAME.conf"
SITE_LINK="$SITES_EN/$SITE_NAME.conf"
BACKUP_PREFIX="$SITE_FILE.bak-"
DEMO_PORT=8090

MODE=""
TS="$(date -u +%Y%m%dT%H%M%SZ)"

usage() {
  echo "usage: sudo bash $0 --check|--apply|--undo"
  echo "  --check  read-only: the site it would write, a diff, and the prerequisites"
  echo "  --apply  write file and symlink, nginx -t, reload (with a backup before)"
  echo "  --undo   remove file and symlink (keeping a backup copy), nginx -t, reload"
}

say() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 2; }

# --- the site file ---------------------------------------------------------------

site_content() {  # the whole file on stdout
  cat <<'SITE'
# HOM-383 demo.homayar.com: the demo console (/app/) and API (/api/v1/).
# Runbook: docs/operations/demo-site.md in the Homayar repository.
# Public only through the relay tunnel: frpc sends relay traffic to
# 127.0.0.1:10443 with a PROXY protocol header. TLS ends here. The demo
# stack (Docker, user gpu) listens on 127.0.0.1:8090. There is no port 80
# server (dead since HOM-374), and no LAN listens: LAN users use the public
# name, which goes through the relay.

server {
    listen 127.0.0.1:10443 ssl http2 proxy_protocol;
    include snippets/relay-tunnel-realip.conf;
    server_name demo.homayar.com;

    ssl_certificate     /etc/letsencrypt/live/demo.homayar.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/demo.homayar.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    access_log /var/log/nginx/demo.homayar.com.access.log;
    error_log  /var/log/nginx/demo.homayar.com.error.log;

    # One HSTS header, set here at the TLS boundary. The API sends its own with
    # includeSubDomains and preload; hide it so only this one reaches the browser.
    proxy_hide_header Strict-Transport-Security;
    add_header Strict-Transport-Security "max-age=31536000" always;

    # The stack refuses bodies over 16m outside the upload route
    # (infra/nginx/conf.d/homayar.conf). A larger limit here would let anyone make
    # this host buffer a big body to disk before the stack's 413.
    client_max_body_size 16m;

    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    # OVERWRITE, never append: a client value must not reach the stack.
    proxy_set_header X-Forwarded-For   $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host  $host;
    proxy_set_header Upgrade           $http_upgrade;
    proxy_set_header Connection        $connection_upgrade;
    # The stack's nginx and the API build absolute redirects with http:// (and the
    # nginx one with port 8080). Send them to the browser as https on the host the
    # browser used: $http_host keeps the port (like app.homayar.com, HOM-374).
    proxy_redirect http://demo.homayar.com:8080/ https://$http_host/;
    proxy_redirect http://demo.homayar.com/ https://$http_host/;
    proxy_connect_timeout 5s;

    # Recording uploads (POST /api/v1/uploads): 300m and streaming, with the stack's
    # limit and timeouts (infra/nginx/includes/upload-proxy.conf).
    location ^~ /api/v1/uploads {
        client_max_body_size 300m;
        proxy_request_buffering off;
        proxy_buffering off;
        client_body_timeout 15m;
        proxy_send_timeout 15m;
        proxy_read_timeout 15m;
        proxy_pass http://127.0.0.1:8090;
    }

    # Recording download, inline playback and share link (HOM-340): the API streams
    # them. With 0, nginx reads only as fast as the visitor takes the bytes, instead
    # of copying up to 1024m per response to this host's disk.
    location ~ ^/api/v1/(meetings/[^/]+|workspaces/[^/]+/meetings/[^/]+|shares/[^/]+)/recording$ {
        proxy_max_temp_file_size 0;
        proxy_pass http://127.0.0.1:8090;
    }

    location / {
        proxy_pass http://127.0.0.1:8090;
    }
}
SITE
}

# --- prerequisites ----------------------------------------------------------------

port_listens() {  # 0 when 127.0.0.1:$DEMO_PORT listens (the demo proxy's published port)
  ss -H -ltn 2>/dev/null | awk -v a="127.0.0.1:$DEMO_PORT" '$4 == a { found = 1 } END { exit !found }'
}

other_site_uses_name() {  # prints enabled sites (not ours) with this server_name
  local f
  [[ -d "$SITES_EN" ]] || return 1
  for f in "$SITES_EN"/*; do
    [[ -e "$f" || -L "$f" ]] || continue
    [[ "$(basename -- "$f")" == "$SITE_NAME.conf" ]] && continue
    if grep -q 'server_name demo\.homayar\.com' "$f" 2>/dev/null; then
      printf '%s\n' "$f"
    fi
  done
  return 0
}

run_checks() {  # 0 when every prerequisite holds; prints PASS/FAIL per item
  local ready=1 other
  if [[ "$(id -u)" == 0 ]]; then
    say "  P1 PASS: running as root (uid 0)"
  else
    say "  P1 FAIL: run with sudo (as root)"
    ready=0
  fi

  if [[ -f "$CERT_PEM" && -r "$CERT_PEM" ]]; then
    say "  P2 PASS: certificate $CERT_PEM exists"
  else
    say "  P2 FAIL: certificate $CERT_PEM is missing (runbook, owner step 2)"
    ready=0
  fi
  if [[ -f "$KEY_PEM" && -r "$KEY_PEM" ]]; then
    say "  P3 PASS: key $KEY_PEM exists"
  else
    say "  P3 FAIL: key $KEY_PEM is missing"
    ready=0
  fi

  if [[ -f "$SNIPPET" ]]; then
    say "  P4 PASS: snippet $SNIPPET exists"
  else
    say "  P4 FAIL: snippet $SNIPPET is missing"
    ready=0
  fi

  if [[ -f "$WSMAP" ]]; then
    say "  P5 PASS: websocket map $WSMAP exists"
  else
    say "  P5 FAIL: websocket map $WSMAP is missing"
    ready=0
  fi

  if port_listens; then
    say "  P6 PASS: 127.0.0.1:$DEMO_PORT listens (the demo proxy)"
  else
    say "  P6 FAIL: nothing listens on $DEMO_PORT (run demo-site.sh --apply first)"
    ready=0
  fi

  other="$(other_site_uses_name || true)"
  if [[ -z "$other" ]]; then
    say "  P7 PASS: no other enabled site uses server_name $SITE_NAME"
  else
    say "  P7 FAIL: another enabled site uses server_name $SITE_NAME: $other"
    ready=0
  fi

  say "  The site file --apply would write: $SITE_FILE"
  # A process substitution, not a temp file: --check writes nothing.
  if [[ -f "$SITE_FILE" ]]; then
    say "  Diff against the live site file:"
    diff -u "$SITE_FILE" <(site_content) | sed 's/^/    | /' || true
  else
    say "  No live site file yet (this --apply would create it):"
    site_content | sed 's/^/    | /'
  fi
  return $(( 1 - ready ))
}

# --- state save and restore --------------------------------------------------------

save_state() {  # record the current file and symlink into $SAVED_DIR
  SAVED_DIR="$(mktemp -d)"
  if [[ -f "$SITE_FILE" ]]; then
    cp -p "$SITE_FILE" "$SAVED_DIR/site-file"
    printf 'file\n' >"$SAVED_DIR/file-existed"
  fi
  if [[ -L "$SITE_LINK" ]]; then
    printf '%s\n' "$(readlink "$SITE_LINK")" >"$SAVED_DIR/link-target"
  fi
}

restore_state() {  # put the exact previous file and symlink back
  if [[ -f "$SAVED_DIR/file-existed" ]]; then
    cp -p "$SAVED_DIR/site-file" "$SITE_FILE"
  else
    rm -f -- "$SITE_FILE"
  fi
  if [[ -f "$SAVED_DIR/link-target" ]]; then
    ln -sfn "$(cat "$SAVED_DIR/link-target")" "$SITE_LINK"
  else
    rm -f -- "$SITE_LINK"
  fi
  rm -rf -- "$SAVED_DIR"
  SAVED_DIR=""
}

write_site() {  # file (644) and symlink
  site_content >"$SITE_FILE"
  chmod 644 "$SITE_FILE"
  ln -sfn "../sites-available/$SITE_NAME.conf" "$SITE_LINK"
}

nginx_t_ok() { nginx -t; }

reload_nginx() {
  systemctl reload nginx
  sleep 1
  if [[ "$(systemctl is-active nginx)" == active ]]; then
    say "nginx reloaded and active (MainPID kept; a reload never restarts it)."
    return 0
  fi
  say "WARNING: systemctl is-active nginx did not answer active."
  return 1
}

# --- main -------------------------------------------------------------------------

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check|--apply|--undo)
        [[ -z "$MODE" ]] || { usage; exit 2; }
        MODE="${1#--}"
        shift
        ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage; exit 2; }

  if [[ "$(id -u)" != 0 ]]; then
    fail "REFUSED: run as root (sudo bash $0 $MODE); this script edits /etc/nginx."
  fi

  if [[ "$MODE" == check ]]; then
    say "demo.homayar.com nginx site, --check (read-only):"
    if run_checks; then
      say "Ready: sudo bash $0 --apply would write $SITE_FILE and the symlink, then reload."
      exit 0
    fi
    say "Not ready."
    exit 1
  fi

  if [[ "$MODE" == apply ]]; then
    say "demo.homayar.com nginx site, --apply:"
    if ! run_checks; then
      say "REFUSED: a prerequisite failed (above); nothing was changed."
      exit 2
    fi
    if [[ -f "$SITE_FILE" ]]; then
      cp -p "$SITE_FILE" "$BACKUP_PREFIX$TS"
      say "Backed up the existing site file to $BACKUP_PREFIX$TS"
    fi
    save_state
    write_site
    say "Wrote $SITE_FILE (644) and the symlink $SITE_LINK"
    if ! nginx_t_ok; then
      say "nginx -t failed after the change; restoring the previous state."
      restore_state
      say "Previous state restored. No reload was run. Fix the cause and retry."
      exit 1
    fi
    rm -rf -- "$SAVED_DIR" 2>/dev/null || true
    SAVED_DIR=""
    reload_nginx || exit 1
    say "Site live: https://demo.homayar.com:8080/app/ (check from outside, runbook owner step 5)."
    exit 0
  fi

  # --undo
  if [[ ! -f "$SITE_FILE" && ! -L "$SITE_LINK" ]]; then
    say "Nothing to undo: $SITE_FILE and $SITE_LINK do not exist."
    exit 0
  fi
  say "demo.homayar.com nginx site, --undo:"
  if [[ -f "$SITE_FILE" ]]; then
    cp -p "$SITE_FILE" "$BACKUP_PREFIX$TS"
    say "Kept a backup copy at $BACKUP_PREFIX$TS"
  fi
  save_state
  rm -f -- "$SITE_LINK" "$SITE_FILE"
  say "Removed $SITE_LINK and $SITE_FILE (nothing else under $NGINX_ETC)"
  if ! nginx_t_ok; then
    say "nginx -t failed after the removal; restoring the previous state."
    restore_state
    say "Previous state restored. No reload was run."
    exit 1
  fi
  rm -rf -- "$SAVED_DIR" 2>/dev/null || true
  SAVED_DIR=""
  reload_nginx || exit 1
  say "The demo site is gone from nginx. The stack itself is removed by demo-site.sh --undo --yes."
  exit 0
}

SAVED_DIR=""
main "$@"
