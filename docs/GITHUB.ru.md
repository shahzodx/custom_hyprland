# Загрузка на GitHub

Весь проект находится в одной папке `arch-hyprland-dotfiles`. Загружай её
**содержимое** как корень репозитория: README, install.sh, config, docs и остальные
исходники. Скриншоты уже подключены относительными ссылками.

В этой папке подготовлен локальный Git-репозиторий с веткой `main`.
Создай на GitHub пустой репозиторий `arch-hyprland-dotfiles` без добавления README.
Затем выполни из папки проекта:

```bash
git add .
git commit -m "Add Arch Hyprland dotfiles and screenshots"
git remote add origin https://github.com/YOUR_USERNAME/arch-hyprland-dotfiles.git
git push -u origin main
```

В строке `git remote add` замени `YOUR_USERNAME` на своё имя в GitHub.
Если Git попросит имя и email автора, задай свои значения через `git config`.

В качестве описания репозитория подойдёт:

> Arch Linux + Hyprland dotfiles with wallpaper colors, Waybar widgets, installer and rollback.

Темы: `archlinux`, `hyprland`, `dotfiles`, `waybar`, `kitty`, `matugen`, `linux`.

## Архив для переноса

```bash
python3 -B scripts/package.py
```

Архив и SHA-256 появятся в `dist/`. Этот каталог исключён из Git через
`.gitignore`, поэтому бинарный дубликат проекта не попадёт в обычный коммит.
Архив содержит все исходники и скриншоты, без `.git`, личных баз и кэша.
