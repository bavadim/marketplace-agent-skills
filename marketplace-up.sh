#!/usr/bin/env bash
set -euo pipefail
umask 077
usage() { printf 'Usage: %s USER@SSH_HOST VIEWER_DOMAIN [--update]\n' "$0" >&2; exit 2; }
[[ $# == 2 || ( $# == 3 && $3 == --update ) ]] || usage
peer=$1 domain=$2 update=${3:-}
[[ $peer =~ ^([a-z_][a-z0-9_-]*)@[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || usage
user=${BASH_REMATCH[1]}
[[ $domain =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || usage
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image=${MMCP_IMAGE:-ghcr.io/bavadim/marketplace-mcp:pilot}
name=mmcp-pilot
volume=mmcp-pilot-data
origin=https://$domain
socket=${MMCP_REMOTE_SOCKET:-/home/$user/.mmcp-pilot/mcp-live.sock}
state=$(docker container inspect --format '{{.State.Running}}' "$name" 2>/dev/null || true)
if [[ -n $state && -z $update ]]; then
  # Never silently replace a running container or its browser profile.
  config=$(docker exec "$name" printenv MARKET_MCP_VIEWER_ORIGIN 2>/dev/null || true)
  if [[ $state == false ]]; then
    docker start "$name" >/dev/null
    config=$(docker exec "$name" printenv MARKET_MCP_VIEWER_ORIGIN 2>/dev/null || true)
  fi
  if [[ $config != "$origin" ]]; then
    printf 'Warning: container uses a different viewer origin; run again with --update to change it.\n' >&2
  fi
else
  docker pull --platform linux/amd64 "$image"
  opts=(--security-opt no-new-privileges --cap-drop=ALL)
  probe() {
    docker run --rm --platform linux/amd64 --init --network none "${opts[@]}" --entrypoint python "$image" -c '
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/chromium", chromium_sandbox=True, timeout=12000)
    page = browser.new_page()
    page.goto("chrome://sandbox", timeout=8000)
    assert "adequately sandboxed" in page.locator("body").inner_text(timeout=5000).lower()
    browser.close()
'
  }
  if ! probe; then
    printf 'Default seccomp failed sandbox probe; trying the bundled profile.\n' >&2
    opts+=(--security-opt "seccomp=$root/mcp_servers/marketplace/chromium-seccomp.json")
    probe || { printf 'Chromium sandbox failed; container not replaced.\n' >&2; exit 1; }
  fi
  [[ -z $state ]] || docker rm -f "$name" >/dev/null
  docker run -d --restart unless-stopped --platform linux/amd64 --name "$name" --init --shm-size=512m \
    "${opts[@]}" -p 127.0.0.1:18000:8000 -e MARKET_MCP_BIND=0.0.0.0 \
    -e "MARKET_MCP_HOSTS=127.0.0.1:18000,$domain" -e "MARKET_MCP_VIEWER_ORIGIN=$origin" \
    --mount "source=$volume,target=/data" "$image" >/dev/null
fi
for ((attempt=0; attempt<20; attempt++)); do
  status=$(curl --noproxy '*' --max-time 2 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/mcp || true)
  if [[ $status == 401 ]]; then
    exec bash "$root/marketplace-tunnel.sh" "$peer" "$socket"
  fi
  sleep 1
done
printf 'MCP not ready on local port 18000 (expected HTTP 401). Check docker logs %s.\n' "$name" >&2
exit 1
