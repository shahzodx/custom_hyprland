#!/usr/bin/env python3
"""Shared session timer for Waybar and a small GTK control window."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import time

BASE = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get('WAYBAR_TIMER_STATE_DIR', BASE / '.timer'))


def new_state(duration=1500):
    return {'mode': 'idle', 'duration': duration, 'remaining': duration, 'deadline': None}


def update(action=None, duration=None):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    finished = False
    with (STATE_DIR / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = STATE_DIR / 'state.json'
        try:
            state = json.loads(path.read_text())
            if state['mode'] not in ('idle', 'running', 'paused', 'done'):
                raise ValueError('Invalid timer mode')
            float(state['remaining'])
            int(state['duration'])
            if state['mode'] == 'running':
                float(state['deadline'])
        except (OSError, ValueError, KeyError, TypeError):
            state = new_state()
        previous = state.copy()
        now = time.time()
        if state['mode'] == 'running':
            state['remaining'] = max(0, state['deadline'] - now)
            if state['remaining'] == 0:
                state.update(mode='done', deadline=None)
                finished = True
        if action == 'start':
            seconds = int(duration if duration is not None else state['duration'])
            if not 60 <= seconds <= 180 * 60:
                raise ValueError('Choose between 1 and 180 minutes')
            state = new_state(seconds)
            state.update(mode='running', deadline=now + seconds)
        elif action == 'pause' and state['mode'] == 'running':
            state.update(mode='paused', deadline=None)
        elif action == 'resume' and state['mode'] == 'paused':
            state.update(mode='running', deadline=now + state['remaining'])
        elif action == 'reset':
            state = new_state(state['duration'])
        # The running countdown is derived from the deadline, not written every second.
        if action or state['mode'] != previous['mode'] or not path.exists():
            pending = STATE_DIR / 'state.tmp'
            pending.write_text(json.dumps(state))
            pending.replace(path)
    if finished:
        try:
            subprocess.Popen(['notify-send', '--app-name=Рабочий таймер',
                              '--icon=alarm-symbolic', 'Время вышло',
                              'Перерыв окончен.' if state['duration'] <= 900 else
                              'Сессия завершена. Можно сделать перерыв.'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    return state


def clock_text(seconds):
    seconds = max(0, math.ceil(seconds))
    return f'{seconds // 60:02}:{seconds % 60:02}'


def status():
    state = update()
    mode = state['mode']
    text = '' if mode == 'idle' else ('Готово' if mode == 'done' else clock_text(state['remaining']))
    title = {'idle': 'Рабочий таймер', 'running': 'Таймер идёт',
             'paused': 'Таймер на паузе', 'done': 'Время вышло'}[mode]
    print(json.dumps({'text': text, 'class': mode,
                      'tooltip': title + '\nНажмите, чтобы открыть управление'}, ensure_ascii=False))


def menu():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

    # Keep one timer window open at a time.
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    menu_lock = (STATE_DIR / 'window.lock').open('a')
    try:
        fcntl.flock(menu_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return

    provider = Gtk.CssProvider()
    provider.load_from_path(str(BASE / 'timer.css'))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Gtk.Window(title='Рабочий таймер')
    window.set_default_size(300, -1)
    window.set_resizable(False)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, 'waybar-timer')
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 8)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.RIGHT, 12)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
    window.connect('destroy', Gtk.main_quit)
    window.connect('key-press-event', lambda widget, event: window.destroy() if event.keyval == Gdk.KEY_Escape else False)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    card.set_name('timer-card')
    window.add(card)
    header = Gtk.Box(spacing=8)
    title = Gtk.Label(label='Рабочий таймер', xalign=0)
    title.set_name('timer-title')
    header.pack_start(title, True, True, 0)
    close = Gtk.Button.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU)
    close.set_name('timer-close')
    close.set_tooltip_text('Закрыть · Esc')
    close.connect('clicked', lambda *_: window.destroy())
    header.pack_end(close, False, False, 0)
    card.pack_start(header, False, False, 0)

    display = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    subtitle = Gtk.Label()
    subtitle.set_name('timer-subtitle')
    digits = Gtk.Label()
    digits.set_name('timer-digits')
    display.pack_start(subtitle, False, False, 0)
    display.pack_start(digits, False, False, 0)
    card.pack_start(display, False, False, 0)
    progress = Gtk.ProgressBar()
    card.pack_start(progress, False, False, 0)

    initial = update()
    selected_minutes = [initial['duration'] // 60]
    presets = Gtk.Box(spacing=6, homogeneous=True)
    preset_buttons = []
    spin = Gtk.SpinButton.new_with_range(1, 180, 1)
    spin.set_value(selected_minutes[0])
    spin.set_numeric(True)
    spin.set_width_chars(3)

    def select_minutes(button, minutes):
        spin.set_value(minutes)

    for label, minutes in [('25 мин', 25), ('50 мин', 50), ('5 · отдых', 5)]:
        button = Gtk.Button(label=label)
        button.connect('clicked', select_minutes, minutes)
        preset_buttons.append((button, minutes))
        presets.pack_start(button, True, True, 0)
    card.pack_start(presets, False, False, 0)
    custom = Gtk.Box(spacing=8)
    custom_label = Gtk.Label(label='Своё время', xalign=0)
    custom_label.get_style_context().add_class('muted')
    custom.pack_start(custom_label, True, True, 0)
    custom.pack_start(spin, False, False, 0)
    custom.pack_start(Gtk.Label(label='мин'), False, False, 0)
    card.pack_start(custom, False, False, 0)

    controls = Gtk.Box(spacing=8, homogeneous=True)
    reset = Gtk.Button(label='Сбросить')
    primary = Gtk.Button()
    primary.set_name('timer-primary')
    controls.pack_start(reset, True, True, 0)
    controls.pack_start(primary, True, True, 0)
    card.pack_start(controls, False, False, 0)
    hint = Gtk.Label(label='Можно закрыть окно — отсчёт продолжится')
    hint.set_name('timer-hint')
    card.pack_start(hint, False, False, 0)

    def refresh():
        state = update()
        mode = state['mode']
        editable = mode in ('idle', 'done')
        spin.set_sensitive(editable)
        for button, minutes in preset_buttons:
            button.set_sensitive(editable)
            context = button.get_style_context()
            if minutes == selected_minutes[0]:
                context.add_class('chosen')
            else:
                context.remove_class('chosen')
        remaining = selected_minutes[0] * 60 if mode == 'idle' else state['remaining']
        digits.set_text(clock_text(remaining))
        subtitle.set_text({'idle': 'Готов к началу', 'running': 'Время для себя' if state['duration'] <= 900 else 'Рабочая сессия',
                           'paused': 'На паузе', 'done': 'Время вышло'}[mode])
        progress.set_fraction(0 if mode == 'idle' else max(0, min(1, 1 - state['remaining'] / state['duration'])))
        primary.set_label({'idle': 'Начать', 'running': 'Пауза', 'paused': 'Продолжить', 'done': 'Ещё раз'}[mode])
        reset.set_sensitive(mode != 'idle')
        return True

    def change_duration(widget):
        selected_minutes[0] = widget.get_value_as_int()
        refresh()

    def act(*_):
        spin.update()
        mode = update()['mode']
        if mode in ('idle', 'done'):
            update('start', selected_minutes[0] * 60)
        else:
            update('pause' if mode == 'running' else 'resume')
        refresh()

    spin.connect('value-changed', change_duration)
    primary.connect('clicked', act)
    reset.connect('clicked', lambda *_: (update('reset'), refresh()))
    refresh()
    GLib.timeout_add(250, refresh)
    window.show_all()
    Gtk.main()
    menu_lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['status', 'menu', 'start', 'pause', 'resume', 'reset'])
    parser.add_argument('minutes', nargs='?', type=int, default=25)
    args = parser.parse_args()
    if args.command == 'status':
        status()
    elif args.command == 'menu':
        menu()
    else:
        update(args.command, args.minutes * 60 if args.command == 'start' else None)
