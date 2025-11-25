FROM python:3.11-slim

WORKDIR /app

# Install universal dependencies
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# ---- dependencies ----
# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock* requirements.txt* ./

# Install Python dependencies with uv.lock support
RUN python -m pip install --upgrade pip && \
    if [ -f uv.lock ]; then \
      pip install uv && \
      uv export --format requirements-txt > /tmp/requirements.txt && \
      pip install --no-cache-dir -r /tmp/requirements.txt; \
    elif [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt; \
    else \
      echo "No dependency manifest found (uv.lock or requirements.txt)" && exit 1; \
    fi
# ---- end dependencies ----

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