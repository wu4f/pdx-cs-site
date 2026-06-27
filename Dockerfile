# Multi-stage build: 'builder' fetches the docs and renders the site;
# the final image just serves it.

FROM python:3.12-slim AS builder

# Required for the build step (fetches docs + runs categorization LLM):
ARG GOOGLE_API_KEY
ENV GOOGLE_API_KEY=$GOOGLE_API_KEY

# Pick auth mode: 'service_account' for Cloud Run, 'oauth' for local dev.
ENV GDOC_AUTH_MODE=service_account

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY . .

# The service-account JSON must be supplied at build time (Cloud Build secret,
# `docker build --secret`, or simply COPYed in from CI). It's NOT copied into
# the final image. Share both Google Docs with the SA email.
# Example with --secret:  docker build --secret id=sa,src=service_account.json .
RUN --mount=type=secret,id=sa,target=/run/secrets/sa \
    if [ -f /run/secrets/sa ]; then cp /run/secrets/sa service_account.json; fi && \
    python -m cspdx.cli build && \
    rm -f service_account.json


FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY --from=builder /app /app

ENV PORT=8080 \
    SITE_DIR=/app/build/site \
    SECTIONS_PATH=/app/build/sections.json

# Version-controlled assets in static/ (incl. uploaded PDFs in static/files/)
# were copied into build/site by `cspdx build` in the builder stage. Admin
# uploads at runtime write to static/files/ and are EPHEMERAL in a container --
# mount a volume at /app/static/files to persist them across restarts.

# GOOGLE_API_KEY must be provided at runtime (Cloud Run env var or secret)
# for the chat to call Gemini. ADMIN_TOKEN gates the /admin/reload endpoint.
CMD uvicorn server.app:app --host 0.0.0.0 --port $PORT
