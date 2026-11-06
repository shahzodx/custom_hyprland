import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'todo', Path(__file__).resolve().parents[1] / 'scripts/todo.py')
todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo)


class TodoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        directory = patch.object(todo, 'STATE_DIR', Path(self.directory.name))
        directory.start()
        self.addCleanup(directory.stop)
        self.store = todo.Tasks()
        self.addCleanup(self.store.close)

    def test_unicode_tasks_and_completion_persist_after_reopening(self):
        task_id = self.store.add('  Прочитать книгу 📚  ')
        self.store.complete(task_id, True)
        reopened = todo.Tasks()
        self.addCleanup(reopened.close)
        task = reopened.list()[0]
        self.assertEqual((task['id'], task['text'], task['done']),
                         (task_id, 'Прочитать книгу 📚', 1))
        reopened.complete(task_id, False)
        self.assertEqual(self.store.list()[0]['done'], 0)

    def test_edit_retains_completion_and_creation_date(self):
        task_id = self.store.add('Исходная задача')
        before = self.store.list()[0]
        self.store.complete(task_id, True)
        self.store.edit(task_id, 'Новая формулировка')
        after = self.store.list()[0]
        self.assertEqual(after['text'], 'Новая формулировка')
        self.assertEqual(after['created'], before['created'])
        self.assertEqual(after['done'], 1)

    def test_undo_delete_does_not_replace_new_task(self):
        task_id = self.store.add('Первая задача')
        self.store.complete(task_id, True)
        deleted = self.store.delete(task_id)
        other_id = self.store.add('Вторая задача')
        self.assertNotEqual(task_id, other_id)
        self.store.restore(deleted)
        data = {task['id']: task for task in self.store.list()}
        self.assertEqual(data[task_id]['text'], 'Первая задача')
        self.assertEqual(data[task_id]['done'], 1)
        self.assertEqual(data[other_id]['text'], 'Вторая задача')

    def test_invalid_add_and_edit_leave_saved_tasks_intact(self):
        task_id = self.store.add('Сохранённая задача')
        for text in ('', '  \t\n', 'x' * 501):
            with self.assertRaises(ValueError):
                self.store.add(text)
            with self.assertRaises(ValueError):
                self.store.edit(task_id, text)
        self.assertEqual([task['text'] for task in self.store.list()], ['Сохранённая задача'])

    def test_two_connections_do_not_lose_each_others_changes(self):
        other = todo.Tasks()
        self.addCleanup(other.close)
        first_id = self.store.add('Первая')
        second_id = other.add('Вторая')
        self.store.complete(first_id, True)
        other.edit(second_id, 'Изменённая вторая')
        data = {task['id']: task for task in self.store.list()}
        self.assertEqual(len(data), 2)
        self.assertEqual(data[first_id]['done'], 1)
        self.assertEqual(data[second_id]['text'], 'Изменённая вторая')

    def test_tooltip_displays_literal_special_characters_safely(self):
        self.store.add('Проверить x < 3 & y > 1')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            todo.status()
        status = json.loads(output.getvalue())
        self.assertIn('x &lt; 3 &amp; y &gt; 1', status['tooltip'])
        self.assertEqual(status['class'], 'has-tasks')


if __name__ == '__main__':
    unittest.main()
