# Multi-stage build for Baloo Code Review Agent
# Pin to specific version for security patching - update periodically
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 as node-runtime

FROM python:3.15.0b2-slim-bookworm@sha256:5e946dab0224498220fff2f032782e0a87c8ec7d2cbf94d7dad884e9ffd0a274 as base

# Build arguments for version tracking
ARG BALOO_VERSION=dev
ARG BALOO_COMMIT_SHA=unknown
ARG BALOO_BUILD_DATE=unknown

# Set as environment variables
ENV BALOO_VERSION=${BALOO_VERSION}
ENV BALOO_COMMIT_SHA=${BALOO_COMMIT_SHA}
ENV BALOO_BUILD_DATE=${BALOO_BUILD_DATE}
ENV PATH="/app/node_modules/.bin:${PATH}"

# Copy Node.js from the pinned official Node image. PI requires Node >=20.6.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Install system dependencies and ensure OpenSSL is up-to-date
RUN apt-get update && apt-get install -y \
    curl \
    git \
    bubblewrap \
    && apt-get upgrade -y openssl libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files first (for better layer caching)
COPY requirements-prod.txt ./

# Install Python dependencies from a hash-pinned requirements export.
RUN pip install --no-cache-dir --require-hashes -r requirements-prod.txt

# Install PI coding agent from package-lock.json (provides the 'pi' CLI)
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Install AST tools extension dependencies
COPY extensions/package.json extensions/package-lock.json /app/extensions/
RUN cd /app/extensions && npm ci --omit=dev

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 baloo && chown -R baloo:baloo /app
USER baloo

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "main.py"]
