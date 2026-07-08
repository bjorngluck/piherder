# PiHerder - python:3.12-slim-bookworm per spec
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System tools needed for parity with bash scripts (rsync for backups, ssh client, ping, dns utils)
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsync \
    openssh-client \
    iputils-ping \
    dnsutils \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps first for layer caching
COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install -e .[dev]

# Copy source
COPY . .

# Vendor frontend CDNs (Tailwind Play, HTMX, Alpine).
# This step REQUIRES internet access on the build machine (or pre-vendored files
# brought in via a previous stage or volume). We deliberately fail hard so that
# broken images are never produced.
RUN mkdir -p app/static && bash scripts/vendor_cdns.sh

# Remove any obviously invalid tailwind.js (e.g. Pi-hole block pages or truncated files)
# that might have been copied or partially downloaded.
RUN if [ -f app/static/tailwind.js ]; then \
      size=$(stat -c%s app/static/tailwind.js 2>/dev/null || echo 0); \
      if [ "$size" -lt 100000 ] || grep -qiE 'pi-hole|blocked|this file is copyright' app/static/tailwind.js 2>/dev/null; then \
        echo "⚠️  Removing invalid tailwind.js (size=$size or looks like a block page)"; \
        rm -f app/static/tailwind.js; \
      fi; \
    fi

# Hard fail if the critical Tailwind Play script is missing or invalid.
# This protects open-source users who build the image themselves.
# (Pre-built images on Docker Hub will already contain the assets.)
RUN if [ ! -f app/static/tailwind.js ]; then \
      echo ""; \
      echo "ERROR: tailwind.js is missing after vendoring."; \
      echo ""; \
      echo "The build requires internet access to download frontend assets."; \
      echo "Common causes and fixes:"; \
      echo "  - Pi-hole or ad-blocker blocking cdn.tailwindcss.com"; \
      echo "    → Whitelist the domain temporarily and rebuild."; \
      echo "  - Building in an air-gapped / no-internet environment"; \
      echo "    → Download the file on a machine that has internet:"; \
      echo "      curl -L -o app/static/tailwind.js https://cdn.tailwindcss.com"; \
      echo "    → Then either:"; \
      echo "       a) Build with internet once, or"; \
      echo "       b) Use a multi-stage Dockerfile / build context that includes the file."; \
      echo ""; \
      echo "HTMX and Alpine are also vendored but are less critical."; \
      echo ""; \
      exit 1; \
    fi

# Create non-root user (optional hardening)
RUN useradd --create-home --shell /bin/bash piherder && \
    mkdir -p /backups /data/avatars && \
    chown -R piherder:piherder /app /backups /data

USER piherder

EXPOSE 8000

# Healthcheck (uses the lightweight /health endpoint).
# Helps orchestrators and compose detect when the web app is unhealthy and restart it.
HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)" || exit 1

# Default command (overridden in compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
