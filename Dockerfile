# syntax=docker/dockerfile:1
FROM node:22-slim AS frontend-builder

WORKDIR /build
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci

COPY app/frontend/ ./
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Backend
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

# Frontend static export
COPY --from=frontend-builder /build/dist ./frontend-dist

# Nginx configuration
RUN rm /etc/nginx/sites-enabled/default
COPY nginx.conf /etc/nginx/sites-enabled/health-connect

EXPOSE 80

# Startup script
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
