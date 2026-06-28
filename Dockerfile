FROM python:3.11-slim

WORKDIR /workspace

# Install system dependencies if any are needed (e.g. gcc, git, build-essential)
# We keep it clean and minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy package installation configurations
COPY pyproject.toml .

# Install dependencies. Using pip install . installs all standard dependencies from pyproject.toml
# and sets up our application.
# Note: we copy a placeholder app directory structure first or just copy everything.
# Let's copy the code
COPY app/ app/
RUN pip install --no-cache-dir .

# Create the data directory for SQLite persistence and exports
RUN mkdir -p /data

# Expose the default FastAPI port
EXPOSE 8000

# Environment variables setup
ENV PORT=8000
ENV HOST=0.0.0.0
ENV DATABASE_PATH=/data/strava.db
ENV EXPORT_DIR=/data/exports

# Run the app
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
