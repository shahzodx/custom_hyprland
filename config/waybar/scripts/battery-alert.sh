#!/usr/bin/env bash
set -u

threshold=30
reset_threshold=35
poll_interval=30
already_notified=0

while true; do
    battery=""
    for device in /sys/class/power_supply/*; do
        [[ -r "$device/type" ]] || continue
        if [[ "$(<"$device/type")" == "Battery" && -r "$device/capacity" && -r "$device/status" ]]; then
            battery="$device"
            break
        fi
    done

    if [[ -n "$battery" ]]; then
        capacity="$(<"$battery/capacity")"
        status="$(<"$battery/status")"

        if [[ "$status" == "Discharging" && "$capacity" -le "$threshold" && "$already_notified" -eq 0 ]]; then
            if notify-send --urgency=normal --expire-time=8000 "Батарея: ${capacity}%" "Поставь на зарядку, кодер."; then
                already_notified=1
            fi
        elif [[ "$capacity" -ge "$reset_threshold" || "$status" == "Full" ]]; then
            already_notified=0
        fi
    fi

    sleep "$poll_interval"
done
