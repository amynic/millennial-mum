FROM python:3.12-slim

WORKDIR /app

# Install gh CLI (required by the Copilot SDK)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements-hosted.txt .
RUN pip install --no-cache-dir -r requirements-hosted.txt

# Copy application code
COPY agent_config.py .
COPY server.py .
COPY tools/ ./tools/
COPY family_profile.json .
COPY shopping_list.json .

EXPOSE 8088

CMD ["python", "server.py"]
