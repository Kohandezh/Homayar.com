ARG ALPINE_IMAGE=alpine:3.22

# Egress proxy for AI and other approved outbound HTTP(S) (WP08-T06, ADR 0031).
# hadolint ignore=DL3006
FROM ${ALPINE_IMAGE}
ARG SQUID_PACKAGE=squid
# The package set comes from the pinned Alpine release; pin SQUID_PACKAGE
# (e.g. squid=6.12-r0) together with a digest-pinned ALPINE_IMAGE for releases.
# hadolint ignore=DL3018
RUN apk add --no-cache "${SQUID_PACKAGE}"
COPY infra/egress/entrypoint.sh /usr/local/bin/homayar-egress
USER squid
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/homayar-egress"]
