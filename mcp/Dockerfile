FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MARKETPLACE_CHROMIUM_BINARY=/usr/bin/chromium MARKET_MCP_DATA=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium curl ca-certificates fonts-dejavu-core novnc tigervnc-standalone-server xauth \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --home-dir /data marketplace \
    && install -d -m 0700 -o 10001 -g 10001 /data \
    && install -d -m 1777 /tmp/.X11-unix
COPY requirements.txt /app/engine-requirements.txt
COPY mcp/requirements.txt /app/mcp-requirements.txt
RUN pip install --no-cache-dir -r /app/engine-requirements.txt -r /app/mcp-requirements.txt
COPY skills/marketplace-shopping /app/skills/marketplace-shopping
COPY skills/marketplace-accounts /app/skills/marketplace-accounts
COPY mcp/*.py mcp/view.html mcp/view-client.js /app/mcp/
COPY mcp/chromium-policy.json /etc/chromium/policies/managed/marketplace.json
ENV HOME=/data
USER 10001:10001
ENTRYPOINT ["python", "/app/mcp/market_mcp.py"]
CMD ["serve"]
