# ── Frontend build stage (production only) ──
FROM node:22-slim AS frontend-builder

WORKDIR /build
COPY app/frontend/ ./
RUN npm ci
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

# ── Base stage ──
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl nginx ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Backend source
COPY app/ .

# Remove the frontend source (we'll replace with built output)
RUN rm -rf frontend/node_modules frontend/.next frontend/dist

# Copy built frontend from builder stage
COPY --from=frontend-builder /build/dist /app/frontend/dist

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Configure nginx
RUN rm -rf /etc/nginx/sites-enabled/default && \
    cp /app/nginx.conf /etc/nginx/sites-enabled/health-connect

EXPOSE 3000 8000

# Startup script
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
