# This tag and the playwright pin in requirements.txt must be the same version.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99

WORKDIR /app

# Virtual display for headed Chromium. The socket directory is created here
# because Xvfb is started by a non-root user at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Don't run a browser that renders third-party pages as root.
USER pwuser

# Xvfb runs in the background and exec makes gunicorn PID 1, so it still
# receives SIGTERM and shuts down cleanly. xvfb-run would not pass it on.
CMD ["sh", "-c", "rm -f /tmp/.X99-lock; Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -noreset & exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --worker-class gthread --workers 1 --threads 8 --graceful-timeout 30"]
