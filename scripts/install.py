#!/usr/bin/env python3
"""Install a curated config snapshot with per-file backups and explicit restore."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "arch-hyprland-dotfiles-v1"


def exists(path):
    return path.exists() or path.is_symlink()


def check_parents(path, home):
    """Never write through a config directory redirected by a symlink."""
    if not path.is_relative_to(home) or path == home:
        raise ValueError(f"Путь вне домашней папки: {path}")
    for parent in path.parents:
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"Родительская папка — симлинк: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Вместо папки найден файл: {parent}")


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".dotfiles-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def write_manifest(backup, manifest):
    atomic_write(backup / "manifest.json",
                 (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(),
                 0o600)


def payload(source, home):
    data = source.read_bytes()
    if source.suffix not in {".png", ".jpeg", ".jpg", ".ttf"}:
        data = data.decode().replace("@HOME@", str(home)).encode()
    return data


def plan_files(home, profile):
    files = {}
    for source in sorted((ROOT / "config").rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        if source.is_symlink():
            raise ValueError(f"Неожиданный симлинк в сборке: {source}")
        relative = Path(".config") / source.relative_to(ROOT / "config")
        files[relative] = (payload(source, home), source.stat().st_mode & 0o777)
    if profile == "laptop":
        source = ROOT / "profiles/laptop/monitors.lua"
        files[Path(".config/hypr/monitors.lua")] = (payload(source, home), 0o644)
    wallpaper = Path("Pictures/wallpapers/dotfiles-default.jpeg")
    if not exists(home / wallpaper):
        files[wallpaper] = ((ROOT / "assets/wallpaper.jpeg").read_bytes(), 0o644)
    plan = []
    for relative, (data, mode) in sorted(files.items()):
        target = home / relative
        check_parents(target, home)
        if target.is_dir() and not target.is_symlink():
            raise ValueError(f"Вместо файла найдена папка: {target}")
        if not target.is_symlink() and target.is_file():
            if target.read_bytes() == data and target.stat().st_mode & 0o777 == mode:
                continue
        plan.append((relative, data, mode))
    return plan


def restore(backup, dry_run=False):
    backup = backup.expanduser().resolve()
    manifest = json.loads((backup / "manifest.json").read_text())
    if manifest.get("format") != FORMAT:
        raise ValueError("Неизвестный формат резервной копии")
    home = Path(manifest["home"]).resolve()
    if manifest.get("restored"):
        print(f"Эта копия уже восстановлена: {backup}")
        return
    entries = manifest["entries"]
    # Validate every entry and backup before changing any installed file.
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"Некорректный путь в манифесте: {relative}")
        target = home / relative
        check_parents(target, home)
        if target.is_dir() and not target.is_symlink():
            raise ValueError(f"Вместо восстанавливаемого файла найдена папка: {target}")
        original = backup / "files" / relative
        if entry["existed"] and not exists(original):
            raise ValueError(f"Не найдена резервная копия: {original}")
    for entry in reversed(entries):
        relative = Path(entry["path"])
        target = home / relative
        print(f"{'RESTORE' if entry['existed'] else 'REMOVE '} {target}")
        if dry_run:
            continue
        # Keep any edits made since installation, including generated palettes.
        saved = backup / "before-restore" / relative
        if exists(target):
            if not exists(saved):
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved, follow_symlinks=False)
            target.unlink()
        if entry["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "files" / relative, target, follow_symlinks=False)
    if not dry_run:
        manifest["restored"] = True
        write_manifest(backup, manifest)
        print(f"Восстановлено. Изменения после установки сохранены в {backup / 'before-restore'}")


def install(home, profile, dry_run=False):
    # Config formats and embedded shell commands need paths without shell metacharacters.
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(home)):
        raise ValueError("Путь домашней папки должен содержать только латиницу, цифры, / . _ -")
    if home == Path("/"):
        raise ValueError("Корень файловой системы нельзя использовать как домашнюю папку")
    if not home.is_dir():
        raise ValueError(f"Домашняя папка не существует: {home}")
    if ROOT.is_relative_to(home / ".config/hypr"):
        raise ValueError("Перемести сборку за пределы устанавливаемых каталогов")
    plan = plan_files(home, profile)
    for relative, _, _ in plan:
        print(f"{'UPDATE' if exists(home / relative) else 'CREATE'} {home / relative}")
    if dry_run:
        print(f"Предпросмотр: {len(plan)} файлов. Ничего не записано.")
        return
    if not plan:
        print("Все файлы уже установлены; изменений нет.")
        return
    state = home / ".local/state/arch-hyprland-dotfiles/backups"
    check_parents(state / "placeholder", home)
    state.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=state))
    manifest = {"format": FORMAT, "home": str(home), "profile": profile,
                "restored": False, "entries": []}
    # Back up all original files before the first config write.
    for relative, data, _ in plan:
        target = home / relative
        present = exists(target)
        if present:
            saved = backup / "files" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved, follow_symlinks=False)
        manifest["entries"].append({"path": str(relative), "existed": present,
                                    "installed_sha256": hashlib.sha256(data).hexdigest()})
    write_manifest(backup, manifest)
    print(f"Резервная копия подготовлена: {backup}", flush=True)
    try:
        for relative, data, mode in plan:
            atomic_write(home / relative, data, mode)
    except BaseException:
        print(f"Установка прервана; выполняю откат из {backup}", file=sys.stderr)
        restore(backup)
        raise
    print(f"\nУстановлено: {len(plan)} файлов. Резервная копия: {backup}")
    print(f"Откат: {ROOT / 'install.sh'} --restore {backup}")
    print("Для применения автозапуска выйди из Hyprland и войди снова.")


def packages(dry_run):
    package_names = [line.split("#", 1)[0].strip()
                     for line in (ROOT / "packages.txt").read_text().splitlines()]
    package_names = [name for name in package_names if name]
    command = ["sudo", "pacman", "-Syu", "--needed", *package_names]
    print(" ".join(command), flush=True)
    if not dry_run:
        if not shutil.which("pacman"):
            raise ValueError("Установка пакетов поддерживается только через pacman в Arch Linux")
        subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restore", type=Path, metavar="BACKUP", help="Восстановить резервную копию")
    mode.add_argument("--packages", action="store_true", help="Установить зависимости через pacman")
    parser.add_argument("--dry-run", action="store_true", help="Показать действия без записи")
    parser.add_argument("--home", type=Path, help="Целевая домашняя папка (для проверки или переноса)")
    parser.add_argument("--profile", choices=["portable", "laptop"], default="portable")
    args = parser.parse_args()
    if (args.packages or args.restore) and (args.home or args.profile != "portable"):
        parser.error("--home и --profile используются только при установке конфигов")
    if args.packages:
        packages(args.dry_run)
    elif args.restore:
        restore(args.restore, args.dry_run)
    else:
        if os.geteuid() == 0:
            parser.error("Запускай установщик от обычного пользователя, без sudo")
        home = (args.home or Path.home()).expanduser().resolve()
        if args.home is None:
            expected = {"XDG_CONFIG_HOME": home / ".config", "XDG_CACHE_HOME": home / ".cache"}
            for key, value in expected.items():
                if os.environ.get(key) and Path(os.environ[key]).resolve() != value:
                    parser.error(f"Сборка рассчитана на {key}={value}; найден нестандартный путь")
        install(home, args.profile, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)
