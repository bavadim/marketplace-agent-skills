#!/usr/bin/env bash
# Restart only the SSH forward owned by this checkout's pilot launcher.
set -euo pipefail
umask 077
usage() { printf 'Usage: %s USER@SSH_HOST /absolute/remote.sock\n' "$0" >&2; exit 2; }
[[ $# == 2 ]] || usage
host=$1 socket=$2
[[ $host =~ ^[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || usage
[[ $socket =~ ^(/[a-zA-Z0-9_.-]+)+\.sock$ && $socket/ != *'/../'* && $socket/ != *'/./'* && ${#socket} -le 100 ]] || usage
[[ ${socket%/*} != "$socket" && -n ${socket%/*} ]] || usage
state=${MMCP_SSH_STATE:-$HOME/.mmcp-tunnel}
mkdir -p -m 700 "$state"
[[ -d $state && ! -L $state && -O $state ]] || exit 1
chmod 700 "$state"
control="$state/control"
if ! mkdir "$state/lock" 2>/dev/null; then
  printf 'Another tunnel restart is running; lock: %s/lock\n' "$state" >&2; exit 1
fi
trap 'rmdir "$state/lock"' EXIT
# Don't inherit forwards or other SSH masters from the user's SSH config.
common=(-o ConnectTimeout=8 -o ForwardAgent=no -o ForwardX11=no -o PermitLocalCommand=no)
if [[ -S $control ]]; then
  if ssh "${common[@]}" -S "$control" -O check "$host" 2>/dev/null; then
    ssh "${common[@]}" -S "$control" -O exit "$host"
  fi
fi
# Do not replace an active socket owned by a different SSH session. Only
# unlink a same-owner stale Unix socket, never a regular file/symlink.
ssh "${common[@]}" -o ControlPath=none -o ClearAllForwardings=yes "$host" "python3 - '$socket'" <<'PY'
import errno, os, pathlib, socket, stat, sys, time
p = pathlib.Path(sys.argv[1])
p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
d = p.parent.lstat()
if not stat.S_ISDIR(d.st_mode) or d.st_uid != os.getuid() or d.st_mode & 0o077:
    sys.exit(f'Remote directory must be private and owned by SSH user: {p.parent}')
for _ in range(15):
    try:
        entry = p.lstat()
    except FileNotFoundError:
        break
    if not stat.S_ISSOCK(entry.st_mode) or entry.st_uid != os.getuid():
        sys.exit('Remote path is not a socket owned by the SSH user')
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect(str(p))
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            break
        if exc.errno != errno.ECONNREFUSED:
            raise
        try:
            if p.lstat().st_ino == entry.st_ino:
                p.unlink()
                break
        except FileNotFoundError:
            break
    time.sleep(.2)
else:
    sys.exit('Remote socket already has a listener. Close the previous manual SSH tunnel first.')
PY
ssh "${common[@]}" -M -S "$control" -fNT -o ControlPersist=no \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -R "$socket:127.0.0.1:18000" "$host"
if ssh "${common[@]}" -S "$control" "$host" \
    "test \"\$(curl --noproxy '*' --unix-socket '$socket' --max-time 5 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/mcp)\" = 401"; then
  printf 'SSH tunnel ready: %s (MCP HTTP 401 without bearer)\n' "$socket"
else
  ssh "${common[@]}" -S "$control" -O exit "$host" || true
  printf 'Forward started, but remote MCP did not return HTTP 401.\n' >&2
  exit 1
fi
