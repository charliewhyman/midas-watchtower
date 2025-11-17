FROM python:3.11-slim

WORKDIR /app

# Install universal dependencies (wget works everywhere)
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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