# ADR 0055: Public HTTPS moves to relay port 8080

Date: 2026-11-05 · Status: Accepted (owner decision, HOM-374) · Related: HOM-332, HOM-328

## Context

The relay VPS `193.93.169.205` is shared. On 2026-11-03 the owner installed `arange-tun`, a
root service of the owner, which holds relay ports 80, 443, 2053, 2083, 8443, 19896 and its
control port 59675. Since then frps (`allowPorts`) allows only 8080 and 8443.

The relay rebooted on 2026-11-05 15:07:57 UTC. arange-tun bound 8443 before frps could, so
the frpc proxy `https-8443` failed with `port unavailable`. On a shared port, whoever starts
first after a reboot owns it. The public sites were down.

Without relay port 80, certbot HTTP-01 renewal is also broken for every site.

The owner weighed three options on 2026-11-05:

- (A) give relay ports 80 and 443 back to frps.
- (B) remove 8443 from arange-tun.
- (C) keep the relay as it is and use another port.

The choice was C, and then port 8080, because arange-tun had taken 8443 after the
2026-11-05 reboot and does not use 8080. arange-tun keeps 80, 443 and 8443.

## Decision

- Public HTTPS is served on relay port 8080. frps already allowed it, ufw and the provider
  security group already opened it, and arange-tun does not list it.
- frpc runs one proxy, `https-8080`: `remotePort = 8080`, local `127.0.0.1:10443`, PROXY
  protocol v2. The plain HTTP proxy (`http-8080`, local `127.0.0.1:10080`) is removed:
  relay port 80 is not ours and plain HTTP could only answer a redirect.
- The nginx tunnel listener `127.0.0.1:10080` stays on the local server, with no tunnel
  traffic reaching it.
- Public URLs carry the port: `https://app.homayar.com:8080`,
  `https://inotex.padyar.com:8080`, `https://elecomp.padyar.com:8080`,
  `https://inotexpwa.padyar.com:8080`.
- The change on the local server runs through one reviewed script with backup, check, apply
  and undo modes (`docs/operations/relay-tunnel.md`, "Move HTTPS to Relay Port 8080").
- Certificate renewal moves from HTTP-01 to DNS-01 (`dns-cloudflare`), one site at a time,
  inotexpwa first. The plan and owner steps are in `docs/operations/relay-tunnel.md`,
  "Site Certificates".

## Consequences

- Every public URL carries an explicit port. Bookmarks and links that use the bare
  `https://<site>/` form stop reaching us: relay port 443 belongs to arange-tun.
- App URL settings that include an origin must carry the port (`PUBLIC_ORIGIN`,
  `CORS_ORIGINS`). Host checks that strip the port and cookie domains need no change. The
  full audit is in `docs/operations/app-server.md`, "Public URL Settings (Relay Port 8080)".
- Renewal by DNS-01 needs a Cloudflare API token on the local server. Nobody asks for its
  value in chat or Linear. The zone of `homayar.com` (same token or a second one) is an open
  owner decision.
- The shared-port race stays: after a relay reboot, arange-tun could take a port that frps
  also allows. Port 8080 is not in arange-tun's list today. Check the tunnel after every
  relay reboot.
- A later move back to port 443, or to another front, is a new owner decision.
