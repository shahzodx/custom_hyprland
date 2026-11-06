#!/usr/bin/env bash

SOCK="$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock"

compact() {
  local clients active ids target new_active
  clients=$(hyprctl clients -j)
  active=$(hyprctl activeworkspace -j | jq '.id')
  ids=$(jq -r '[.[] | .workspace.id | select(. > 0)] | unique | .[]' <<< "$clients")

  target=1
  new_active=""
  for id in $ids; do
    if [ "$id" -ne "$target" ]; then
      for addr in $(jq -r --argjson w "$id" '.[] | select(.workspace.id == $w) | .address' <<< "$clients"); do
        hyprctl dispatch "hl.dsp.window.move({ workspace = '$target', follow = false, window = 'address:$addr' })" >/dev/null
      done
      [ "$id" -eq "$active" ] && new_active=$target
    fi
    target=$((target + 1))
  done

  [ -n "$new_active" ] && hyprctl dispatch "hl.dsp.focus({ workspace = '$new_active' })" >/dev/null
}

socat -U - UNIX-CONNECT:"$SOCK" | while read -r line; do
  case "$line" in
    closewindow\>\>*|movewindowv2\>\>*)
      sleep 0.05
      compact
      ;;
  esac
done
