# ---- builder: patch + build wheels for Real-ESRGAN dependencies ----
# basicsr/gfpgan/facexlib read their version via exec()+locals()['__version__'],
# which raises KeyError under affected interpreter semantics. Build patched
# wheels here so Cookbook can install Real-ESRGAN in the Python image.
FROM python:3.11-slim AS realesrgan-wheels
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY docker/build-realesrgan-wheels.sh /usr/local/bin/build-realesrgan-wheels.sh
RUN bash /usr/local/bin/build-realesrgan-wheels.sh /wheels

FROM python:3.11-slim

# System deps. tmux is required by Cookbook for background downloads/serves.
# openssh-client is required for Cookbook remote server tests, setup, probes,
# downloads, and serves from Docker installs.
# git/cmake are required when Cookbook builds llama.cpp on first llama.cpp
# launch inside Docker.
# nodejs/npm provide npx for the optional built-in Browser MCP server.
# gosu lets the entrypoint drop privileges cleanly so signals still reach
# uvicorn directly (no extra shell layer like `su`/`sudo` would add).
ARG INSTALL_OCR=false
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    libgl1 \
    libglib2.0-0t64 \
    libmagic1 \
    libxcb1 \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    $(if [ "$INSTALL_OCR" = "true" ]; then echo "poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng"; fi) \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI client only. The daemon remains on the host via the optional
# /var/run/docker.sock mount in docker-compose.yml.
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) DARCH=x86_64 ;; \
         arm64) DARCH=aarch64 ;; \
         *) echo "unsupported arch $ARCH"; exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /app

ARG ODYSSEUS_RELEASE_REVISION=""
ENV ODYSSEUS_RELEASE_REVISION=${ODYSSEUS_RELEASE_REVISION} \
    HOME=/app \
    npm_config_cache=/app/.cache/npm \
    NPM_CONFIG_CACHE=/app/.cache/npm

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
# Office extraction is its own switch so Nextcloud/document import can enable
# MarkItDown without enabling unrelated optional features.
ARG INSTALL_OPTIONAL=false
ARG INSTALL_OFFICE=false
ARG INSTALL_STT=false
COPY requirements.txt requirements-optional.txt requirements-office.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi \
    && if [ "$INSTALL_OFFICE" = "true" ] && [ "$INSTALL_OPTIONAL" != "true" ]; then pip install --no-cache-dir -r requirements-office.txt; fi \
    && if [ "$INSTALL_STT" = "true" ] && [ "$INSTALL_OPTIONAL" != "true" ]; then pip install --no-cache-dir faster-whisper; fi

COPY --from=realesrgan-wheels /wheels/ /tmp/odysseus-wheels/
RUN pip install --no-cache-dir --no-deps /tmp/odysseus-wheels/*.whl \
    && rm -rf /tmp/odysseus-wheels

# Install Node deps used by test/dev tooling and optional npx-backed helpers.
COPY package.json package-lock.json ./
RUN npm ci \
    && npx playwright install --with-deps chromium \
    && npx -y @playwright/mcp@latest --version

# Copy app code
COPY . .

# Create data directory (mount a volume here for persistence)
RUN mkdir -p data logs services/cache/search

# Entrypoint that drops to PUID/PGID (default 1000:1000) and repairs
# ownership on the bind-mounted /app/data and /app/logs. Without this,
# the container runs as root and writes root-owned files into host
# bind mounts — any later non-root run (or a host user trying to
# update them) silently fails on EPERM, breaking skill extraction,
# prefs persistence, mail attachments, etc.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000", "--workers", "1"]
