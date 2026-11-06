#!/usr/bin/env python3
"""A persistent local checklist and an icon-only Waybar launcher."""

import argparse
import fcntl
import html
import json
import os
from pathlib import Path
import sqlite3
import time

BASE = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get('WAYBAR_TODO_STATE_DIR', BASE / '.todo'))
MAX_LENGTH = 500


class Tasks:
    def __init__(self):
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        STATE_DIR.chmod(0o700)
        self.db = sqlite3.connect(STATE_DIR / 'tasks.sqlite3', timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0, 1)),
            created REAL NOT NULL, updated REAL NOT NULL
        )''')
        self.db.commit()

    def close(self):
        self.db.close()

    @staticmethod
    def validate(text):
        text = text.strip()
        if not text:
            raise ValueError('Введите текст задачи')
        if len(text) > MAX_LENGTH:
            raise ValueError(f'Не больше {MAX_LENGTH} символов')
        return text

    def list(self):
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM tasks ORDER BY done, '
            'CASE WHEN done = 1 THEN updated END DESC, created DESC, id DESC')]

    def add(self, text):
        text = self.validate(text)
        now = time.time()
        with self.db:
            cursor = self.db.execute('INSERT INTO tasks(text, created, updated) VALUES (?, ?, ?)',
                                     (text, now, now))
        return cursor.lastrowid

    def edit(self, task_id, text):
        text = self.validate(text)
        with self.db:
            self.db.execute('UPDATE tasks SET text = ?, updated = ? WHERE id = ?',
                            (text, time.time(), task_id))

    def complete(self, task_id, done):
        with self.db:
            self.db.execute('UPDATE tasks SET done = ?, updated = ? WHERE id = ?',
                            (int(bool(done)), time.time(), task_id))

    def delete(self, task_id):
        with self.db:
            task = self.db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
            self.db.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        return dict(task) if task else None

    def restore(self, task):
        with self.db:
            self.db.execute('INSERT INTO tasks(id, text, done, created, updated) VALUES (?, ?, ?, ?, ?)',
                            tuple(task[key] for key in ('id', 'text', 'done', 'created', 'updated')))


def status():
    tasks = Tasks()
    try:
        data = tasks.list()
    finally:
        tasks.close()
    pending = [task for task in data if not task['done']]
    lines = ['Список дел', f'В работе: {len(pending)} · Готово: {len(data) - len(pending)}']
    if pending:
        lines.append('')
        for task in pending[:6]:
            text = task['text']
            lines.append('☐ ' + (text if len(text) <= 72 else text[:69] + '…'))
        if len(pending) > 6:
            lines.append(f'Ещё задач: {len(pending) - 6}')
    lines.extend(['', 'Нажмите, чтобы открыть список'])
    print(json.dumps({'text': ' ', 'tooltip': html.escape('\n'.join(lines)),
                      'class': 'has-tasks' if pending else 'empty'}, ensure_ascii=False))


def menu():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import Gtk, Gdk, GLib, GtkLayerShell, Pango

    store = Tasks()
    window_lock = (STATE_DIR / 'window.lock').open('a')
    try:
        fcntl.flock(window_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        store.close()
        window_lock.close()
        return

    provider = Gtk.CssProvider()
    provider.load_from_path(str(BASE / 'todo.css'))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Gtk.Window(title='Список дел')
    window.set_name('todo-window')
    window.set_default_size(440, -1)
    window.set_resizable(False)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, 'waybar-todo')
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, 36)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.RIGHT, 12)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
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
        area.set_name('todo-dismiss-area')
        area.set_title('Закрыть список дел')
        area.set_decorated(False)
        area.set_app_paintable(True)
        visual = area.get_screen().get_rgba_visual()
        if visual:
            area.set_visual(visual)
        GtkLayerShell.init_for_window(area)
        GtkLayerShell.set_namespace(area, 'waybar-todo-dismiss')
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
    window.connect_after('key-press-event', lambda _, event:
                   dismiss() if event.keyval == Gdk.KEY_Escape else False)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    card.set_name('todo-card')
    window.add(card)
    header = Gtk.Box(spacing=12)
    heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    title = Gtk.Label(label='Список дел', xalign=0)
    title.set_name('todo-title')
    summary = Gtk.Label(xalign=0)
    summary.get_style_context().add_class('muted')
    heading.pack_start(title, False, False, 0)
    heading.pack_start(summary, False, False, 0)
    header.pack_start(heading, True, True, 0)
    close = Gtk.Button.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU)
    close.set_name('todo-close')
    close.set_tooltip_text('Закрыть · Esc')
    close.connect('clicked', dismiss)
    header.pack_end(close, False, False, 0)
    card.pack_start(header, False, False, 0)

    composer = Gtk.Box(spacing=6)
    entry = Gtk.Entry()
    entry.set_name('todo-input')
    entry.set_placeholder_text('Что нужно сделать?')
    entry.set_max_length(MAX_LENGTH)
    entry.set_width_chars(20)
    composer.pack_start(entry, True, True, 0)
    add = Gtk.Button.new_from_icon_name('list-add-symbolic', Gtk.IconSize.MENU)
    add.set_name('todo-add')
    add.set_tooltip_text('Добавить · Enter')
    add.set_sensitive(False)
    composer.pack_end(add, False, False, 0)
    card.pack_start(composer, False, False, 0)

    tabs = Gtk.Box(spacing=6, homogeneous=True)
    buttons = {}
    group = None
    for period, label in [('all', 'Все'), ('active', 'В работе'), ('done', 'Готово')]:
        button = Gtk.RadioButton.new_with_label_from_widget(group, label)
        button.set_mode(False)
        if group is None:
            group = button
        buttons[period] = button
        tabs.pack_start(button, True, True, 0)
    buttons['all'].set_active(True)
    card.pack_start(tabs, False, False, 0)
    current_filter = ['all']

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(120)
    scroll.set_max_content_height(320)
    scroll.set_propagate_natural_height(True)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    scroll.add(rows)
    card.pack_start(scroll, True, True, 0)

    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    footer.set_name('todo-footer')
    feedback = Gtk.Box(spacing=8)
    message = Gtk.Label(xalign=0)
    message.get_style_context().add_class('muted')
    feedback.pack_start(message, True, True, 0)
    undo = Gtk.Button(label='Вернуть')
    undo.set_name('todo-undo')
    undo.set_sensitive(False)
    feedback.pack_end(undo, False, False, 0)
    footer.pack_start(feedback, False, False, 0)
    hint = Gtk.Label(label='Enter — добавить · Esc — закрыть', xalign=0)
    hint.get_style_context().add_class('muted')
    footer.pack_start(hint, False, False, 0)
    card.pack_start(footer, False, False, 0)
    deleted = []

    def safely(action, success=''):
        try:
            action()
        except (sqlite3.Error, ValueError, OSError) as error:
            message.set_text('Не удалось сохранить задачу')
            message.set_tooltip_text(str(error))
            return False
        message.set_text(success)
        message.set_tooltip_text(None)
        return True

    def make_row(task):
        row = Gtk.Box(spacing=8)
        row.get_style_context().add_class('task-row')
        row.set_name(f"todo-task-{task['id']}")
        check = Gtk.CheckButton()
        check.set_active(bool(task['done']))
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text('Вернуть в работу' if task['done'] else 'Отметить выполненной')
        check.get_accessible().set_name(task['text'])
        row.pack_start(check, False, False, 0)
        content = Gtk.Stack()
        content.set_hexpand(True)
        label = Gtk.Label(xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(28)
        if task['done']:
            label.set_markup('<s>' + html.escape(task['text']) + '</s>')
            label.get_style_context().add_class('completed')
        else:
            label.set_text(task['text'])
        editor = Gtk.Entry()
        editor.set_max_length(MAX_LENGTH)
        editor.set_width_chars(12)
        editor.set_text(task['text'])
        content.add_named(label, 'label')
        content.add_named(editor, 'edit')
        content.set_visible_child_name('label')
        row.pack_start(content, True, True, 0)
        edit = Gtk.Button.new_from_icon_name('document-edit-symbolic', Gtk.IconSize.MENU)
        edit.get_style_context().add_class('task-action')
        edit.set_tooltip_text('Редактировать')
        row.pack_start(edit, False, False, 0)
        remove = Gtk.Button.new_from_icon_name('user-trash-symbolic', Gtk.IconSize.MENU)
        remove.get_style_context().add_class('task-action')
        remove.set_tooltip_text('Удалить задачу')
        row.pack_start(remove, False, False, 0)

        def finish_edit(*_):
            if safely(lambda: store.edit(task['id'], editor.get_text()), 'Изменения сохранены'):
                refresh()

        def edit_task(*_):
            if content.get_visible_child_name() == 'edit':
                finish_edit()
            else:
                content.set_visible_child_name('edit')
                edit.set_image(Gtk.Image.new_from_icon_name('object-select-symbolic', Gtk.IconSize.MENU))
                edit.set_tooltip_text('Сохранить · Enter')
                editor.grab_focus()
                editor.select_region(0, -1)

        def cancel_edit(_, event):
            if event.keyval != Gdk.KEY_Escape:
                return False
            editor.set_text(task['text'])
            content.set_visible_child_name('label')
            edit.set_image(Gtk.Image.new_from_icon_name('document-edit-symbolic', Gtk.IconSize.MENU))
            edit.set_tooltip_text('Редактировать')
            entry.grab_focus()
            return True

        def complete_task(widget):
            if safely(lambda: store.complete(task['id'], widget.get_active())):
                refresh()

        def remove_task(*_):
            def action():
                removed = store.delete(task['id'])
                if removed:
                    deleted.append(removed)
            if safely(action, 'Задача удалена'):
                undo.set_sensitive(bool(deleted))
                refresh()

        check.connect('toggled', complete_task)
        edit.connect('clicked', edit_task)
        editor.connect('activate', finish_edit)
        editor.connect('key-press-event', cancel_edit)
        remove.connect('clicked', remove_task)
        return row

    def refresh():
        try:
            data = store.list()
        except sqlite3.Error as error:
            message.set_text('Не удалось прочитать задачи')
            message.set_tooltip_text(str(error))
            return
        remaining = sum(not task['done'] for task in data)
        summary.set_text(f'В работе: {remaining} · Готово: {len(data) - remaining}')
        for child in rows.get_children():
            child.destroy()
        selected = [task for task in data if current_filter[0] == 'all' or
                    bool(task['done']) == (current_filter[0] == 'done')]
        if not selected:
            texts = {'all': 'Здесь пока пусто.\nДобавь первую задачу сверху.',
                     'active': 'Всё сделано. Можно выдохнуть.',
                     'done': 'Выполненные задачи появятся здесь.'}
            empty = Gtk.Label(label=texts[current_filter[0]], xalign=0)
            empty.set_name('todo-empty')
            rows.pack_start(empty, False, False, 0)
        for task in selected:
            rows.pack_start(make_row(task), False, False, 0)
        rows.show_all()

    def add_task(*_):
        if safely(lambda: store.add(entry.get_text()), 'Задача добавлена'):
            entry.set_text('')
            if current_filter[0] == 'done':
                buttons['active'].set_active(True)
            else:
                refresh()
            entry.grab_focus()

    def restore_task(*_):
        if deleted and safely(lambda: store.restore(deleted[-1]), 'Задача восстановлена'):
            deleted.pop()
            undo.set_sensitive(bool(deleted))
            refresh()

    def select(button, period):
        if button.get_active():
            current_filter[0] = period
            refresh()

    for period, button in buttons.items():
        button.connect('toggled', select, period)
    entry.connect('changed', lambda widget: add.set_sensitive(bool(widget.get_text().strip())))
    entry.connect('activate', add_task)
    add.connect('clicked', add_task)
    undo.connect('clicked', restore_task)
    refresh()
    for area in dismiss_areas:
        area.show_all()
    window.show_all()
    entry.grab_focus()
    try:
        Gtk.main()
    finally:
        store.close()
        window_lock.close()


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'menu'])
    args = parser.parse_args()
    globals()[args.command]()
