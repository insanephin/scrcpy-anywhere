ARG REDROID_BASE=redroid/redroid:12.0.0-240527

FROM ubuntu:24.04 AS fetch
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

ARG MTG_RELEASE=https://github.com/s1204IT/MindTheGappsBuilder/releases/download/20240619
RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) zip=MindTheGapps-12.1.0-x86_64-20240619.zip; sha=2f0d7d0f2a5608fb96f8c9c8aad1d1e8f24e14607650622395a72d3f408735f9 ;; \
        arm64) zip=MindTheGapps-12.1.0-arm64-20240619.zip;  sha=b8c28e92f8434975c58747278db677050b481dc65232fd1d79374df3806f3d9b ;; \
        *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/mtg.zip "$MTG_RELEASE/$zip"; \
    echo "$sha  /tmp/mtg.zip" | sha256sum -c -; \
    mkdir -p /out/gapps; \
    unzip -q /tmp/mtg.zip 'system/*' -d /out/gapps; \
    find /out/gapps -type d -exec chmod 755 {} +; \
    find /out/gapps -type f -exec chmod 644 {} +

FROM ${REDROID_BASE}
COPY --from=fetch /out/gapps/ /
COPY --chmod=644 scrcpy-anywhere.rc /system/etc/init/scrcpy-anywhere.rc
ENTRYPOINT ["/init", "androidboot.hardware=redroid", "ro.setupwizard.mode=DISABLED"]
