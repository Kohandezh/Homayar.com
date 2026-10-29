# Relay Tunnel Runbook

This runbook covers the relay VPS and the frp tunnel that publish sites from the local
production server to the internet (HOM-332). No secret value is in this file.

Published through the tunnel now: `inotex.padyar.com`, `elecomp.padyar.com` and
`inotexpwa.padyar.com`. `app.homayar.com` comes later (HOM-328, see the last part of this
runbook).

## How It Works

```text
visitor  -> TCP 443 or 80 ->  relay 193.93.169.205 (frps)
                                  |  raw TCP, TLS does not end here
                                  v
            tunnel on TCP 7000 (TLS + token), opened by frpc from the local server
                                  |
                                  v
local server: frpc (Docker) -> nginx 127.0.0.1:10443 or 127.0.0.1:10080
                               with a PROXY protocol v2 header;
                               TLS ends here and nginx picks the site by SNI
```

- The relay forwards raw TCP. It holds no site certificate and no app secret.
- frpc on the local server opens the connection to the relay. The local server needs no open
  inbound port.
- frpc adds a PROXY protocol v2 header to each connection. nginx reads the real client IP
  from it, so logs, app rate limits and the admin lockout see the real visitor.
- nginx chooses the site by the TLS SNI name, the same way it does for LAN traffic.

## Hosts

| Host | SSH alias | Address | Role |
| --- | --- | --- | --- |
| Relay VPS | `homayar-relay` | public `193.93.169.205`, private `172.20.89.194` (1:1 NAT) | Ubuntu 24.04, user `ubuntu`, sudo without password. Runs `frps` only. Hostname `local-sites-tunnel`. OpenStack VM, provider security group `relay-web`. |
| Local server | `homayar-local` | LAN `192.168.100.6` | Hostname `gpuserver`, user `gpu`. Runs the live Padyar products, nginx with 5 `padyar.com` sites, fail2ban, and the frpc container. `sudo` needs the owner's password. |

Rules for the local server:

- Never stop, restart or reconfigure nginx, fail2ban, PostgreSQL, the Padyar services or the
  GitHub runner yourself. Every `sudo` step goes to the owner as an exact command list.
- The tunnel side there runs as user `gpu` in Docker, so it needs no `sudo`.

## Ports

| Host | Port | Listener | Why |
| --- | --- | --- | --- |
| Relay | 22/tcp | sshd | Admin access. Key login only. |
| Relay | 80/tcp | frps (proxy `http-80`) | HTTP. The sites answer 301 to HTTPS, and the ACME path stays reachable. |
| Relay | 443/tcp | frps (proxy `https-443`) | HTTPS, raw TCP. TLS ends on the local nginx. |
| Relay | 7000/tcp | frps (`bindPort`) | frpc control channel. TLS is forced, the client needs the token, and frpc checks the relay certificate against the tunnel CA. Open to all, because the local server's egress IP changes. |
| Local | 127.0.0.1:10080 | nginx, `proxy_protocol` | Tunnel HTTP. Loopback only. |
| Local | 127.0.0.1:10443 | nginx, `ssl http2 proxy_protocol` | Tunnel HTTPS. Loopback only. |

frps listens on 80 and 443 only while frpc is logged in. Right after a relay reboot, only 22
and 7000 listen until frpc reconnects.

Off on purpose: the frps dashboard (`webServer.port`), the frpc admin server, vhost ports,
KCP, QUIC, tcpmux and the SSH gateway. None of them is set in the configs, so none listens.

Firewalls:

- Relay ufw: default deny incoming, allow 22, 80, 443, 7000 (IPv4 and IPv6 rules). This is
  the real filter. The provider security group `relay-web` also has an "all inbound" rule, so
  do not rely on it.
- Local ufw is active. Loopback traffic (frpc to nginx) is always allowed. The LAN cannot
  reach 10080 or 10443, because nginx binds them to `127.0.0.1`.

## Files

### Relay

| Path | Owner | Mode | What |
| --- | --- | --- | --- |
| `/usr/local/bin/frps` | root:root | 0755 | frp v0.71.1 server binary |
| `/etc/frp/` | root:frps | 0750 | config folder |
| `/etc/frp/frps.toml` | root:frps | 0640 | frps config, no secret |
| `/etc/frp/token` | frps:frps | 0600 | tunnel token (64 hex characters) |
| `/etc/frp/tls/ca.crt` | root:frps | 0644 | tunnel CA certificate (public) |
| `/etc/frp/tls/server.crt` | root:frps | 0644 | frps certificate for `relay-tunnel.internal` (public) |
| `/etc/frp/tls/server.key` | frps:frps | 0600 | frps private key |
| `/root/frp-ca/` | root:root | 0700 | CA folder: `ca.key` (0600), `ca.crt`, `ca.cnf`, `ca.srl`, `server.crt` |
| `/etc/systemd/system/frps.service` | root:root | 0644 | systemd unit |

- frps runs as the system user `frps` (no login shell, no home). The unit gives it only
  `CAP_NET_BIND_SERVICE`, so it can bind 80 and 443 without root.
- The unit has `Restart=always`, `RestartSec=5` and is enabled at boot.
- frps logs to the journal: `journalctl -u frps`.
- The CA private key `/root/frp-ca/ca.key` never leaves the relay. It is kept so the server
  certificate can be renewed without a change on the local server.
- Tunnel CA SHA-256 fingerprint (public):
  `8F:EC:E4:56:39:C2:FB:17:01:AA:9D:36:61:5E:FB:24:C8:8C:C6:0B:F8:5F:69:A6:9A:24:B5:87:9E:90:2D:12`.
- Expiry: server certificate 2029-10-28, CA 2036-10-26.

### Local server

All under `/home/gpu/relay-tunnel/` (folder 0700, owner `gpu`):

| Path | Mode | What |
| --- | --- | --- |
| `frp_0.71.1_linux_amd64.tar.gz`, `frp_sha256_checksums.txt` | 0664 | the verified download, kept for audit |
| `build/frpc`, `build/Dockerfile` | 0755, 0644 | image source (`FROM scratch`, the static frpc binary only) |
| `frpc.toml` | 0644 | frpc config, no secret |
| `tls/ca.crt` | 0644 | tunnel CA certificate (public), same fingerprint as above |
| `secrets/` | 0700 | |
| `secrets/token` | 0600 | tunnel token, owner `gpu` (uid 1000), the same uid as the frpc process |

Container:

| Item | Value |
| --- | --- |
| Image | `relay-tunnel-frpc:0.71.1` (one layer, user `1000:1000`) |
| Container | `relay-tunnel-frpc` |
| Network | `--network host` (frpc reaches nginx on `127.0.0.1`) |
| Restart policy | `unless-stopped` (Docker is enabled at boot) |
| Hardening | `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, no published ports |
| Mounts (read-only) | `frpc.toml` to `/etc/frp/frpc.toml`, `tls/ca.crt` to `/etc/frp/ca.crt`, `secrets/token` to `/etc/frp/token` |
| Logs | `docker logs relay-tunnel-frpc` (json-file, 3 x 10 MB) |

The token is read from a file by frp itself (`auth.tokenSource`, frp v0.64.0 and later). It is
never in a config file, a command line or a container environment variable.

The exact `docker run` command is in "Rebuild or Recreate the Container".

First start: 2026-10-29 06:06 UTC. frpc logged in 0.8 s after the start. Image ID
`sha256:415414a29bead345effc2f104811390ae5936127c2b11b80007e3211e7667e9c`. Docker does not
remap user IDs on this host, so uid 1000 in the container is `gpu` and can read the token.

### nginx and fail2ban on the local server

Added by the owner (HOM-332):

| Path | What |
| --- | --- |
| `/etc/nginx/snippets/relay-tunnel-realip.conf` | `set_real_ip_from 127.0.0.1;` and `real_ip_header proxy_protocol;` |
| `/etc/nginx/conf.d/05-relay-tunnel-default.conf` | default server for 10080 and 10443: refuses every name without a tunnel listen |
| `/etc/fail2ban/jail.d/relay-tunnel.conf` | `[DEFAULT] ignoreip = 127.0.0.1/8 ::1` |
| `/root/hom332-backup/nginx-fail2ban-before.tgz` | backup of `/etc/nginx` and `/etc/fail2ban` from before the change. Keep it; never overwrite it. Later backups get a dated name. |

Edited by the owner: in each of `/etc/nginx/sites-available/inotex.padyar.com.conf`,
`elecomp.padyar.com.conf` and `inotexpwa.padyar.com.conf`, 4 lines:

```nginx
# in the port 80 server, after "listen [::]:80;"
    listen 127.0.0.1:10080 proxy_protocol;
    include snippets/relay-tunnel-realip.conf;
# in the port 443 server, after "listen [::]:443 ssl http2;"
    listen 127.0.0.1:10443 ssl http2 proxy_protocol;
    include snippets/relay-tunnel-realip.conf;
```

Where the lines go (tested read-only on the live files, 2026-10-29): inotex and elecomp after
lines 14 and 31, inotexpwa after lines 8 and 21.

The InotexPWA auto-deploy (GitHub runner `gh-runner-inotex-pwa`) does not touch this file. It
only runs `sudo /usr/local/bin/inotex-pwa-deploy`, which swaps the webroot
`/var/www/inotex-pwa`. But the InotexPWA repo has its own copy of the site file, and its
`deploy/README.md` tells an operator to `sudo cp` it into `sites-available`. If someone does
that by hand, the 4 tunnel lines are lost. Then follow "Re-apply the Tunnel Lines" below.

### Expected LAN status codes

From the local server, `curl --resolve <site>:443:192.168.100.6 https://<site>/` and the same
on port 80. These were the same before and after HOM-332 (2026-10-29):

| Site | 443 | 80 |
| --- | --- | --- |
| avatar | 502 | 301 |
| elecomp | 200 | 301 |
| inotex | 200 | 301 |
| inotexpwa | 200 | 301 |
| live | 502 | 301 |

avatar and live answered 502 before this work too. Their backends are not running, and that is
not part of the tunnel.

```bash
for s in avatar elecomp inotex inotexpwa live; do printf '%s 443=%s 80=%s\n' $s "$(curl -s -o /dev/null -w '%{http_code}' --resolve $s.padyar.com:443:192.168.100.6 https://$s.padyar.com/)" "$(curl -s -o /dev/null -w '%{http_code}' --resolve $s.padyar.com:80:192.168.100.6 http://$s.padyar.com/)"; done
```

### Re-apply the Tunnel Lines

Use this when a site file lost its tunnel lines (for example after a manual copy from the
InotexPWA repo). It is an owner step with `sudo`. Run it in one terminal, because `B` is used
again in the rollback.

1. A new, dated backup. Never overwrite `/root/hom332-backup/nginx-fail2ban-before.tgz`: it is
   the state from before HOM-332.

   ```bash
   B=/root/hom332-backup/nginx-reapply-$(date -u +%Y%m%dT%H%M%SZ).tgz
   sudo tar -czf "$B" -C / etc/nginx && sudo ls -l /root/hom332-backup/
   ```

2. Edit only a file that has no tunnel lines. A file that still has them is skipped.

   ```bash
   for f in inotex elecomp inotexpwa; do
     p=/etc/nginx/sites-available/$f.padyar.com.conf
     if sudo grep -q relay-tunnel "$p"; then
       echo "$f: tunnel lines present, skip"
     else
       sudo sed -i \
         -e '/^    listen \[::\]:80;$/a\    listen 127.0.0.1:10080 proxy_protocol;\n    include snippets/relay-tunnel-realip.conf;' \
         -e '/^    listen \[::\]:443 ssl http2;$/a\    listen 127.0.0.1:10443 ssl http2 proxy_protocol;\n    include snippets/relay-tunnel-realip.conf;' \
         "$p"
       echo "$f: edited"
     fi
   done
   for f in inotex elecomp inotexpwa; do
     p=/etc/nginx/sites-available/$f.padyar.com.conf
     printf '%s: include=%s listen=%s\n' $f "$(sudo grep -c 'include snippets/relay-tunnel-realip.conf;' "$p")" "$(sudo grep -c -E 'listen 127\.0\.0\.1:(10080|10443) ' "$p")"
   done
   ```

3. Check: every file must show `include=2 listen=2`. Any other number means stop and do not
   reload. Fewer than 2 means one or both listen lines are not in the expected form
   (`    listen [::]:80;` and `    listen [::]:443 ssl http2;`, 4-space indent): restore that
   file (step 5) and add the 4 lines by hand after those two listens (see the lines above).
   More than 2 means the lines were added twice: restore that file (step 5).

4. Test, then reload (never restart):

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

   Then run the LAN loop above and the "Check the Tunnel" commands.

5. Rollback of this procedure (same terminal, so `B` is still set):

   ```bash
   for f in inotex elecomp inotexpwa; do sudo tar -xzf "$B" -C / etc/nginx/sites-available/$f.padyar.com.conf; done
   sudo nginx -t && sudo systemctl reload nginx
   ```

Why it is safe for the other sites and for the LAN:

- avatar and live are not touched and have no tunnel listen. The default server refuses them
  on the tunnel sockets.
- The existing `*:80`, `*:443`, `[::]:80` and `[::]:443` listeners and their default server
  (`conf.d/00-default-server.conf`) do not change.
- The realip lines replace the http-level settings (`conf.d/cloudflare-realip.conf`) only in
  the server blocks that include the snippet. They trust only `127.0.0.1` and read only the
  PROXY header. LAN clients keep their own address. A client header such as
  `CF-Connecting-IP` cannot set the logged IP for these sites.
- One change of behaviour: for the 3 published sites, a request from a Cloudflare range no
  longer has its IP taken from `CF-Connecting-IP`. The Cloudflare proxy and cloudflared are
  gone, so nothing uses that path. If the orange cloud is ever turned on again for these
  names, this must be revisited.
- The listen options on all 10443 lines are the same (`ssl http2 proxy_protocol`), and on all
  10080 lines (`proxy_protocol`). Different options on one address would make nginx warn
  "protocol options redefined".

### Exact contents of the new files

These are the live files, read back on 2026-10-29. All are `root:root 0644`.

`/etc/nginx/snippets/relay-tunnel-realip.conf`:

```nginx
# HOM-332 relay tunnel (frp). Include only in server blocks that listen on
# 127.0.0.1:10080 or 127.0.0.1:10443 with proxy_protocol.
# Take the client IP from the PROXY protocol header that frpc adds. These two
# lines replace the http-level realip settings for this server block, so a
# client header such as CF-Connecting-IP cannot set the logged IP.
# A connection without a PROXY header (LAN, local tools) keeps its own address.
set_real_ip_from 127.0.0.1;
real_ip_header proxy_protocol;
```

`/etc/nginx/conf.d/05-relay-tunnel-default.conf`:

```nginx
# HOM-332 relay tunnel: catch-all for the tunnel listeners.
# frpc on this host sends relay traffic to 127.0.0.1:10080 and 127.0.0.1:10443
# with a PROXY protocol v2 header. Only inotex, elecomp and inotexpwa have a
# tunnel listen. Every other name (avatar, live, chat, a random name, no SNI)
# lands here and gets no site content and no site certificate.
# The listen options must match the site files exactly, so nginx does not warn
# "protocol options redefined".
server {
    listen 127.0.0.1:10080 proxy_protocol default_server;
    server_name _;
    include snippets/relay-tunnel-realip.conf;
    access_log /var/log/nginx/relay-tunnel-default.access.log;
    return 444;
}

server {
    listen 127.0.0.1:10443 ssl http2 proxy_protocol default_server;
    server_name _;
    include snippets/relay-tunnel-realip.conf;
    access_log /var/log/nginx/relay-tunnel-default.access.log;
    ssl_protocols TLSv1.2 TLSv1.3;
    # Unknown or missing SNI: refuse the TLS handshake. No certificate needed.
    ssl_reject_handshake on;
    # Known SNI but another Host header: close without an answer.
    return 444;
}
```

`/etc/fail2ban/jail.d/relay-tunnel.conf`:

```ini
# HOM-332 relay tunnel. frpc on this host delivers internet traffic to nginx
# from 127.0.0.1. Never ban the loopback address, or the whole tunnel stops.
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1
```

Order used on 2026-10-29: backup, then the fail2ban file and `fail2ban-client reload` (so
`127.0.0.1` could not be banned before any tunnel request arrived), then the 2 nginx files and
the 4 lines per site, then `nginx -t` and one `systemctl reload nginx`. That one reload turned on
the tunnel listens and the real IP lines together, so nginx never took a tunnel request without
the real client IP. The undo steps are in "Rollback" below.

## Check the Tunnel

From any machine on the internet:

```bash
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://inotex.padyar.com/
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://elecomp.padyar.com/
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://inotexpwa.padyar.com/
nc -z -G 5 193.93.169.205 7000     # macOS; on Linux use: nc -z -w 5 193.93.169.205 7000
```

`verify=0` means the certificate is valid. The status must match what the LAN gives.

On the relay:

```bash
systemctl is-active frps
sudo ss -ltnp | grep frps                          # 7000, 80 and 443
sudo journalctl -u frps -n 50 --no-pager -o cat    # "client login info" when frpc connects
sudo ufw status verbose
```

On the local server (as `gpu`, no sudo):

```bash
docker ps --filter name=relay-tunnel-frpc --format '{{.Names}} {{.Status}}'
docker logs --tail 30 relay-tunnel-frpc            # "login to server success", "start proxy success"
ss -ltn | grep -E '127\.0\.0\.1:(10080|10443) '    # nginx tunnel listeners
tail -n 5 /var/log/nginx/inotex.access.log         # tunnel requests show the visitor IP, not 127.0.0.1
```

Never use a bare `docker inspect` (it prints the environment). Use `--format` with the field
you need, for example
`docker inspect --format '{{.HostConfig.RestartPolicy.Name}} {{.HostConfig.NetworkMode}}' relay-tunnel-frpc`.

## Restart

| Side | Command | Effect |
| --- | --- | --- |
| Relay | `sudo systemctl restart frps` | All tunnel connections drop. frpc logs in again by itself. |
| Local | `docker restart relay-tunnel-frpc` (as `gpu`) | The same, from the other side. |
| Stop the tunnel (emergency) | `docker stop relay-tunnel-frpc` | The sites are no longer public. LAN access stays. With `unless-stopped` the container stays stopped after a reboot, until `docker start relay-tunnel-frpc`. |
| Stop the relay side | `sudo systemctl stop frps` | The same effect from the relay. `systemctl start frps` brings it back. |

Measured on 2026-10-29, from the command until `https://inotex.padyar.com/` answered 200
through the relay again. No manual step was needed in any case.

| Event | Sites back after | Detail |
| --- | --- | --- |
| `sudo systemctl restart frps` | 5.4 s | The restart itself takes about 2 s. frpc got one "connection refused", retried, and logged in 2.5 s later. |
| `docker restart relay-tunnel-frpc` | 1.9 s | frpc logged in 0.3 s after the container started. |
| `sudo reboot` of the relay | 47.9 s | Kernel up after 9 s, frps listening after 31 s, frpc logged in after 46.5 s. The frpc container did not restart: frpc kept retrying, and its retry wait grows while the relay is down. |

frpc has `loginFailExit = false`, so it keeps retrying while the relay is down. Docker restarts
the container if the process exits.

## Token Rotation

The token is a random 64-character hex string. Rotate it when someone who should not have it
may have seen it, and when a person with access leaves.

Never print the token. No `cat`, `head`, `xxd`, `od`, `base64` or editor on the token files,
except inside the pipes below, where the output goes only into the other command.

1. On the relay, write a new token in place (the file keeps its owner and mode):

   ```bash
   ssh homayar-relay "sudo bash -c 'umask 077; openssl rand -hex 32 > /etc/frp/token'"
   ssh homayar-relay 'sudo stat -c "%a %U:%G %s" /etc/frp/token'     # expect: 600 frps:frps 65
   ```

2. From the Mac, copy it to the local server in place, through a pipe only:

   ```bash
   set -o pipefail
   ssh homayar-relay 'sudo -n cat /etc/frp/token' | ssh homayar-local 'umask 077 && cat > ~/relay-tunnel/secrets/token && test -s ~/relay-tunnel/secrets/token' && echo move-ok
   ssh homayar-relay 'sudo -n cat /etc/frp/token' | ssh homayar-local 'cmp -s - ~/relay-tunnel/secrets/token && echo token-match || echo token-differs'
   ```

3. Restart both sides, so both read the new token:

   ```bash
   ssh homayar-relay 'sudo systemctl restart frps'
   ssh homayar-local 'docker restart relay-tunnel-frpc'
   ssh homayar-local 'docker logs --since 2m relay-tunnel-frpc 2>&1 | grep -E "login to server success|start proxy success"'
   ```

The sites are down for a few seconds between step 3's two commands.

## Server Certificate Renewal (frps)

The frps certificate expires on 2029-10-28. Renew it a month before. frpc trusts the CA, not
this certificate, so the local server needs no change.

```bash
ssh homayar-relay 'bash -se' <<'BLOCK'
sudo bash -c 'set -e; umask 077; cd /root/frp-ca
openssl req -new -config ca.cnf -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -noenc -keyout server.key -out server.csr -subj "/CN=relay-tunnel.internal"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAserial ca.srl -days 1095 -sha256 -extfile ca.cnf -extensions v3_server -out server.crt'
sudo install -o root -g frps -m 0644 /root/frp-ca/server.crt /etc/frp/tls/server.crt
sudo install -o frps -g frps -m 0600 /root/frp-ca/server.key /etc/frp/tls/server.key
sudo rm /root/frp-ca/server.key /root/frp-ca/server.csr
sudo openssl verify -CAfile /etc/frp/tls/ca.crt /etc/frp/tls/server.crt
sudo systemctl restart frps
BLOCK
```

The CA itself expires on 2036-10-26. A new CA needs the new `ca.crt` on the local server too
(copy it like in "Rebuild or Recreate the Container", then `docker restart relay-tunnel-frpc`).

## Site Certificates (Let's Encrypt)

The site certificates are separate from the tunnel CA. They stay on the local server. The
relay never sees them.

| Site | Expires (checked 2026-10-29 through the relay) |
| --- | --- |
| `inotex.padyar.com` | 2026-11-18 16:19 UTC |
| `elecomp.padyar.com` | 2026-11-18 16:20 UTC |
| `inotexpwa.padyar.com` | 2026-11-29 12:16 UTC |

How they renew:

- `certbot.timer` runs `certbot renew` twice a day on the local server.
- All 5 `padyar.com` certificates use `authenticator = dns-cloudflare`
  (`/etc/letsencrypt/renewal/<site>.conf`). Renewal proves control through a DNS TXT record
  at Cloudflare. It does not use port 80 and does not need the tunnel.
- So renewal needs the Cloudflare DNS API token that certbot uses. Keep that token when
  Cloudflare settings are cleaned up.
- After a renewal, the deploy hook `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`
  reloads nginx.
- The default window is 30 days before expiry (`renew_before_expiry` is not set).
- The port 80 ACME path (`/.well-known/acme-challenge/`, webroot `/var/www/certbot`) works
  through the tunnel too (it answers 404 for an unknown token). So HTTP-01 is a fallback if
  the DNS token is ever lost.

Test a renewal without changing anything (owner, `sudo`), one site at a time:

```bash
sudo certbot renew --dry-run --cert-name inotex.padyar.com
sudo certbot renew --dry-run --cert-name elecomp.padyar.com
sudo certbot renew --dry-run --cert-name inotexpwa.padyar.com
```

Each must print `The dry run was successful.`

Check the last automatic run, without `sudo`:
`systemctl show certbot.service -p ExecMainStatus -p ExecMainExitTimestamp`. `ExecMainStatus=0`
means every due renewal worked. On 2026-10-29 the 02:48 UTC run ended with
`ExecMainStatus=1`, while inotex and elecomp were already inside their 30-day window. Read
`/var/log/letsencrypt/letsencrypt.log` (root only) for the reason. This was before the tunnel
work and does not depend on it.

## Rebuild or Recreate the Container

Copy the CA certificate (public) from the relay:

```bash
set -o pipefail
ssh homayar-relay 'sudo -n cat /etc/frp/tls/ca.crt' | ssh homayar-local 'cat > ~/relay-tunnel/tls/ca.crt && test -s ~/relay-tunnel/tls/ca.crt && chmod 644 ~/relay-tunnel/tls/ca.crt'
```

Build and run (on the local server, as `gpu`):

```bash
docker build -t relay-tunnel-frpc:0.71.1 ~/relay-tunnel/build
docker rm -f relay-tunnel-frpc
docker run -d --name relay-tunnel-frpc \
  --restart unless-stopped --network host \
  --user 1000:1000 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --log-opt max-size=10m --log-opt max-file=3 \
  --mount type=bind,src=/home/gpu/relay-tunnel/frpc.toml,dst=/etc/frp/frpc.toml,readonly \
  --mount type=bind,src=/home/gpu/relay-tunnel/tls/ca.crt,dst=/etc/frp/ca.crt,readonly \
  --mount type=bind,src=/home/gpu/relay-tunnel/secrets/token,dst=/etc/frp/token,readonly \
  relay-tunnel-frpc:0.71.1
```

`buildx` is not installed on the local server, so `docker build` uses the legacy builder.
That is fine for this Dockerfile. Do not use `COPY --chmod` there.

## Upgrade frp

1. Download the new release and its `frp_sha256_checksums.txt` from
   `github.com/fatedier/frp/releases`. Check the SHA-256 with `sha256sum -c`. Never pipe a
   download into a shell.
2. Read the release notes for config changes.
3. Relay: `sudo install -o root -g root -m 0755 <new frps> /usr/local/bin/frps`, then
   `sudo -u frps /usr/local/bin/frps verify -c /etc/frp/frps.toml`, then
   `sudo systemctl restart frps`.
4. Local: put the new `frpc` in `~/relay-tunnel/build/`, build with a new tag, and recreate
   the container with that tag (change the tag in the `docker run` line and in this runbook).

## Rollback

Local server, as `gpu`:

```bash
docker rm -f relay-tunnel-frpc
docker rmi relay-tunnel-frpc:0.71.1
rm -rf ~/relay-tunnel
```

Local server, owner with `sudo` (nginx and fail2ban back to the state before HOM-332):

```bash
sudo rm -f /etc/nginx/conf.d/05-relay-tunnel-default.conf /etc/nginx/snippets/relay-tunnel-realip.conf
for f in inotex elecomp inotexpwa; do sudo tar -xzf /root/hom332-backup/nginx-fail2ban-before.tgz -C / etc/nginx/sites-available/$f.padyar.com.conf; done
sudo nginx -t && sudo systemctl reload nginx
sudo rm /etc/fail2ban/jail.d/relay-tunnel.conf && sudo fail2ban-client reload
```

Relay:

```bash
sudo systemctl disable --now frps
sudo ufw --force reset
sudo rm -rf /etc/frp /root/frp-ca /etc/systemd/system/frps.service /usr/local/bin/frps
sudo systemctl daemon-reload
sudo userdel frps
```

`ufw --force reset` turns ufw off and removes the added rules. `ufw disable` alone would keep
the rules, and a later `ufw enable` would bring them back.

## Adding app.homayar.com Later (HOM-328)

DNS for `app.homayar.com` already points at the relay. The tunnel forwards every name, so the
relay and frpc need no change. Only nginx on the local server changes (an owner `sudo` step):

1. Issue the certificate on the local server with `certbot` and `dns-cloudflare`, like the
   other sites. It does not need the tunnel.
2. Add a site file for `app.homayar.com` with the same tunnel lines as the 3 sites above:
   `listen 127.0.0.1:10080 proxy_protocol;` and `listen 127.0.0.1:10443 ssl http2 proxy_protocol;`,
   each with `include snippets/relay-tunnel-realip.conf;`. Add LAN listens only if the LAN
   needs it.
3. Proxy to the Homayar stack on `127.0.0.1:8080` (reserved for it). Set `X-Forwarded-For` and
   `X-Real-IP` to `$remote_addr` (overwrite, do not append), like the Padyar sites, and make
   the stack trust only `127.0.0.1` for those headers.
4. `sudo nginx -t`, then `sudo systemctl reload nginx`. Never restart.
5. Until then, the tunnel default server refuses `app.homayar.com`.

## Known Limits

- **fail2ban bans do not block tunnel clients.** fail2ban bans with nftables on the local
  server, but tunnel clients reach nginx from `127.0.0.1`, which is in `ignoreip`. A banned IP
  can still come in through the relay. Follow-up: ban on the relay (for example, fail2ban on
  the relay fed by the local logs, or an frps plugin).
- **The nginx fail2ban jails may not read the nginx logs at all.**
  `/etc/fail2ban/jail.d/defaults-debian.conf` sets `[DEFAULT] backend = systemd`. That
  setting may also apply to `nginx-http-auth` and `nginx-limit-req`, but nginx writes to log
  files, not to the journal. This is older than HOM-332 and is not verified. Follow-up ticket;
  do not change fail2ban for the tunnel.
- **A reboot of the local server is not tested.** Docker is enabled at boot and the container
  is `unless-stopped`, so it should come back. Check at the next reboot the owner plans:
  `docker ps --filter name=relay-tunnel-frpc` and the "Check the Tunnel" commands.
- **Port 7000 is open to the internet.** It is protected by forced TLS, the token and the token
  check on heartbeats and new work connections. A source-IP rule is not used, because the
  local server's egress IP changes.
- **IPv6.** The relay has no public IPv6 address and the names have no AAAA record. If an AAAA
  record is ever added, IPv6 clients would bypass the relay.

## Troubleshooting

| Symptom | Where to look | Likely cause and fix |
| --- | --- | --- |
| Sites time out from the internet | `nc -z -G 5 193.93.169.205 443` | Relay down, ufw changed, or the provider security group. Check "On the relay". |
| frpc log: `login to the server failed: register control error` | `docker logs relay-tunnel-frpc` | Token differs between the hosts. Run the `cmp` line in "Token Rotation", step 2. frps hides the reason on purpose (`detailedErrorsToClient = false`). |
| frpc log: `x509: certificate ...` | `docker logs relay-tunnel-frpc` | Wrong or old `tls/ca.crt`, or the frps certificate expired. Compare fingerprints, or renew. |
| frpc log: `dial tcp 193.93.169.205:7000: i/o timeout` | Mac or local server: `nc` to 7000 | Relay down, or ufw or the security group blocks 7000. |
| frpc log: `connect to local service [127.0.0.1:10443] error` | `ss -ltn` on the local server | nginx has no tunnel listener: the owner change is not live or was rolled back. |
| Relay listens on 7000 but not on 80 or 443 | `docker logs relay-tunnel-frpc` | frpc is not logged in. frps opens 80 and 443 only for a logged-in frpc. |
| A name gets a TLS alert or no answer through the relay | `/var/log/nginx/relay-tunnel-default.access.log` | Expected for any name without a tunnel listen (avatar, live, chat, random names). |
| Access log shows `127.0.0.1` for tunnel requests | the site file | The `include snippets/relay-tunnel-realip.conf;` line is missing in that server block. |
| frps does not start | `sudo journalctl -u frps -n 50 --no-pager -o cat` | Run `sudo -u frps /usr/local/bin/frps verify -c /etc/frp/frps.toml`. Check modes in "Files". |
| `nginx -t` warns "protocol options redefined" for 10080 or 10443 | `sudo nginx -t` | A tunnel listen line has different options from the others. Make them all the same. |
| One site is not reachable through the relay, the others are | `grep -c relay-tunnel /etc/nginx/sites-available/<site>.padyar.com.conf` | The site file lost its tunnel lines (0 instead of 2). Follow "Re-apply the Tunnel Lines". |
| A site certificate is close to expiry | `systemctl show certbot.service -p ExecMainStatus` | Renewal fails. See "Site Certificates". The usual cause is the Cloudflare DNS API token. |
