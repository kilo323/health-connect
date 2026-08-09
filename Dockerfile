# ── Frontend build stage (production only) ──
FROM node:22-slim AS frontend-builder

WORKDIR /build
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci
COPY app/frontend/ ./
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

# ── Base stage ──
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl nginx tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

# Backend source
COPY app/ .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Production: pre-build frontend and bundle node modules
RUN npm ci --prefix frontend && \
    cp -r /build/dist ./frontend/dist && \
    cp -r /build/node_modules ./frontend/node_modules && \
    rm -rf /etc/nginx/sites-enabled/default && \
    cp /app/nginx.conf /etc/nginx/sites-enabled/health-connect;

EXPOSE 3000 8000

# Startup script
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
