# ── Frontend build stage (production only) ──
FROM node:22-slim AS frontend-builder

# Pin npm to the latest stable major version to avoid warnings and ensure reproducible installs
RUN npm install -g npm@12

WORKDIR /build
COPY app/frontend/ ./
RUN npm ci
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

# ── Base stage ──
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl nginx ca-certificates gettext-base && \
    rm -rf /var/lib/apt/lists/*

# Backend source
COPY app/ .

# Remove the frontend source (we'll replace with built output)
RUN rm -rf frontend/node_modules frontend/.next frontend/dist

# Copy built frontend from builder stage
COPY --from=frontend-builder /build/dist /app/frontend/dist

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Configure nginx (config is rendered via envsubst in start.sh using
# BACKEND_PORT/FRONTEND_PORT, so just remove the default site here)
RUN rm -rf /etc/nginx/sites-enabled/default

EXPOSE 3000 8000

# Startup script
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
