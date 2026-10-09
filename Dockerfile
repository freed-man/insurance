
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install a virtual display for visible Chromium mode.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput


CMD ["sh", "-c", "xvfb-run -a gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --timeout 120 --graceful-timeout 30"]

