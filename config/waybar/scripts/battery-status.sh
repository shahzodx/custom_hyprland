#!/usr/bin/env bash
set -euo pipefail

battery=""
power_supply_dir="${WAYBAR_POWER_SUPPLY_DIR:-/sys/class/power_supply}"
external_power="unknown"
for device in "$power_supply_dir"/*; do
    [[ -r "$device/type" ]] || continue
    if [[ "$(<"$device/type")" == "Battery" ]]; then
        battery="$device"
    elif [[ -r "$device/online" ]]; then
        # A disconnected charger takes precedence over a stale Charging status.
        if [[ "$(<"$device/online")" == 1 ]]; then
            external_power="yes"
        elif [[ "$external_power" != yes ]]; then
            external_power="no"
        fi
    fi
done

if [[ -z "$battery" || ! -r "$battery/capacity" ]]; then
    printf '{"text":"—","tooltip":"Батарея не найдена","class":"warning"}\n'
    exit 0
fi

capacity="$(<"$battery/capacity")"
status="$(<"$battery/status")"
if [[ "$external_power" == no ]]; then
    status="Discharging"
fi

case "$status" in
    Charging)
        css_class="charging"
        state="Идёт зарядка"
        ;;
    Full)
        css_class="plugged"
        state="Заряжена полностью"
        ;;
    Discharging)
        css_class="normal"
        state="Работает от батареи"
        ;;
    *)
        css_class="normal"
        state="Состояние батареи неизвестно"
        if [[ "$external_power" == yes ]]; then
            css_class="plugged"
            state="Подключена к питанию · зарядка приостановлена"
        fi
        ;;
esac

if [[ "$external_power" == yes && "$status" == Discharging ]]; then
    css_class="plugged"
    state="Подключена к питанию · ожидание зарядки"
fi

if [[ "$css_class" == normal ]]; then
    (( capacity <= 15 )) && css_class="critical"
    (( capacity > 15 && capacity <= 30 )) && css_class="warning"
fi

printf '{"text":"%s%%","tooltip":"%s · %s%%","class":"%s"}\n' \
    "$capacity" "$state" "$capacity" "$css_class"
