# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js and npm (and remove what's not needed after installation)
RUN apt-get update \
	&& apt-get install -y --no-install-recommends build-essential curl gnupg \
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
	&& apt-get install -y nodejs \
	&& apt-get purge -y curl gnupg \
	&& rm -rf /var/lib/apt/lists/* \
	&& npm install -g mcp-remote \
	&& npm cache clean --force

RUN pip install uv
# Copy requirements first for better caching
COPY requirements.txt ./requirements.txt

# Install Python dependencies
RUN uv pip install --no-cache-dir --system -r ./requirements.txt

# Install additional dependencies mentioned in README
# RUN uv pip install --no-cache-dir --system google-adk litellm

# Create the user and group first in a separate layer
RUN addgroup --system appuser && adduser --system -home /home/appuser --ingroup appuser appuser

# Set the HOME environment variable to the user's home directory
ENV HOME=/home/appuser

# Create data, artifacts, and agents directories
RUN mkdir -p /app/data /app/artifacts /app/agents

RUN chown -R appuser:appuser /app

# Copy files with the correct ownership directly
# Copy only specified agents based on build argument
ARG AGENTS_LIST=""
RUN echo "Building with agents: $AGENTS_LIST"

# Agents directory already created above

# # Copy all agents first, then remove unwanted ones
COPY --chown=appuser:appuser agents/ ./agents/

# Remove agents not in the list
RUN echo "$AGENTS_LIST" | tr ',' '\n' > /tmp/agents_list.txt && \
    for dir in ./agents/*/; do \
        agent_name=$(basename "$dir"); \
        if ! grep -q "^${agent_name}$" /tmp/agents_list.txt; then \
            echo "Removing agent: $agent_name"; \
            rm -rf "$dir"; \
        else \
            echo "Keeping agent: $agent_name"; \
        fi; \
    done && \
    rm -f /tmp/agents_list.txt



# Copy the shared directories -- should be copied in each deployment
COPY --chown=appuser:appuser shared/ ./shared/
COPY --chown=appuser:appuser server/ ./server/
COPY --chown=appuser:appuser auth_server.py ./auth_server.py
COPY --chown=appuser:appuser adk_main.py ./adk_main.py
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser static/ ./static/

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

# Expose port for web interface (if needed)
EXPOSE 8000

# Default command: run auth server (same as docker-compose)
CMD ["python", "auth_server.py"]
