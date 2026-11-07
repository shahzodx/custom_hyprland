#!/usr/bin/env python3
"""Capture the installed Hyprland theme on a temporary workspace.

Run manually inside Hyprland. Demo tasks and launcher entries are isolated from
personal data. Only processes launched by this script are terminated afterwards.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip()


def dispatch(expression):
    response = run("hyprctl", "dispatch", expression)
    if response != "ok":
        raise RuntimeError(response)


def demo_terminal():
    print("\033[2J\033[H", end="")
    print("\033[38;2;150;204;248m  ARCH / HYPRLAND\033[0m")
    print("\033[2m  A quiet desktop. Everything within reach.\033[0m\n")
    subprocess.run(["fastfetch", "--config", str(ROOT / "config/fastfetch/config.jsonc"),
                    "--structure", "OS:Kernel:WM:Terminal:Packages:Uptime:Memory"], check=True)
    print("\n\033[2m  ~/dotfiles\033[0m \033[38;2;150;204;248m❯\033[0m ./install.sh --dry-run")
    print("\n  \033[32m✓\033[0m Hyprland · Waybar · Kitty · Wofi")
    print("  \033[32m✓\033[0m Wallpaper palette · GTK · Qt · Dunst")
    print("  \033[32m✓\033[0m Backups and restore · portable monitor profile")
    print("\n\033[2m  Super + R  apps     Super + W  wallpaper     Super + L  lock\033[0m")
    sys.stdout.flush()
    signal.pause()


def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def capture(output):
    monitors = json.loads(run("hyprctl", "monitors", "-j"))
    monitor = next(item for item in monitors if item["focused"])
    workspace = monitor["activeWorkspace"]["id"]
    active = json.loads(run("hyprctl", "activewindow", "-j"))
    clients = json.loads(run("hyprctl", "clients", "-j"))
    if any(item.get("pinned") for item in clients):
        raise RuntimeError("Сначала убери закреплённые окна: они попадут на скриншоты")
    preview = max([item["workspace"]["id"] for item in clients] + [0]) + 1
    output.mkdir(parents=True, exist_ok=True)
    paused = run("dunstctl", "is-paused")
    processes = []

    with tempfile.TemporaryDirectory(prefix="dotfiles-screenshots-") as temporary:
        temp = Path(temporary)
        log = (temp / "capture.log").open("w")

        def launch(command, *, env=None, text_input=None):
            process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE if text_input else subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True, text=True)
            processes.append(process)
            if text_input:
                process.stdin.write(text_input)
                process.stdin.close()
            return process

        def shot(name):
            time.sleep(1.0)
            visible = json.loads(run("hyprctl", "monitors", "-j"))
            current = next(item for item in visible if item["name"] == monitor["name"])
            if current["activeWorkspace"]["id"] != preview:
                raise RuntimeError("Рабочее пространство изменилось; съёмка остановлена")
            run("grim", "-o", monitor["name"], str(output / name))
            print(f"Saved {name}", flush=True)

        try:
            run("dunstctl", "set-paused", "true")
            dispatch(f"hl.dsp.focus({{workspace = {preview}}})")
            shot("01-desktop.png")
            layers = json.loads(run("hyprctl", "layers", "-j"))
            bars = [layer for level in layers[monitor["name"]]["levels"].values()
                    for layer in level if layer.get("namespace") == "waybar"]
            if bars:
                bottom = max(layer["y"] + layer["h"] for layer in bars) - monitor["y"]
                width = round(monitor["width"] / monitor["scale"])
                run("grim", "-g", f'{monitor["x"]},{monitor["y"]} {width}x{bottom}',
                    str(output / "05-waybar.png"))
            terminal = launch(["kitty", "--class", "dotfiles-preview", "--title", "Arch / Hyprland",
                               "--config", str(ROOT / "config/kitty/kitty.conf"),
                               "-o", "font_size=12", "-o", "remember_window_size=no",
                               sys.executable, str(Path(__file__).resolve()), "--demo-terminal"])
            for _ in range(40):
                demo = [client for client in json.loads(run("hyprctl", "clients", "-j"))
                        if client["class"] == "dotfiles-preview"]
                if demo:
                    break
                time.sleep(0.1)
            if not demo:
                raise RuntimeError("Не удалось открыть демонстрационный терминал")
            address = demo[0]["address"]
            target = json.dumps("address:" + address)
            dispatch(f'hl.dsp.window.float({{window = {target}, action = "toggle"}})')
            width = round(monitor["width"] / monitor["scale"] * 0.76)
            height = round(monitor["height"] / monitor["scale"] * 0.77)
            dispatch(f"hl.dsp.window.resize({{window = {target}, x = {width}, y = {height}, relative = false}})")
            dispatch(f"hl.dsp.window.center({{window = {target}}})")
            shot("02-terminal.png")
            stop(terminal)
            time.sleep(0.3)
            launcher_style = temp / "launcher.css"
            launcher_style.write_text((ROOT / "config/wofi/style.css").read_text().replace("@HOME@", str(Path.home())))
            # A real Wofi window, populated with public demo entries only.
            launcher = launch(["wofi", "--dmenu", "--conf", str(ROOT / "config/wofi/config"),
                               "--style", str(launcher_style),
                               "--cache-file", "/dev/null", "--prompt", "Applications"],
                              text_input="Firefox\nKitty\nDolphin\nVisual Studio Code\nPavucontrol\nBluetooth Manager\n")
            shot("03-launcher.png")
            stop(launcher)
            env = dict(os.environ, WAYBAR_TODO_STATE_DIR=str(temp / "todo"), PYTHONDONTWRITEBYTECODE="1")
            os.environ["WAYBAR_TODO_STATE_DIR"] = str(temp / "todo")
            spec = importlib.util.spec_from_file_location("demo_todo", ROOT / "config/waybar/scripts/todo.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tasks = module.Tasks()
            try:
                first = tasks.add("Собрать dotfiles с установщиком")
                tasks.complete(first, True)
                tasks.add("Добавить скриншоты рабочего стола")
                tasks.add("Опубликовать репозиторий на GitHub")
                tasks.add("Настроить следующий рабочий проект")
            finally:
                tasks.close()
            launch([sys.executable, "-B", str(ROOT / "config/waybar/scripts/todo.py"), "menu"], env=env)
            shot("04-tasks.png")
        finally:
            try:
                for process in reversed(processes):
                    stop(process)
                dispatch(f"hl.dsp.focus({{workspace = {workspace}}})")
                if active.get("address"):
                    dispatch("hl.dsp.focus({window = " + json.dumps("address:" + active["address"]) + "})")
            finally:
                run("dunstctl", "set-paused", paused)
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-terminal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots")
    args = parser.parse_args()
    if args.demo_terminal:
        demo_terminal()
    else:
        capture(args.output.resolve())


if __name__ == "__main__":
    main()
