# PiHerder - python:3.12-slim-bookworm per spec
# Dependencies are installed from the committed lockfile (reproducible RC/prod builds).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System tools needed for parity with bash scripts (rsync for backups, ssh client, ping, dns utils)
# postgresql-client-16 must match compose db (postgres:16) for full DR dumps
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsync \
    openssh-client \
    iputils-ping \
    dnsutils \
    ca-certificates \
    curl \
    gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Locked third-party deps first (layer cache when requirements.lock.txt is unchanged).
# Source of truth: uv.lock → export via scripts/refresh-lockfiles.sh
# requirements.lock.txt = runtime + [dev] (pytest) for compose / CI image parity.
COPY requirements.lock.txt ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install --require-hashes -r requirements.lock.txt

# Project source + install without re-resolving dependencies
COPY . .
RUN pip install --no-deps -e .

# Vendor HTMX / Alpine / xterm (needs internet unless already in the context).
# Tailwind is a committed compiled stylesheet — no Play / no cdn.tailwindcss.com.
RUN mkdir -p app/static && bash scripts/vendor_cdns.sh

# Hard fail if compiled Tailwind is missing (air-gapped builds still work when
# app/static/css/tailwind.css is in the git tree).
RUN if [ ! -f app/static/css/tailwind.css ]; then \
      echo ""; \
      echo "ERROR: app/static/css/tailwind.css is missing."; \
      echo "Compile it (needs Node or Docker) and commit the result:"; \
      echo "  bash scripts/build-tailwind.sh"; \
      echo ""; \
      exit 1; \
    fi \
    && test "$(stat -c%s app/static/css/tailwind.css)" -ge 5000 \
    || { echo "ERROR: tailwind.css looks too small"; exit 1; }

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
