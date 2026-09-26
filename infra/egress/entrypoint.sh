#!/bin/sh
# Homayar egress proxy (WP08-T06 network layer, ADR 0031).
#
# Renders a deny-by-default Squid configuration from the operator allowlist
# (one "host:port" per line; "#" comments) and runs Squid in the foreground.
# Only CONNECT/HTTP to an exact listed host and port is forwarded; loopback,
# link-local (cloud metadata) and every unlisted destination is refused, and
# the cache manager is never reachable. An empty allowlist refuses everything.
set -eu

allowlist="${EGRESS_ALLOWLIST:-/etc/homayar/egress/allowlist.txt}"
conf=/tmp/squid.conf

if [ ! -r "${allowlist}" ]; then
  echo "egress: allowlist ${allowlist} is not readable" >&2
  exit 2
fi

cat > "${conf}" <<'CONF'
http_port 3128
pid_filename /tmp/squid.pid
coredump_dir /tmp
cache deny all
cache_mem 8 MB
access_log stdio:/dev/stdout squid
cache_log stdio:/dev/stderr
cache_store_log none
logfile_rotate 0
visible_hostname homayar-egress
httpd_suppress_version_string on
via off
forwarded_for delete
acl CONNECT method CONNECT
acl manager url_regex -i ^cache_object:// /squid-internal-mgr/
acl link_local dst 169.254.0.0/16 fe80::/10
http_access deny manager
http_access deny to_localhost
http_access deny link_local
CONF

count=0
line_number=0
while IFS= read -r raw || [ -n "${raw}" ]; do
  line_number=$((line_number + 1))
  entry=$(printf '%s' "${raw%%#*}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
  [ -z "${entry}" ] && continue
  host=${entry%:*}
  port=${entry##*:}
  if [ "${host}" = "${entry}" ] \
    || ! printf '%s' "${port}" | grep -Eq '^[0-9]{1,5}$' \
    || [ "${port}" -lt 1 ] || [ "${port}" -gt 65535 ] \
    || ! printf '%s' "${host}" | grep -Eq '^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$'; then
    echo "egress: ${allowlist}:${line_number}: expected host:port" >&2
    exit 2
  fi
  count=$((count + 1))
  if printf '%s' "${host}" | grep -Eq '^[0-9.]+$'; then
    echo "acl allow_host_${count} dst ${host}" >> "${conf}"
  else
    echo "acl allow_host_${count} dstdomain ${host}" >> "${conf}"
  fi
  {
    echo "acl allow_port_${count} port ${port}"
    echo "http_access allow allow_host_${count} allow_port_${count}"
  } >> "${conf}"
done < "${allowlist}"

echo "http_access deny all" >> "${conf}"
echo "egress: ${count} allowlisted destination(s)" >&2
exec squid -N -f "${conf}"
