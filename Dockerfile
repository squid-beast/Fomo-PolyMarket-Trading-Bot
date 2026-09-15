# ---- stage 1: build the React UI --------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY web/ui/package.json web/ui/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ui/ ./
# vite.config.ts writes to ../static, which is /static inside this stage
RUN npm run build

# ---- stage 2: the service ---------------------------------------------
FROM python:3.11-slim

# non-root: a trading daemon should never run as root
RUN useradd -m -u 10001 trader
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ct/ ./ct/
COPY app/ ./app/
COPY web/server.py ./web/
COPY --from=ui /static ./web/static
COPY config.yaml dashboard.py scan.py audit.py ./

RUN mkdir -p /data && chown -R trader:trader /app /data
USER trader

ENV PYTHONUNBUFFERED=1 \
    STATE_FILE=/data/state.json \
    DB_FILE=/data/paper.db \
    LEDGER_FILE=/data/ledger.db

HEALTHCHECK --interval=120s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import os,json,time,sys; \
p=os.environ.get('STATE_FILE','/data/state.json'); \
sys.exit(0 if not os.path.exists(p) or time.time()-os.path.getmtime(p) < 900 else 1)"

CMD ["python", "-m", "app.service"]
