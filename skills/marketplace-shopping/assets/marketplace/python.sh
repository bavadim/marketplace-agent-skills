#!/bin/bash
# Usage: bash python.sh PYTHON SCRIPT [ARGS...]. Routing is inherited by children.
set -euo pipefail

if (( $# == 0 )); then
    echo 'Python command required' >&2
    exit 64
fi

if [[ -v ISPROXYGO ]]; then
    record=$(getent group proxygo) || { echo 'proxygo group unavailable' >&2; exit 126; }
    IFS=: read -r group_name unused group_gid group_members <<< "$record"
    [[ "$group_gid" =~ ^[0-9]+$ ]] || { echo 'proxygo group unavailable' >&2; exit 126; }
    if [[ $(id -g) != "$group_gid" ]]; then
        user_name=$(id -un)
        if [[ " $(id -G) " != *" $group_gid "* && ",$group_members," != *",$user_name,"* ]]; then
            echo 'Current user cannot use proxygo' >&2
            exit 126
        fi
        command -v sg >/dev/null || { echo 'sg unavailable' >&2; exit 126; }
        # sg runs /bin/sh; use POSIX single quoting, including embedded newlines.
        command='exec'
        for argument in "$BASH" "${BASH_SOURCE[0]}" "$@"; do
            command+=" '${argument//\'/\'\\\'\'}'"
        done
        exec sg proxygo -c "$command"
    fi
fi

export _SKILL_PYTHON_LAUNCH_PID=$$
exec "$@"
