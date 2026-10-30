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
| Relay | 80/tcp | frps (proxy `http-80`) | HTTP. The sites answer 301 to HTTPS. The ACME path stays reachable: the 3 sites renew their certificates through it (HTTP-01). |
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
| `build/frpc`, `build/Dockerfile` | 0755, 0664 | image source (`FROM scratch`, the static frpc binary only) |
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

4. Test, then reload (never restart). A reload keeps open connections, but a wrong config can
   interrupt all 5 sites. Tell the owner first.

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
| Stop the relay side | `sudo systemctl stop frps` | The same effect from the relay. `sudo systemctl start frps` brings it back. |

Every command in this table interrupts the public sites: a frps restart for about 5 seconds, a
container restart for about 2 seconds, a stop until the matching start. Tell the owner first.

Measured on 2026-10-29, from the command until `https://inotex.padyar.com/` answered 200
through the relay again. No manual step was needed in any case.

| Event | Sites back after | Detail |
| --- | --- | --- |
| `sudo systemctl restart frps` | 5.4 s | The restart itself takes about 2 s. frpc got one "connection refused", retried, and logged in 2.5 s later. |
| `docker restart relay-tunnel-frpc` | 1.9 s | frpc logged in 0.3 s after the container started. |
| `sudo reboot` of the relay | 47.9 s | Kernel up after 9 s, frps listening after 31 s, frpc logged in after 46.5 s. The frpc container did not restart: frpc kept retrying, and its retry wait grows while the relay is down. |

A relay reboot interrupts the public sites for about 48 seconds. Tell the owner first.

frpc has `loginFailExit = false`, so it keeps retrying while the relay is down. Docker restarts
the container if the process exits.

## Token Rotation

The token is a random 64-character hex string. Rotate it when someone who should not have it
may have seen it, and when a person with access leaves.

Never print the token. No `cat`, `head`, `xxd`, `od`, `base64` or editor on the token files,
except inside the pipes below, where the output goes only into the other command.

Do steps 1 to 3 in one sitting. Step 3 interrupts the public sites for about 10 seconds (a frps
restart, then a container restart). Tell the owner first.

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

   Run step 3 only if step 2 printed both `move-ok` and `token-match`. Otherwise repeat step 2.

   Keep the local write in place (`cat >` into the existing file). Do not write a temp file and
   `mv` it: that gives the file a new inode, and the running container keeps seeing the old
   token through its bind mount.

3. Restart both sides, so both read the new token:

   ```bash
   ssh homayar-relay 'sudo systemctl restart frps'
   ssh homayar-local 'docker restart relay-tunnel-frpc'
   ssh homayar-local 'docker logs --since 2m relay-tunnel-frpc 2>&1 | grep -E "login to server success|start proxy success"'
   ```

The sites are down from the frps restart until the container has logged in again with the new
token. Between the two commands, frpc retries with the old token and is refused.

## Server Certificate Renewal (frps)

The frps certificate expires on 2029-10-28. Renew it a month before. frpc trusts the CA, not
this certificate, so the local server needs no change.

The last command restarts frps. That interrupts the public sites for about 5 seconds. Tell the
owner first.

```bash
ssh homayar-relay 'bash -se' <<'BLOCK'
sudo bash -c 'set -e; umask 077; cd /root/frp-ca
openssl req -new -config ca.cnf -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -noenc -keyout server.key -out server.csr -subj "/CN=relay-tunnel.internal"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAserial ca.srl -days 1095 -sha256 -extfile ca.cnf -extensions v3_server -out server.crt'
sudo openssl verify -CAfile /root/frp-ca/ca.crt /root/frp-ca/server.crt
sudo install -o root -g frps -m 0644 /root/frp-ca/server.crt /etc/frp/tls/server.crt
sudo install -o frps -g frps -m 0600 /root/frp-ca/server.key /etc/frp/tls/server.key
sudo rm /root/frp-ca/server.key /root/frp-ca/server.csr
sudo systemctl restart frps
BLOCK
```

The `openssl verify` line runs before the two `install` lines. If it fails, `bash -se` stops
the block, so a bad certificate never reaches `/etc/frp/tls/` and frps keeps the old one.

The CA itself expires on 2036-10-26. A new CA needs the new `ca.crt` on the local server too
(copy it like in "Rebuild or Recreate the Container", then `docker restart relay-tunnel-frpc`).
That restart interrupts the public sites for about 2 seconds. Tell the owner first.

## Site Certificates (Let's Encrypt)

The site certificates are separate from the tunnel CA. They stay on the local server. The
relay never sees them.

| Site | Renews with | Expires |
| --- | --- | --- |
| `inotex.padyar.com` | HTTP-01, webroot | 2027-01-28 10:33 UTC (renewed 2026-10-30) |
| `elecomp.padyar.com` | HTTP-01, webroot | 2027-01-28 10:33 UTC (renewed 2026-10-30) |
| `inotexpwa.padyar.com` | HTTP-01, webroot | 2026-11-29 12:16 UTC (due from 2026-10-30 12:16 UTC; the timer renews it) |
| `avatar.padyar.com` | `dns-cloudflare`, token invalid | 2026-12-01 00:07 UTC |
| `live.padyar.com` | `dns-cloudflare`, token invalid | 2026-12-01 00:50 UTC |

The 3 published sites were switched to webroot on 2026-10-30 at 11:31 UTC (HOM-335). The same
owner run renewed inotex and elecomp and reloaded nginx once, at 11:32 UTC.

How they renew:

- `certbot.timer` runs `certbot -q renew --no-random-sleep-on-renew` twice a day on the local
  server. The window is 30 days before expiry (`renew_before_expiry` is not set).
- The 3 published sites use `authenticator = webroot` with `/var/www/certbot`
  (`/etc/letsencrypt/renewal/<site>.conf`, with a `[[webroot_map]]` entry). This is HTTP-01:
  Let's Encrypt asks for `http://<site>/.well-known/acme-challenge/<token>`. That request goes
  through the relay (port 80) and the tunnel to nginx, and nginx serves the file from
  `/var/www/certbot`. So renewal needs the relay, frpc and the ACME location in the port 80
  server of each site. It needs no Cloudflare token.
- `avatar.padyar.com` and `live.padyar.com` still use `dns-cloudflare` with the invalid token.
  They are not published through the relay, so HTTP-01 does not work for them. Their renewal
  fails on every run from 2026-11-01. The owner decides what happens to them.
- After a real renewal, the deploy hook `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`
  reloads nginx.

The cause (HOM-335): from 2026-10-19 20:57 UTC, every timer run failed for inotex and elecomp
with `Error determining zone_id: 9109 Invalid access token. Please confirm that you have supplied valid Cloudflare API credentials.`
Cloudflare rejected the token in `/root/.secrets/cloudflare.ini`. That run was the first one
inside their 30-day window. After about 10 failed calls in a short time, Cloudflare answers
`10502 Too many authentication failures` instead. That is the same cause. The owner script then switched the 3 sites with
`certbot reconfigure`, which tests the new settings on the staging server and saves them only
if the test works.

Check without `sudo` (user `gpu` is in group `adm`, so it can read the journal):

```bash
journalctl -u certbot.service --since "3 days ago" --no-pager | grep 'Failed to renew'
systemctl show certbot.service -p ExecMainStatus -p ExecMainExitTimestamp
echo | openssl s_client -connect 192.168.100.6:443 -servername inotex.padyar.com 2>/dev/null | openssl x509 -noout -enddate
```

No `Failed to renew` line may name inotex, elecomp or inotexpwa. With `-q`, a good renewal
writes no certbot line, so check the expiry date too. From 2026-11-01, the avatar and live
failures make `ExecMainStatus=1` even when the 3 sites renew fine. Read the journal, not only
the status.

Test a renewal without changing anything (owner, `sudo`), one site at a time:

```bash
sudo certbot renew --dry-run --cert-name inotex.padyar.com --no-random-sleep-on-renew --no-directory-hooks
sudo certbot renew --dry-run --cert-name elecomp.padyar.com --no-random-sleep-on-renew --no-directory-hooks
sudo certbot renew --dry-run --cert-name inotexpwa.padyar.com --no-random-sleep-on-renew --no-directory-hooks
```

Each must end with `Congratulations, all simulated renewals succeeded`. A dry run uses the
staging server, changes no certificate and does not reload nginx. `--no-directory-hooks` skips
the pre and post hooks in `renewal-hooks/`, because certbot runs them even in a dry run.

Backup and undo: the owner script saved `/root/hom335-backup/letsencrypt-before.tgz` (root,
0600) with `renewal/`, `renewal-hooks/` and `cli.ini` from before the switch. It holds no
private key. To undo without the script (owner):

```bash
sudo tar -xzf /root/hom335-backup/letsencrypt-before.tgz -C / \
  etc/letsencrypt/renewal/inotex.padyar.com.conf \
  etc/letsencrypt/renewal/elecomp.padyar.com.conf \
  etc/letsencrypt/renewal/inotexpwa.padyar.com.conf
```

The certificates stay as they are, because a renewal conf points to the `live/` links, not to a
certificate version. No nginx reload is needed. After an undo, the 3 sites use `dns-cloudflare`
again, so their renewal fails again until the token is fixed.

If HTTP-01 ever stops working (no relay, or the ACME path is blocked), go back to DNS-01:

1. In Cloudflare, create a new API token with only Zone, DNS, Edit, for the zone `padyar.com`.
2. The owner writes it to `/root/.secrets/cloudflare.ini` as `dns_cloudflare_api_token = ...`,
   owner root, mode 0600. Never put the token in a ticket, a chat or this file.
3. Switch each site back, one at a time. certbot tests it on staging before it saves it:

```bash
sudo certbot reconfigure --cert-name inotex.padyar.com --dns-cloudflare \
  --dns-cloudflare-credentials /root/.secrets/cloudflare.ini --dns-cloudflare-propagation-seconds 30
```

A valid token also fixes avatar and live, which still read that file.

## Rebuild or Recreate the Container

Copy the CA certificate (public) from the relay:

```bash
set -o pipefail
ssh homayar-relay 'sudo -n cat /etc/frp/tls/ca.crt' | ssh homayar-local 'cat > ~/relay-tunnel/tls/ca.crt && test -s ~/relay-tunnel/tls/ca.crt && chmod 644 ~/relay-tunnel/tls/ca.crt'
```

Build and run (on the local server, as `gpu`). `docker rm -f` stops the tunnel until the new
container has logged in. That interrupts the public sites for a few seconds (a container
restart takes about 2 seconds). Tell the owner first.

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

Steps 3 and 4 interrupt the public sites: about 5 seconds for the frps restart and a few
seconds for the container recreate. Tell the owner first.

## Rollback

The full rollback takes the 3 sites off the internet for good (that is its goal), and the nginx
reload in it can interrupt the sites if something is wrong. Tell the owner first.

Local server, as `gpu`:

```bash
docker rm -f relay-tunnel-frpc
docker rmi relay-tunnel-frpc:0.71.1
rm -rf ~/relay-tunnel
```

Local server, owner with `sudo` (nginx and fail2ban back to the state before HOM-332).

1. Compare each site file with the backup from before HOM-332:

   ```bash
   for f in inotex elecomp inotexpwa; do echo "== $f"; sudo tar -xzOf /root/hom332-backup/nginx-fail2ban-before.tgz etc/nginx/sites-available/$f.padyar.com.conf | diff - /etc/nginx/sites-available/$f.padyar.com.conf; done
   ```

2. For each file, choose:

   - The `diff` shows exactly the 4 tunnel lines as added (`>`): the 2 `listen 127.0.0.1:...`
     lines and the 2 `include snippets/relay-tunnel-realip.conf;` lines, and nothing else.
     Then restore that file from the backup:

     ```bash
     sudo tar -xzf /root/hom332-backup/nginx-fail2ban-before.tgz -C / etc/nginx/sites-available/inotex.padyar.com.conf
     ```

     (Change `inotex` to the site name.)
   - The `diff` shows anything else: the file was changed later for another reason, and a
     restore would undo that change too. Remove only the 4 tunnel lines:

     ```bash
     sudo sed -i -E -e '/^    listen 127\.0\.0\.1:(10080|10443) /d' -e '/^    include snippets\/relay-tunnel-realip\.conf;$/d' /etc/nginx/sites-available/inotex.padyar.com.conf
     ```

     (Change `inotex` to the site name.) Then run step 1 again for that file: only the later
     change may remain.

3. Remove the 2 new nginx files, test, reload, and remove the fail2ban file:

   ```bash
   sudo rm -f /etc/nginx/conf.d/05-relay-tunnel-default.conf /etc/nginx/snippets/relay-tunnel-realip.conf
   sudo nginx -t && sudo systemctl reload nginx
   sudo rm /etc/fail2ban/jail.d/relay-tunnel.conf && sudo fail2ban-client reload
   ```

Do step 3 only after every site file has no `include snippets/relay-tunnel-realip.conf;` line
left. Otherwise `nginx -t` fails, because the snippet is gone.

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

1. Issue the certificate on the local server with HTTP-01 through the tunnel, like the 3 sites:
   first a port 80 server with the tunnel listen and the ACME location (webroot
   `/var/www/certbot`), then `certbot certonly --webroot -w /var/www/certbot -d app.homayar.com`.
   Do not use `dns-cloudflare` (see "Site Certificates").
2. Add a site file for `app.homayar.com` with the same tunnel lines as the 3 sites above:
   `listen 127.0.0.1:10080 proxy_protocol;` and `listen 127.0.0.1:10443 ssl http2 proxy_protocol;`,
   each with `include snippets/relay-tunnel-realip.conf;`. Add LAN listens only if the LAN
   needs it.
3. Proxy to the Homayar stack on `127.0.0.1:8080` (reserved for it). Set `X-Forwarded-For` and
   `X-Real-IP` to `$remote_addr` (overwrite, do not append), like the Padyar sites, and make
   the stack trust only `127.0.0.1` for those headers.
4. `sudo nginx -t`, then `sudo systemctl reload nginx`. Never restart. A reload keeps open
   connections, but a wrong config can interrupt all 5 sites. Tell the owner first.
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
| A request through the relay stalls once, then works | `docker logs relay-tunnel-frpc` | The internet path between the local server and the relay drops packets now and then (1 of 5 pings was lost in the first survey). Retry. If it repeats often, look in the frpc log for reconnects (`try to connect to server`, `login to server success`). |
| One site is not reachable through the relay, the others are | `grep -c relay-tunnel /etc/nginx/sites-available/<site>.padyar.com.conf` | The site file lost its tunnel lines (0 instead of 2). Follow "Re-apply the Tunnel Lines". |
| A site certificate is close to expiry | `journalctl -u certbot.service --since "3 days ago" --no-pager \| grep 'Failed to renew'` | Renewal fails. See "Site Certificates (Let's Encrypt)". For the 3 webroot sites: the relay, frpc or the ACME path (`/.well-known/acme-challenge/` must answer 404 for an unknown token through the relay). For a `dns-cloudflare` certificate (avatar, live): the Cloudflare token. |
