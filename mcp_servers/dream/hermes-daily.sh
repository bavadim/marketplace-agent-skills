#!/bin/bash
set -euo pipefail
umask 077

# Install one copy per trusted owner/profile. This is operator-owned configuration,
# never generated from chat or model arguments. Store only paths here, not tokens.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/dream-daily.env"
: "${DREAM_CHECKOUT:?}" "${DREAM_PYTHON:?}" "${DREAM_DB:?}" "${DREAM_OWNER:?}" "${DREAM_TOKEN_FILE:?}"
export DREAM_DB DREAM_OWNER DREAM_TOKEN_FILE DREAM_CA_FILE
export DREAM_READ_ONLY=1
unset TINVEST_SANDBOX_TOKEN PYTHONPATH
cd -- "$DREAM_CHECKOUT"
exec "$DREAM_PYTHON" -m mcp_servers.dream.daily
