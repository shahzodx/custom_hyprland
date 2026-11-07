#!/usr/bin/env python3
"""Local, foreground-app time tracking and a small Waybar statistics panel."""

import argparse
from datetime import datetime, timedelta, time as day_time
import fcntl
import html
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get('WAYBAR_USAGE_STATE_DIR', BASE / '.app-usage'))
try:
    with open("/etc/localtime", "rb") as zone_file:
        LOCAL_TZ = ZoneInfo.from_file(zone_file)
except OSError:
    LOCAL_TZ = datetime.now().astimezone().tzinfo
SAMPLE_SECONDS = 1
FLUSH_SECONDS = 5
IDLE_SECONDS = 180


def prepare_state():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)


def write_json(name, value):
    pending = STATE_DIR / (name + '.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False))
    pending.replace(STATE_DIR / name)


def read_json(name):
    try:
        return json.loads((STATE_DIR / name).read_text())
    except (OSError, ValueError):
        return {}


def process_identity(pid):
    try:
        directory = Path('/proc') / str(int(pid))
        # The command field may contain spaces and parentheses.
        stat = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        return (directory / 'comm').read_text().strip(), stat[19]
    except (OSError, ValueError, IndexError):
        return None


def mark_idle(idle):
    prepare_state()
    if not idle:
        (STATE_DIR / 'idle.json').unlink(missing_ok=True)
        return
    # Tie the marker to this hypridle process, so a previous login cannot pause
    # tracking forever. A new hypridle instance has a different start time.
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        identity = process_identity(directory.name)
        if identity and identity[0] == 'hypridle':
            write_json('idle.json', {'pid': int(directory.name), 'start': identity[1]})
            return


def is_idle():
    marker = read_json('idle.json')
    if 'pid' not in marker or 'start' not in marker:
        return False
    return process_identity(marker['pid']) == ('hypridle', marker['start'])


def is_locked():
    try:
        return subprocess.run(['pidof', 'hyprlock'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=0.5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        # If the lock check fails, avoid attributing time to the window behind it.
        return True


def active_app():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    root = runtime / 'hypr'
    signature = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
    candidates = [root / signature / '.socket.sock'] if signature else []
    candidates.extend(sorted(root.glob('*/.socket.sock'),
                             key=lambda path: path.stat().st_mtime, reverse=True))
    for path in dict.fromkeys(candidates):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.5)
                connection.connect(str(path))
                connection.sendall(b'j/activewindow')
                chunks = []
                while data := connection.recv(65536):
                    chunks.append(data)
            window = json.loads(b''.join(chunks))
            if not window.get('mapped') or window.get('hidden'):
                return ''
            # Only the application class is retained, never a window title or URL.
            return (window.get('class') or window.get('initialClass') or '').strip().casefold()
        except (OSError, ValueError, AttributeError):
            continue
    return ''


def split_days(start, end):
    """Split a short interval across local calendar dates, including midnight."""
    while start < end:
        local = datetime.fromtimestamp(start, LOCAL_TZ)
        midnight = datetime.combine(local.date() + timedelta(days=1), day_time(), LOCAL_TZ)
        stop = min(end, midnight.timestamp())
        yield local.date().isoformat(), stop - start
        start = stop


class Recorder:
    def __init__(self, database):
        self.database = database
        self.previous = None
        self.pending = {}

    def sample(self, app, eligible, wall, monotonic):
        eligible = eligible and bool(app)
        previous = self.previous
        self.previous = (app, eligible, wall, monotonic)
        if previous is None:
            return
        old_app, was_eligible, old_wall, old_monotonic = previous
        elapsed = monotonic - old_monotonic
        # Do not count missed samples, clock changes, suspend, reconnects or locks.
        if (not old_app or not was_eligible or not eligible or
                not 0 < elapsed <= SAMPLE_SECONDS * 4 or
                abs(wall - old_wall - elapsed) > 0.5):
            return
        for day, seconds in split_days(old_wall, old_wall + elapsed):
            key = day, old_app
            self.pending[key] = self.pending.get(key, 0) + seconds

    def flush(self):
        if not self.pending:
            return
        with self.database:
            self.database.executemany(
                'INSERT INTO usage(day, app, seconds) VALUES (?, ?, ?) '
                'ON CONFLICT(day, app) DO UPDATE SET seconds = seconds + excluded.seconds',
                [(day, app, seconds) for (day, app), seconds in self.pending.items()])
        self.pending.clear()


def open_database():
    prepare_state()
    database = sqlite3.connect(STATE_DIR / 'usage.sqlite3', timeout=5)
    database.execute('PRAGMA journal_mode=WAL')
    database.executescript('''
        CREATE TABLE IF NOT EXISTS usage (
            day TEXT NOT NULL, app TEXT NOT NULL,
            seconds REAL NOT NULL CHECK(seconds >= 0), PRIMARY KEY(day, app)
        );
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    with database:
        database.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)',
                         ('started', datetime.now(LOCAL_TZ).isoformat()))
    return database


def daemon():
    prepare_state()
    with (STATE_DIR / 'daemon.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        stopped = False

        def stop(*_):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        database = open_database()
        recorder = Recorder(database)
        last_flush = 0
        try:
            while not stopped:
                tick_start = time.monotonic()
                idle = is_idle()
                locked = is_locked()
                app = '' if idle or locked else active_app()
                now = time.time()
                mono = time.monotonic()
                recorder.sample(app, bool(app) and not idle and not locked, now, mono)
                if mono - last_flush >= FLUSH_SECONDS:
                    recorder.flush()
                    write_json('runtime.json', {'updated': now, 'app': app,
                               'reason': 'idle' if idle else 'locked' if locked else
                               'tracking' if app else 'no-window'})
                    last_flush = mono
                time.sleep(max(0, SAMPLE_SECONDS - (time.monotonic() - tick_start)))
        finally:
            recorder.flush()
            database.close()
            (STATE_DIR / 'runtime.json').unlink(missing_ok=True)


def report(period='today'):
    path = STATE_DIR / 'usage.sqlite3'
    if not path.exists():
        return {'apps': [], 'total': 0, 'started': None}
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as database:
        where = 'WHERE day = ?' if period == 'today' else ''
        values = [datetime.now(LOCAL_TZ).date().isoformat()] if period == 'today' else []
        rows = database.execute(
            'SELECT app, SUM(seconds) FROM usage ' + where +
            ' GROUP BY app ORDER BY SUM(seconds) DESC, app', values).fetchall()
        started = database.execute("SELECT value FROM metadata WHERE key = 'started'").fetchone()
    return {'apps': [{'app': app, 'seconds': seconds} for app, seconds in rows],
            'total': sum(seconds for _, seconds in rows),
            'started': started[0] if started else None}


def duration(seconds, compact=False):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return '< 1 мин' if compact else f'{seconds} с'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} мин'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} ч {minutes:02} мин'


class AppCatalog:
    def __init__(self):
        from gi.repository import Gio
        self.apps = {}
        for app in Gio.AppInfo.get_all():
            app_id = (app.get_id() or '').removesuffix('.desktop')
            executable = app.get_executable() or ''
            keys = [app_id, Path(executable).name]
            if hasattr(app, 'get_startup_wm_class'):
                keys.append(app.get_startup_wm_class() or '')
            for key in keys:
                if key:
                    self.apps.setdefault(key.casefold(), app)

    def lookup(self, key):
        app = self.apps.get(key)
        return (app.get_display_name(), app.get_icon()) if app else (key, None)


def runtime_status():
    state = read_json('runtime.json')
    if time.time() - state.get('updated', 0) > FLUSH_SECONDS * 3:
        return 'offline'
    return state.get('reason', 'offline')


def status():
    data = report()
    reason = runtime_status()
    labels = {'tracking': 'Учёт идёт', 'idle': 'Пауза: нет активности',
              'locked': 'Пауза: экран заблокирован', 'no-window': 'Нет активного окна',
              'offline': 'Счётчик не запущен'}
    lines = ['Использование приложений · сегодня', 'Всего: ' + duration(data['total'])]
    if data['apps']:
        import gi
        catalog = AppCatalog()
        lines.append('')
        for app in data['apps'][:6]:
            name, _ = catalog.lookup(app['app'])
            lines.append(f"{name} — {duration(app['seconds'])}")
    lines.extend(['', labels[reason], 'Нажмите: сегодня и за всё время'])
    print(json.dumps({'text': duration(data['total'], compact=True),
                      'tooltip': html.escape('\n'.join(lines)), 'class': reason},
                     ensure_ascii=False))


def menu():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import Gtk, Gdk, GLib, GtkLayerShell, Pango

    prepare_state()
    window_lock = (STATE_DIR / 'window.lock').open('a')
    try:
        fcntl.flock(window_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(BASE / 'app-usage.css'))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    catalog = AppCatalog()
    window = Gtk.Window(title='Использование приложений')
    window.set_name('app-usage')
    window.set_default_size(420, -1)
    window.set_resizable(False)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, 'waybar-app-usage')
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 8)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.RIGHT, 12)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)

    # Separate transparent surfaces catch outside clicks, including on the bar
    # and other monitors. The card remains a normal, independently sized window.
    # No focus-out handler: Hyprland's follow-mouse focus must not dismiss it.
    dismiss_areas = []

    def dismiss(*_):
        window.destroy()
        return True

    def closed(*_):
        for area in dismiss_areas:
            area.destroy()
        Gtk.main_quit()

    display = Gdk.Display.get_default()
    for index in range(display.get_n_monitors()):
        area = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        area.set_name('usage-dismiss-area')
        area.set_title('Закрыть статистику')
        area.set_decorated(False)
        area.set_app_paintable(True)
        visual = area.get_screen().get_rgba_visual()
        if visual:
            area.set_visual(visual)
        GtkLayerShell.init_for_window(area)
        GtkLayerShell.set_namespace(area, 'waybar-app-usage-dismiss')
        GtkLayerShell.set_monitor(area, display.get_monitor(index))
        GtkLayerShell.set_layer(area, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(area, -1)
        GtkLayerShell.set_keyboard_mode(area, GtkLayerShell.KeyboardMode.NONE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(area, edge, True)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.TOUCH_MASK)
        area.connect('button-press-event', dismiss)
        area.connect('touch-event', lambda _, event:
                     dismiss() if event.type == Gdk.EventType.TOUCH_BEGIN else False)
        dismiss_areas.append(area)

    window.connect('destroy', closed)
    window.connect('key-press-event', lambda _, event:
                   window.destroy() if event.keyval == Gdk.KEY_Escape else False)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.set_name('usage-card')
    window.add(card)
    header = Gtk.Box(spacing=8)
    title = Gtk.Label(label='Использование приложений', xalign=0)
    title.set_name('usage-title')
    header.pack_start(title, True, True, 0)
    close = Gtk.Button.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU)
    close.set_name('usage-close')
    close.set_tooltip_text('Закрыть · Esc')
    close.connect('clicked', lambda *_: window.destroy())
    header.pack_end(close, False, False, 0)
    card.pack_start(header, False, False, 0)

    tabs = Gtk.Box(spacing=6, homogeneous=True)
    today = Gtk.RadioButton.new_with_label_from_widget(None, 'Сегодня')
    all_time = Gtk.RadioButton.new_with_label_from_widget(today, 'За всё время')
    today.set_mode(False)
    all_time.set_mode(False)
    today.set_active(True)
    tabs.pack_start(today, True, True, 0)
    tabs.pack_start(all_time, True, True, 0)
    card.pack_start(tabs, False, False, 0)

    summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    total = Gtk.Label(xalign=0)
    total.set_name('usage-total')
    caption = Gtk.Label(xalign=0)
    caption.get_style_context().add_class('muted')
    summary.pack_start(total, False, False, 0)
    summary.pack_start(caption, False, False, 0)
    card.pack_start(summary, False, False, 0)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(100)
    scroll.set_max_content_height(340)
    scroll.set_propagate_natural_height(True)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    scroll.add(rows)
    card.pack_start(scroll, True, True, 0)

    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    footer.set_name('usage-footer')
    state_label = Gtk.Label(xalign=0)
    state_label.get_style_context().add_class('muted')
    hint = Gtk.Label(label='Считается время активного окна.\nПауза после 3 минут бездействия.', xalign=0)
    hint.get_style_context().add_class('muted')
    footer.pack_start(state_label, False, False, 0)
    footer.pack_start(hint, False, False, 0)
    card.pack_start(footer, False, False, 0)
    current_period = ['today']
    row_widgets = {}

    def refresh():
        try:
            data = report(current_period[0])
        except sqlite3.Error:
            state_label.set_text('Не удалось прочитать статистику. Повторяем…')
            return True
        total.set_text(duration(data['total']))
        if current_period[0] == 'today':
            period_caption = datetime.now(LOCAL_TZ).strftime('%d.%m.%Y')
        else:
            started = datetime.fromisoformat(data['started']) if data['started'] else None
            period_caption = 'С ' + started.strftime('%d.%m.%Y') if started else 'С начала учёта'
        caption.set_text(f"{period_caption} · приложений: {len(data['apps'])}")
        keys = {entry['app'] for entry in data['apps']}
        if not keys:
            keys.add('__empty__')
        for key in list(row_widgets):
            if key not in keys:
                rows.remove(row_widgets.pop(key)[0])
        if not data['apps'] and '__empty__' not in row_widgets:
            empty = Gtk.Label(label='Статистика появится через несколько секунд.\nПользуйся приложениями как обычно.', xalign=0)
            empty.set_name('usage-empty')
            rows.pack_start(empty, False, False, 0)
            row_widgets['__empty__'] = (empty,)
        for position, entry in enumerate(data['apps']):
            key = entry['app']
            if key not in row_widgets:
                row = Gtk.Box(spacing=12)
                name, icon = catalog.lookup(key)
                image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DND) if icon else \
                    Gtk.Image.new_from_icon_name('application-x-executable', Gtk.IconSize.DND)
                image.set_pixel_size(28)
                row.pack_start(image, False, False, 0)
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                line = Gtk.Box(spacing=10)
                label = Gtk.Label(label=name, xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(25)
                label.set_tooltip_text(key)
                line.pack_start(label, True, True, 0)
                value = Gtk.Label(xalign=1)
                value.get_style_context().add_class('duration')
                line.pack_end(value, False, False, 0)
                bar = Gtk.ProgressBar()
                content.pack_start(line, False, False, 0)
                content.pack_start(bar, False, False, 0)
                row.pack_start(content, True, True, 0)
                rows.pack_start(row, False, False, 0)
                row_widgets[key] = (row, value, bar)
            row, value, bar = row_widgets[key]
            value.set_text(duration(entry['seconds']))
            fraction = entry['seconds'] / data['total'] if data['total'] else 0
            bar.set_fraction(min(1, max(0, fraction)))
            bar.set_tooltip_text(f'{fraction:.0%} от общего времени')
            rows.reorder_child(row, position)
        reason = runtime_status()
        state_label.set_text({'tracking': '● Учёт идёт', 'idle': 'Ⅱ Пауза · нет активности',
                              'locked': 'Ⅱ Пауза · экран заблокирован',
                              'no-window': 'Нет активного окна',
                              'offline': 'Счётчик не запущен'}[reason])
        rows.show_all()
        return True

    def select(button, period):
        if not button.get_active():
            return
        current_period[0] = period
        refresh()

    today.connect('toggled', select, 'today')
    all_time.connect('toggled', select, 'all')
    refresh()
    GLib.timeout_add_seconds(5, refresh)
    # Map the dismiss surfaces first, so the card is above them on the overlay.
    for area in dismiss_areas:
        area.show_all()
    window.show_all()
    Gtk.main()
    window_lock.close()


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['daemon', 'status', 'menu', 'idle', 'resume', 'report'])
    parser.add_argument('--period', choices=['today', 'all'], default='today')
    args = parser.parse_args()
    if args.command == 'report':
        print(json.dumps(report(args.period), ensure_ascii=False, indent=2))
    elif args.command in ('idle', 'resume'):
        mark_idle(args.command == 'idle')
    else:
        globals()[args.command]()
