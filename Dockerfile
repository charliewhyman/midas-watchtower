# syntax=docker/dockerfile:1.4

FROM python:3.11-slim

WORKDIR /app

# Install universal dependencies
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy only requirements first for better layer caching
COPY requirements.txt .

# Use BuildKit cache mount for pip to persist downloads between builds.
# This requires BuildKit / buildx which is enabled in the workflow.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p data logs

# Create non-root user (works on all platforms)
RUN groupadd -r monitor && useradd -r -g monitor monitor
RUN chown -R monitor:monitor /app
USER monitor

# Expose FastAPI port
EXPOSE 8000

# Run the application
CMD ["python", "app.py"]