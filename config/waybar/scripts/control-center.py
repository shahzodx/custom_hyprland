#!/usr/bin/env python3
"""Small, wallpaper-themed Wayland control panel; uses installed GTK3 bindings."""
import concurrent.futures
import cairo
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango

BASE = Path(__file__).resolve().parent.parent
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=3)


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def snapshot():
    return {
        'volume': command('wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'),
        'brightness': command('brightnessctl', '-m'),
        'wifi': command('nmcli', '-t', '-f', 'TYPE,STATE,CONNECTION', 'device', 'status'),
        'radio': command('nmcli', 'radio', 'wifi'),
        'bluetooth': command('bluetoothctl', 'show'),
        'focus': command('dunstctl', 'is-paused'),
        'players': command('playerctl', '-l'),
    }


def label(text='', style=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def box(vertical=False, spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)


def panel_icon(name):
    image = Gtk.Image.new_from_gicon(
        Gio.FileIcon.new(Gio.File.new_for_path(str(BASE / 'icons/control-center' / (name.removesuffix('-symbolic') + '.svg')))),
        Gtk.IconSize.BUTTON,
    )
    image.set_pixel_size(16)
    image.set_size_request(16, 16)
    image.set_halign(Gtk.Align.CENTER)
    image.set_valign(Gtk.Align.CENTER)
    return image


def clear_window(_, context):
    # Clear alpha before every frame so rounded corners cannot retain old pixels.
    context.save()
    context.set_operator(cairo.OPERATOR_SOURCE)
    context.set_source_rgba(0, 0, 0, 0)
    context.paint()
    context.restore()
    return False


def button(icon, tooltip, callback):
    widget = Gtk.Button()
    widget.set_image(panel_icon(icon))
    widget.set_tooltip_text(tooltip)
    widget.connect('clicked', lambda *_: callback())
    return widget


class Panel(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='org.joy.ControlCenter', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None
        self.busy = False
        self.updating = False
        self.pending = {}
        self.player = None
        self.art_url = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.css = Gtk.CssProvider()
        self.css.load_from_path(str(BASE / 'control-center.css'))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.window:
            self.window.destroy()
            return
        self.window = Gtk.ApplicationWindow(application=self)
        window = self.window
        window.set_name('control-center')
        window.set_decorated(False)
        window.set_app_paintable(True)
        visual = window.get_screen().get_rgba_visual()
        if visual:
            window.set_visual(visual)
        window.connect('draw', clear_window)
        window.set_title('Центр управления')
        window.set_default_size(320, -1)
        window.set_resizable(False)
        window.connect('destroy', self.closed)
        window.connect('key-press-event', self.keypress)
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, 'joy-control-center')
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(window, edge, True)
        GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 40)
        GtkLayerShell.set_margin(window, GtkLayerShell.Edge.RIGHT, 12)

        surface = box(True, 0)
        surface.set_name('panel-surface')
        surface.set_margin_start(1)
        surface.set_margin_end(1)
        surface.set_margin_top(1)
        surface.set_margin_bottom(1)
        window.add(surface)
        content = box(True, 12)
        content.set_border_width(14)
        surface.pack_start(content, True, True, 0)
        header = box()
        header.pack_start(label('Настройки', 'heading'), True, True, 0)
        header.pack_end(button('window-close-symbolic', 'Закрыть · Esc', window.destroy), False, False, 0)
        content.pack_start(header, False, False, 0)

        tiles = box(False, 8)
        self.wifi = self.tile('network-wireless-symbolic', 'Wi-Fi', lambda: self.launch('nm-connection-editor'))
        self.bt = self.tile('bluetooth-symbolic', 'Bluetooth', self.toggle_bluetooth)
        self.focus = self.tile('notifications-disabled-symbolic', 'Тишина', lambda: self.act('dunstctl', 'set-paused', 'toggle'))
        for tile in (self.wifi, self.bt, self.focus):
            tiles.pack_start(tile, True, True, 0)
        content.pack_start(tiles, False, False, 0)

        controls = box(True, 8)
        controls.get_style_context().add_class('card')
        self.volume = self.slider(controls, 'audio-volume-high-symbolic', 'Громкость', 'volume', lambda: self.act('wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', 'toggle'))
        self.brightness = self.slider(controls, 'display-brightness-symbolic', 'Яркость', 'brightness')
        content.pack_start(controls, False, False, 0)

        media = box(True, 4)
        media.get_style_context().add_class('media')
        top = box(False, 10)
        self.cover = Gtk.Image.new_from_icon_name('audio-x-generic-symbolic', Gtk.IconSize.DIALOG)
        self.cover.set_pixel_size(36)
        top.pack_start(self.cover, False, False, 0)
        track = box(True, 4)
        self.title = label('Ничего не играет', 'track')
        self.title.set_max_width_chars(26)
        self.artist = label('Открой музыку или видео', 'muted')
        self.artist.set_max_width_chars(26)
        self.players = Gtk.ComboBoxText()
        self.players.connect('changed', self.player_changed)
        track.pack_start(self.title, False, False, 0)
        track.pack_start(self.artist, False, False, 0)
        track.pack_start(self.players, False, False, 0)
        top.pack_start(track, True, True, 0)
        media.pack_start(top, False, False, 0)
        transport = box(False, 8)
        transport.set_halign(Gtk.Align.CENTER)
        self.media_buttons = []
        for icon, tip, action in [('media-skip-backward-symbolic', 'Предыдущий', 'previous'), ('media-playback-start-symbolic', 'Пауза / продолжить', 'play-pause'), ('media-skip-forward-symbolic', 'Следующий', 'next')]:
            widget = button(icon, tip, lambda a=action: self.media_action(a))
            transport.pack_start(widget, False, False, 0)
            self.media_buttons.append(widget)
        media.pack_start(transport, False, False, 0)
        content.pack_start(media, False, False, 0)

        footer = box()
        footer.get_style_context().add_class('footer')
        for icon, tip, args in [
            ('preferences-system-symbolic', 'Аудиоустройства', ('pavucontrol',)),
            ('alarm-symbolic', 'Таймер', (str(BASE / 'scripts/work-timer.py'), 'menu')),
            ('preferences-desktop-wallpaper-symbolic', 'Сменить обои', (str(BASE / 'scripts/wallpaper-picker.sh'),)),
            ('system-lock-screen-symbolic', 'Заблокировать', ('hyprlock',)),
        ]:
            footer.pack_start(button(icon, tip, lambda a=args: self.launch(*a)), True, True, 0)
        content.pack_start(footer, False, False, 0)
        self.status = label('', 'muted')
        self.status.set_no_show_all(True)
        content.pack_start(self.status, False, False, 0)
        window.show_all()
        self.player_names = []
        self.refresh()
        self.timer = GLib.timeout_add_seconds(2, self.refresh)

    def closed(self, *_):
        self.window = None
        if hasattr(self, 'timer'):
            GLib.source_remove(self.timer)
        for source in self.pending.values():
            GLib.source_remove(source)
        self.pending.clear()

    def keypress(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def tile(self, icon, text, callback):
        widget = Gtk.Button()
        widget.get_style_context().add_class('tile')
        inner = box(False, 6)
        inner.pack_start(panel_icon(icon), False, False, 0)
        caption = Gtk.Label(label=text)
        caption.get_style_context().add_class('tile-caption')
        inner.pack_start(caption, False, False, 0)
        widget.add(inner)
        widget.set_tooltip_text('Не беспокоить' if text == 'Тишина' else text)
        widget.connect('clicked', lambda *_: callback())
        return widget

    def slider(self, parent, icon, text, name, callback=None):
        row = box(False, 10)
        if callback:
            icon_widget = button(icon, 'Выключить / включить звук', callback)
            icon_widget.set_size_request(28, 28)
            row.pack_start(icon_widget, False, False, 0)
        else:
            icon_widget = Gtk.Box()
            icon_widget.set_size_request(28, 28)
            icon_widget.set_halign(Gtk.Align.CENTER)
            icon_widget.pack_start(panel_icon(icon), True, True, 0)
            row.pack_start(icon_widget, False, False, 0)
        column = box(True, 4)
        row.set_tooltip_text(text)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1 if name == 'brightness' else 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.connect('value-changed', lambda widget: self.schedule(name, widget))
        column.pack_start(scale, False, False, 0)
        row.pack_start(column, True, True, 0)
        value = label('—', 'percentage')
        value.set_width_chars(5)
        row.pack_start(value, False, False, 0)
        parent.pack_start(row, False, False, 0)
        setattr(self, name + '_label', value)
        return scale

    def schedule(self, name, scale):
        if self.updating:
            return
        getattr(self, name + '_label').set_text(f'{scale.get_value():.0f}%')
        if name in self.pending:
            GLib.source_remove(self.pending[name])
        self.pending[name] = GLib.timeout_add(150, self.set_level, name, round(scale.get_value()))

    def set_level(self, name, value):
        self.pending.pop(name, None)
        if name == 'volume':
            self.act('wpctl', 'set-volume', '-l', '1', '@DEFAULT_AUDIO_SINK@', f'{value}%')
        else:
            self.act('brightnessctl', 'set', f'{value}%')
        return False

    def act(self, *args):
        def work():
            result = command(*args)
            GLib.idle_add(self.action_done, result is not None)
        POOL.submit(work)

    def action_done(self, success):
        if self.window:
            self.status.set_text('' if success else 'Действие недоступно — проверь устройство')
            self.status.set_visible(not success)
            self.refresh()
        return False

    def launch(self, *args):
        try:
            subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.window.destroy()
        except OSError:
            self.status.set_text('Приложение не установлено')
            self.status.show()

    def toggle_bluetooth(self):
        self.act('bluetoothctl', 'power', 'off' if self.bt.get_style_context().has_class('active') else 'on')

    def media_action(self, action):
        if self.player:
            self.act('playerctl', '-p', self.player, action)

    def player_changed(self, widget):
        if not self.updating:
            self.player = widget.get_active_text()
            self.refresh()

    def refresh(self):
        if not self.window:
            return False
        if self.busy:
            return True
        self.busy = True
        selected = self.player
        def work():
            data = snapshot()
            names = (data['players'] or '').splitlines()
            player = selected if selected in names else (names[0] if names else None)
            data['player'] = player
            if player:
                for key, field in [('title', 'xesam:title'), ('artist', 'xesam:artist'), ('art', 'mpris:artUrl')]:
                    data[key] = command('playerctl', '-p', player, 'metadata', field)
                data['playing'] = command('playerctl', '-p', player, 'status') == 'Playing'
            GLib.idle_add(self.render, data)
        POOL.submit(work)
        return True

    def render(self, data):
        self.busy = False
        if not self.window:
            return False
        self.updating = True
        try:
            for tile, active in [(self.wifi, data['radio'] == 'enabled'), (self.bt, 'Powered: yes' in (data['bluetooth'] or '')), (self.focus, data['focus'] == 'true')]:
                context = tile.get_style_context()
                (context.add_class if active else context.remove_class)('active')
            wifi = next((line.split(':', 2)[-1] for line in (data['wifi'] or '').splitlines() if line.startswith('wifi:connected:')), None)
            self.wifi.set_tooltip_text(wifi or 'Настроить подключения Wi-Fi')
            self.bt.set_sensitive(data['bluetooth'] is not None and 'Controller ' in data['bluetooth'])
            volume = re.search(r'Volume: ([\d.]+)', data['volume'] or '')
            brightness = (data['brightness'] or '').split(',')
            for name, value in [('volume', min(100, round(float(volume[1]) * 100)) if volume else None), ('brightness', int(brightness[3].rstrip('%')) if len(brightness) > 3 else None)]:
                scale = getattr(self, name)
                scale.set_sensitive(value is not None)
                if name not in self.pending and not scale.has_grab():
                    scale.set_value(value or 0)
                    getattr(self, name + '_label').set_text(f'{value}%' if value is not None else '—')
            if '[MUTED]' in (data['volume'] or ''):
                self.volume_label.set_text('выкл.')
            names = (data['players'] or '').splitlines()
            self.player = data['player']
            if names != self.player_names:
                self.players.remove_all()
                for name in names:
                    self.players.append_text(name)
                self.player_names = names
            if self.player:
                self.players.set_active(names.index(self.player))
            self.players.set_visible(len(names) > 1)
            self.title.set_text(data.get('title') or ('Без названия' if self.player else 'Ничего не играет'))
            self.title.set_tooltip_text(data.get('title'))
            self.artist.set_text(data.get('artist') or self.player or 'Открой музыку или видео')
            for widget in self.media_buttons:
                widget.set_sensitive(bool(self.player))
            self.media_buttons[1].set_image(panel_icon('media-playback-pause-symbolic' if data.get('playing') else 'media-playback-start-symbolic'))
            art = data.get('art') or ''
            if art != self.art_url:
                self.art_url = art
                self.cover.set_from_icon_name('audio-x-generic-symbolic', Gtk.IconSize.DIALOG)
                if art:
                    POOL.submit(self.load_cover, art)
        finally:
            self.updating = False
        return False

    def load_cover(self, url):
        try:
            parsed = urlparse(url)
            if parsed.scheme == 'file':
                raw = Path(unquote(parsed.path)).read_bytes()
            elif parsed.scheme == 'https':
                with urlopen(url, timeout=4) as response:
                    raw = response.read(5 * 1024 * 1024 + 1)
            else:
                return
            if len(raw) > 5 * 1024 * 1024:
                return
            loader = GdkPixbuf.PixbufLoader()
            loader.set_size(36, 36)
            loader.write(raw)
            loader.close()
            GLib.idle_add(self.show_cover, url, loader.get_pixbuf())
        except Exception:
            pass  # Missing/offline artwork keeps the music icon.

    def show_cover(self, url, pixbuf):
        if self.window and self.art_url == url and pixbuf:
            self.cover.set_from_pixbuf(pixbuf)
        return False


if __name__ == '__main__':
    try:
        Panel().run(None)
    finally:
        POOL.shutdown(wait=False, cancel_futures=True)
