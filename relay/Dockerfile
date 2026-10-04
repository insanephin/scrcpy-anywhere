FROM ubuntu:24.04

ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    python3-minimal \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$TARGETARCH" = amd64 ]; then \
        curl -fsSL -o /tmp/platform-tools.zip https://dl.google.com/android/repository/platform-tools-latest-linux.zip \
        && unzip -q /tmp/platform-tools.zip -d /opt \
        && ln -s /opt/platform-tools/adb /usr/local/bin/adb \
        && rm /tmp/platform-tools.zip; \
    else \
        apt-get update && apt-get install -y --no-install-recommends adb && rm -rf /var/lib/apt/lists/*; \
    fi

COPY relay.py /relay.py
COPY relay/entrypoint.sh /entrypoint.sh
COPY relay/healthcheck.py /healthcheck.py
RUN chmod +x /entrypoint.sh

EXPOSE 5555

HEALTHCHECK --interval=15s --timeout=10s --start-period=180s --retries=3 CMD ["python3", "/healthcheck.py"]

ENTRYPOINT ["/entrypoint.sh"]
