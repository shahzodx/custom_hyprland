#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${HOME}/.config/waybar"
WALLPAPER_DIR="${HOME}/Pictures/wallpapers"
CURRENT_LINK="${HOME}/.cache/current-wallpaper"
ROFI_THEME="${CONFIG_DIR}/wallpaper-picker.rasi"
MATUGEN_CONFIG="${CONFIG_DIR}/matugen/config.toml"

mkdir -p "${HOME}/.cache" "${WALLPAPER_DIR}"

start_awww() {
    if ! awww query >/dev/null 2>&1; then
        (awww-daemon >/tmp/awww-daemon.log 2>&1 &)
        for _ in {1..30}; do
            awww query >/dev/null 2>&1 && return 0
            sleep 0.1
        done
        notify-send -u normal "Обои" "Не удалось запустить awww-daemon"
        return 1
    fi
}

apply_wallpaper() {
    local image="$1" accent theme_status="missing"

    [[ -f "${image}" ]] || {
        notify-send -u normal "Обои" "Файл не найден: ${image}"
        return 1
    }

    # Theme generation is optional until Matugen is installed; wallpapers still work.
    if command -v matugen >/dev/null 2>&1; then
        theme_status="failed"
        if matugen image --config "${MATUGEN_CONFIG}" --prefer less-saturation --fallback-color '#cbd2da' "${image}" >/tmp/matugen-wallpaper.log 2>&1; then
            theme_status="updated"
        fi
    fi

    if [[ "${theme_status}" == "updated" ]]; then
        dunstctl reload >/dev/null 2>&1 || true
        # Kitty supports config reload by SIGUSR1; no remote control socket needed.
        pkill -USR1 -x kitty 2>/dev/null || true
    fi

    start_awww || return 1
    # Avoid two wallpaper daemons competing for the background layer.
    awww img --transition-type wave --transition-duration 0.8 --transition-fps 60 "${image}"
    pkill -x hyprpaper 2>/dev/null || true

    ln -sfn "${image}" "${CURRENT_LINK}.new"
    mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"

    accent="$(sed -n 's/^@define-color wallpaper-accent #\([[:xdigit:]]\{6\}\);/\1/p' "${CONFIG_DIR}/wallpaper-colors.css" | head -n1)"
    if [[ "${accent}" =~ ^[[:xdigit:]]{6}$ ]] && command -v hyprctl >/dev/null 2>&1; then
        hyprctl eval "hl.config({general = {col = {active_border = 'rgba(${accent}ff)'}}})" >/dev/null 2>&1 || true
    fi

    pkill -SIGUSR2 waybar 2>/dev/null || true
    if [[ "${theme_status}" == "updated" ]]; then
        notify-send -t 1800 "Обои обновлены" "Плавный переход и новая палитра интерфейса"
    elif [[ "${theme_status}" == "failed" ]]; then
        notify-send -u normal "Обои изменены" "Не удалось обновить палитру. Лог: /tmp/matugen-wallpaper.log"
    else
        notify-send -u normal "Обои изменены" "Палитра пока прежняя: Matugen не установлен"
    fi
}

if [[ "${1:-}" == "--restore" ]]; then
    if [[ -L "${CURRENT_LINK}" && -f "${CURRENT_LINK}" ]]; then
        apply_wallpaper "$(readlink -f "${CURRENT_LINK}")"
    else
        apply_wallpaper "${WALLPAPER_DIR}/dotfiles-default.jpeg"
    fi
    exit $?
fi

mapfile -d '' -t images < <(
    find "${WALLPAPER_DIR}" -type f \( \
        -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o \
        -iname '*.webp' -o -iname '*.avif' \
    \) -print0 | sort -z
)

if ((${#images[@]} == 0)); then
    notify-send -u normal "Обои" "Добавь изображения в ~/Pictures/wallpapers"
    exit 1
fi

rows="$(mktemp)"
trap 'rm -f "${rows}"' EXIT
for image in "${images[@]}"; do
    name="$(basename "${image}")"
    printf '%s\0icon\x1f%s\n' "${name}" "${image}" >>"${rows}"
done

selection="$(rofi -dmenu -i -show-icons -no-custom -theme "${ROFI_THEME}" -format i <"${rows}")" || exit 0
[[ "${selection}" =~ ^[0-9]+$ ]] || exit 0
index=$((selection))
((index < ${#images[@]})) || exit 0
apply_wallpaper "${images[index]}"
