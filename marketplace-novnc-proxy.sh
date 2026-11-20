#!/usr/bin/env bash
# Install an HTTPS-only viewer vhost and a private systemd Unix-socket relay.
set -euo pipefail
usage() { printf 'Usage: sudo %s DOMAIN SSH_USER /path/fullchain.pem /path/privkey.pem [REMOTE_SOCKET]\n       %s --render DIR DOMAIN SSH_USER CERT KEY [REMOTE_SOCKET]\n' "$0" "$0" >&2; exit 2; }
render=''
if [[ ${1:-} == --render ]]; then [[ $# -ge 2 ]] || usage; render=$2; shift 2; fi
[[ $# == 4 || $# == 5 ]] || usage
host=$1 peer=$2 cert=$3 key=$4
backend=${5:-/home/$peer/.mmcp-pilot/mcp-live.sock}
unit=marketplace-novnc-relay
[[ $host =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && $peer =~ ^[a-z_][a-z0-9_-]*$ && $peer != root ]] || usage
for path in "$cert" "$key" "$backend"; do
  [[ $path =~ ^(/[A-Za-z0-9_.-]+)+$ && $path/ != *'/../'* && $path/ != *'/./'* ]] || usage
done
[[ $backend == *.sock && ${#backend} -le 100 ]] || usage
proxy_bin=/lib/systemd/systemd-socket-proxyd
[[ ! -x /usr/lib/systemd/systemd-socket-proxyd ]] || proxy_bin=/usr/lib/systemd/systemd-socket-proxyd
config=marketplace-novnc.conf
if [[ -n $render ]]; then
  mkdir -p "$render"
  out=$render
else
  [[ $EUID == 0 && -r $cert && -r $key && -x $proxy_bin && -d /etc/nginx/conf.d ]] || usage
  id "$peer" >/dev/null; id www-data >/dev/null
  out=$(mktemp -d)
  trap 'rm -rf -- "$out"' EXIT
fi
cat > "$out/$unit.socket" <<EOF
[Unit]
Description=Unix socket for the Marketplace noVNC ingress
[Socket]
ListenStream=/run/$unit.sock
SocketUser=www-data
SocketMode=0600
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
EOF
cat > "$out/$unit.service" <<EOF
[Unit]
Description=Relay from the noVNC ingress to the private SSH socket
Requires=$unit.socket
After=$unit.socket
[Service]
Type=notify
User=$peer
ExecStart=$proxy_bin $backend
NoNewPrivileges=yes
ProtectSystem=strict
PrivateTmp=yes
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
EOF
cat > "$out/$config" <<EOF
map \$http_upgrade \$marketplace_viewer_connection {
    default upgrade;
    '' close;
}
upstream marketplace_novnc_relay {
    server unix:/run/$unit.sock;
}
server {
    listen 127.0.0.1:28080;
    server_name 127.0.0.1;
    access_log off;
    error_log /dev/null;
    location = /mcp {
        client_max_body_size 32k;
        proxy_pass http://marketplace_novnc_relay/mcp;
        proxy_set_header Host 127.0.0.1:18000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 660s;
    }
    location / { return 404; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $host;
    ssl_certificate $cert;
    ssl_certificate_key $key;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    error_log /dev/null;
    if (\$host != $host) { return 421; }
    client_max_body_size 1k;

    # Allow only normalized viewer paths, never the raw URI or /mcp.
    location ~ "^/(view/(claim\\.js|[A-Za-z0-9_-]{32}/(screen|status|check|ws)?)|view-client\\.js|novnc/(core|vendor)/[A-Za-z0-9_./-]+)\$" {
        limit_except GET POST { deny all; }
        proxy_pass http://marketplace_novnc_relay\$uri;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$marketplace_viewer_connection;
        proxy_set_header Authorization "";
        proxy_set_header X-Marketplace-Identity "";
        proxy_buffering off;
        proxy_read_timeout 660s;
        proxy_connect_timeout 5s;
    }
    location / { return 404; }
}
EOF
[[ -z $render ]] || exit 0
# Validate both units and the full NGINX config before reloading the vhost.
systemd-analyze verify "$out/$unit.socket" "$out/$unit.service"
conf=/etc/nginx/conf.d/$config
if [[ -f $conf ]]; then cp -p "$conf" "$out/previous.conf"; fi
restore_conf() {
  if [[ -f $out/previous.conf ]]; then install -m 644 "$out/previous.conf" "$conf"; else rm -f -- "$conf"; fi
}
install -m 644 "$out/$config" "$conf"
if ! nginx -t; then restore_conf; exit 1; fi
if ! (
  install -m 644 "$out/$unit.socket" "$out/$unit.service" /etc/systemd/system/ &&
  systemctl daemon-reload &&
  systemctl stop "$unit.service" &&
  systemctl enable "$unit.socket" &&
  systemctl restart "$unit.socket" &&
  systemctl reload nginx
); then
  restore_conf
  exit 1
fi
printf 'Viewer: https://%s; private MCP: http://127.0.0.1:28080/mcp\n' "$host"
