FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/ /usr/local/

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/pi \
    CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS=1 \
    DISPLAY=:99 \
    GIT_TERMINAL_PROMPT=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        chromium \
        curl \
        findutils \
        git \
        openssh-client \
        pkg-config \
        procps \
        ripgrep \
        rsync \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv \
    && npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# chrome-devtools-mcp defaults to the stable Chrome channel. Point the usual
# stable-Chrome path at Debian Chromium. Chromium's own setuid sandbox cannot
# be used together with Docker's no-new-privileges boundary, so the wrapper
# disables the inner browser sandbox; the outer container remains the security
# boundary for autonomous browser work.
RUN printf '%s\n' \
      '#!/bin/sh' \
      'exec /usr/bin/chromium --no-sandbox "$@"' \
      > /usr/local/bin/google-chrome \
    && chmod 0755 /usr/local/bin/google-chrome \
    && ln -s /usr/local/bin/google-chrome /usr/local/bin/google-chrome-stable \
    && mkdir -p /opt/google/chrome \
    && ln -s /usr/local/bin/google-chrome /opt/google/chrome/chrome

RUN useradd --create-home --uid 1000 --shell /bin/bash pi \
    && mkdir -p \
        /workspace \
        /home/pi/.pi/agent \
        /home/pi/.agents \
        /home/pi/.cache/uv \
        /home/pi/.npm \
    && chown -R pi:pi /workspace /home/pi \
    && git config --system --add safe.directory /workspace

COPY docker/pi-safe/entrypoint.sh /usr/local/bin/pi-safe-entrypoint
RUN chmod 0755 /usr/local/bin/pi-safe-entrypoint

USER pi
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/pi-safe-entrypoint"]
