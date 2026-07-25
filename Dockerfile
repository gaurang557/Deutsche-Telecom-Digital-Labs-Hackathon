FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_DEMO_MODE=true \
    AGENT_PLANNER_PROVIDER=bedrock \
    AGENT_DB=/data/agent_store.db \
    PORT=8000
WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY app ./app
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /data /tmp/voice-desk-demo && \
    useradd --create-home --uid 10001 voicedesk && \
    chown -R voicedesk:voicedesk /data /tmp/voice-desk-demo
USER voicedesk
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
