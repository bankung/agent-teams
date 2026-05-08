# Single-stage dev image. Production multi-stage build is out of scope for the Phase 3 scaffold slice.
# Healthcheck lives in docker-compose.yml (Next.js dev mode takes ~5-10s to compile; expect start_period ~30s).
FROM node:20-alpine

WORKDIR /app

# Copy manifests first for layer caching. package-lock.json may not exist on first build — copy if present
# without failing the build. The wildcard match makes the LOCK file optional.
COPY package.json package-lock.json* ./

RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
