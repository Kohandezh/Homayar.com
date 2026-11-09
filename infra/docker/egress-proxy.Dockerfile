# Base image pinned by digest (HOM-228): the multi-platform index of alpine:3.22
# (Alpine 3.22.7) resolved on 2026-10-23 with
# `docker buildx imagetools inspect alpine:3.22`. Re-pin on purpose,
# never by overriding ALPINE_IMAGE for a release (docs/operations/release.md).
ARG ALPINE_IMAGE=alpine:3.22@sha256:d406446bc05b82480756ce8f1287964d28768c23955fa9480d2a74a2eae7a654

# Egress proxy for AI and other approved outbound HTTP(S) (WP08-T06, ADR 0031).
# hadolint ignore=DL3006
FROM ${ALPINE_IMAGE}
ARG SQUID_PACKAGE=squid
# The pinned base does not pin the package: apk installs the squid of the Alpine
# 3.22 repository on the day of the build (6.12-r0 on 2026-10-23). Set
# SQUID_PACKAGE (e.g. squid=6.12-r0) to pin its version for a release.
# HOM-379, CVE-2026-4738: the base ships zlib 1.3.2-r1 and the newest base of
# 2026-11-09 still did, so upgrade it from the Alpine repository and fail the
# build when zlib is missing or below 1.4-r0, the fixed version.
# hadolint ignore=DL3018
RUN apk add --no-cache "${SQUID_PACKAGE}" \
    && apk add --no-cache --upgrade zlib \
    && zlib_line="$(apk list --installed zlib 2>/dev/null)" \
    && zlib_version="${zlib_line%% *}" \
    && zlib_version="${zlib_version#zlib-}" \
    && case "${zlib_version}" in \
         [0-9]*) ;; \
         *) echo "zlib is not installed; its version floor is 1.4-r0" >&2; exit 1 ;; \
       esac \
    && case "$(apk version -t "${zlib_version}" 1.4-r0)" in \
         '<') echo "zlib ${zlib_version} is not at or above its floor 1.4-r0" >&2; exit 1 ;; \
         '='|'>') echo "zlib ${zlib_version} meets its floor 1.4-r0" ;; \
         *) echo "apk cannot compare zlib ${zlib_version} with 1.4-r0" >&2; exit 1 ;; \
       esac
COPY infra/egress/entrypoint.sh /usr/local/bin/homayar-egress
USER squid
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/homayar-egress"]
