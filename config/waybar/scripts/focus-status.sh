#!/usr/bin/env bash
set -u

paused="$(dunstctl is-paused 2>/dev/null || printf 'false')"
if [[ "$paused" == "true" ]]; then
    printf '{"text":" ","tooltip":"Фокус включён · уведомления скрыты","class":"paused"}\n'
else
    printf '{"text":" ","tooltip":"Фокус выключен · нажмите, чтобы скрыть уведомления","class":"active"}\n'
fi
