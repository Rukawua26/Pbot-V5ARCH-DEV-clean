# Dockerfile for Sniper AI
# Optimized for OCI Ampere (ARM64) and deterministic non-root volumes

FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Ensure the bot looks for the DB in the mapped data directory
ENV SNIPER_DB_PATH=/app/data/sniper_brain.db

# Set working directory
WORKDIR /app

# Create non-root user with deterministic UID/GID for host volume ownership.
# UID/GID 1000 matches the default cloud user on most OCI Ubuntu/Oracle Linux VPS images.
ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g ${GROUP_ID} botgroup && \
    useradd -u ${USER_ID} -g botgroup -m botuser

# Install Python dependencies
# Using --no-cache-dir to keep image size small
COPY requirements.lock .
# Prefer wheels, but allow source install for lightweight packages without wheels (e.g. ta).
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.lock

# Copy the application code
COPY . .

# Create necessary data directories and set permissions
# We create /app/data and /app/models so the user can write to them
RUN mkdir -p /app/data /app/models && \
    chown -R botuser:botgroup /app

# Healthcheck: verify the bot heartbeat file is fresh (< 60s)
# Uses same path logic as core/watchdog.py (env var → /dev/shm → /tmp)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,time; from pathlib import Path; p=os.getenv('WATCHDOG_HEARTBEAT_PATH') or '/dev/shm/sniper_ai_heartbeat.json'; hb=Path(p) if Path(p).parent.exists() else Path('/tmp/sniper_ai_heartbeat.json'); assert hb.exists() and time.time()-hb.stat().st_mtime < 60, 'Heartbeat stale'; print(f'OK {hb}')" || exit 1

# Switch to non-root user
USER botuser

# Command to run the bot
CMD ["python", "main.py"]
