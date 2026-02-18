# SKYNET FastAPI Control Plane - Dockerfile
FROM python:3.13-slim

LABEL maintainer="SKYNET Project"
LABEL description="SKYNET Control Plane API - AI-powered planning and policy enforcement"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY skynet/ ./skynet/

# Create data directory for SQLite
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/v1/health || exit 1

# Run the application
CMD ["uvicorn", "skynet.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
