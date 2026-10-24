import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import test_app_export as support
from pubnexus import store


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'PUBNEXUS_STORE': str(self.root / 'cache')})
        env.start()
        self.addCleanup(env.stop)
        override = patch.dict(os.environ)
        override.start()
        self.addCleanup(override.stop)
        os.environ.pop('PUBNEXUS_OUTPUT', None)
        config = patch.object(support.app_module.utils, 'load_config', return_value={})
        config.start()
        self.addCleanup(config.stop)
        documents = patch('pubnexus.output_location.documents_directory', return_value=self.root / 'Documents')
        documents.start()
        self.addCleanup(documents.stop)

    def test_output_and_auto_update_survive_app_restart(self):
        from pubnexus.settings import Preferences
        prefs = Preferences()
        destination = self.root / '새 저장 위치'
        prefs.set_output_dir(destination)
        prefs.set_auto_update(False)
        app = support.app_module.App()
        self.assertEqual(app.output_dir, destination.resolve())
        self.assertFalse(support.app_module.Api(app).settings()['auto_update'])
        self.assertTrue((store.root() / 'settings.json').is_file())

    def test_theme_survives_restart_without_clobbering_other_preferences_while_busy(self):
        app = support.app_module.App()
        api = support.app_module.Api(app)
        self.assertEqual(api.settings()['theme'], 'light')
        destination = self.root / 'keep-output'
        app.preferences.set_output_dir(destination)
        app.preferences.set_auto_update(False)
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app.push = Mock()
        self.assertTrue(api.set_theme('dark')['ok'])
        restored = support.app_module.App()
        state = support.app_module.Api(restored).state()['settings']
        self.assertEqual(state['theme'], 'dark')
        self.assertEqual(restored.output_dir, destination.resolve())
        self.assertFalse(state['auto_update'])
        restored.preferences.set_auto_update(True)
        self.assertTrue(support.app_module.Api(restored).set_theme('light')['ok'])
        again = support.app_module.Api(support.app_module.App()).settings()
        self.assertEqual(again['theme'], 'light')
        self.assertTrue(again['auto_update'])
        self.assertEqual(again['output_dir'], str(destination.resolve()))
        self.assertTrue(any(args[0] == 'settings' and args[1]['theme'] == 'dark'
                            for args, _ in app.push.call_args_list))

    def test_invalid_theme_does_not_mutate_preferences_or_emit_success(self):
        app = support.app_module.App()
        api = support.app_module.Api(app)
        self.assertTrue(api.set_theme('dark')['ok'])
        before = (store.root() / 'settings.json').read_bytes()
        app.push = Mock()
        for invalid in ('system', 'DARK', '', None, True, 1, [], {}):
            with self.subTest(theme=invalid):
                self.assertFalse(api.set_theme(invalid)['ok'])
                self.assertEqual(api.settings()['theme'], 'dark')
                self.assertEqual((store.root() / 'settings.json').read_bytes(), before)
        app.push.assert_not_called()

    def test_theme_write_failure_preserves_previous_value_and_other_preferences(self):
        app = support.app_module.App()
        api = support.app_module.Api(app)
        self.assertTrue(api.set_theme('dark')['ok'])
        before = (store.root() / 'settings.json').read_bytes()
        app.push = Mock()
        with patch('pubnexus.settings.utils.write_json', side_effect=OSError('disk unavailable')):
            result = api.set_theme('light')
        self.assertFalse(result['ok'])
        self.assertEqual(api.settings()['theme'], 'dark')
        self.assertEqual((store.root() / 'settings.json').read_bytes(), before)
        app.push.assert_not_called()

    def test_missing_or_invalid_saved_theme_defaults_to_light(self):
        from pubnexus.settings import Preferences
        store.root().mkdir(parents=True, exist_ok=True)
        for theme in (None, True, 12, [], {}, 'system'):
            with self.subTest(theme=theme):
                support.app_module.utils.write_json(store.root() / 'settings.json',
                    {'theme': theme, 'auto_update': False})
                prefs = Preferences()
                self.assertEqual(prefs.theme, 'light')
                self.assertFalse(prefs.auto_update)

    def test_change_only_affects_future_exports_and_reset_is_persisted(self):
        app = support.app_module.App()
        app.exported['previous'] = {'markdown': 'old/result.md'}
        app.push = Mock()
        destination = self.root / 'chosen'
        app.change_output_dir(destination)
        self.assertEqual(app.exported['previous']['markdown'], 'old/result.md')
        self.assertTrue(any(args[0] == 'settings' for args, _ in app.push.call_args_list))
        result = support.app_module.Api(app).reset_output_folder()
        self.assertTrue(result['ok'])
        self.assertEqual(support.app_module.App().output_dir, app.output_dir)
        self.assertNotEqual(app.output_dir, destination)

    def test_failed_write_or_busy_does_not_change_destination(self):
        app = support.app_module.App()
        original = app.output_dir
        with patch('pubnexus.settings.utils.write_json', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                app.change_output_dir(self.root / 'failed')
        self.assertEqual(app.output_dir, original)
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        with self.assertRaises(RuntimeError):
            app.change_output_dir(self.root / 'while-running')
        self.assertEqual(app.output_dir, original)

    def test_explicit_output_override_also_isolates_reset(self):
        from pubnexus.settings import Preferences
        isolated = self.root / 'isolated-reset'
        with patch.dict(os.environ, {'PUBNEXUS_OUTPUT': str(isolated)}):
            prefs = Preferences()
            self.assertEqual(prefs.set_output_dir(None), isolated.resolve())
            self.assertEqual(prefs.output_dir, isolated.resolve())
            self.assertFalse((self.root / 'Documents').exists())
    def test_picker_persists_and_cancel_does_not_change(self):
        app = support.app_module.App()
        app.window = Mock()
        app.push = Mock()
        api = support.app_module.Api(app)
        app.window.create_file_dialog.return_value = [str(self.root / 'picked')]
        self.assertTrue(api.choose_output_folder()['ok'])
        chosen = app.output_dir
        app.window.create_file_dialog.return_value = None
        self.assertTrue(api.choose_output_folder()['cancelled'])
        self.assertEqual(app.output_dir, chosen)
        self.assertEqual(support.app_module.App().output_dir, chosen)

    def test_corrupt_settings_default_and_invalid_values(self):
        from pubnexus.settings import Preferences
        store.root().mkdir(parents=True)
        (store.root() / 'settings.json').write_text('{broken', encoding='utf-8')
        prefs = Preferences()
        self.assertTrue(prefs.auto_update)
        with self.assertRaises(ValueError):
            prefs.set_output_dir(Path('relative'))
        with self.assertRaises(ValueError):
            prefs.set_auto_update('false')
        file = self.root / 'file'
        file.write_text('original', encoding='utf-8')
        with self.assertRaises(OSError):
            prefs.set_output_dir(file)
        self.assertEqual(file.read_text(encoding='utf-8'), 'original')

    def test_update_install_waits_for_idle_exit_and_respects_opt_out(self):
        app = support.app_module.App()
        app.push = Mock()
        app.window = Mock()
        app._updater = Mock()
        app._updater.snapshot.return_value = {'status': 'ready', 'version': '1.3.0'}
        app._updater.install.return_value = {'ok': True}
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        self.assertFalse(app.request_close())
        app.apply_update_on_exit()
        app._updater.install.assert_not_called()
        app.worker.is_alive.return_value = False
        self.assertTrue(app.request_close())
        app.apply_update_on_exit()
        app._updater.install.assert_called_once_with(restart=False)
        app._updater.install.reset_mock()
        app.preferences.set_auto_update(False)
        app.apply_update_on_exit()
        app._updater.install.assert_not_called()

    def test_manual_install_closes_only_idle_frozen_app_and_requests_restart(self):
        app = support.app_module.App()
        app.window = Mock()
        app.push = Mock()
        app._updater = Mock()
        app._updater.snapshot.return_value = {'status': 'ready', 'version': '1.3.0'}
        app._updater.install.return_value = {'ok': True}
        api = support.app_module.Api(app)
        self.assertFalse(api.install_update()['ok'])
        app.window.destroy.assert_not_called()
        with patch.object(support.app_module.sys, 'frozen', True, create=True):
            self.assertTrue(api.install_update()['ok'])
        app.window.destroy.assert_called_once()
        app._updater.install.assert_not_called()
        app.apply_update_on_exit()
        app._updater.install.assert_called_once_with(restart=True)

    def test_manual_install_does_not_hold_lock_across_gui_close_callback(self):
        app = support.app_module.App()
        app.window = Mock()
        app.push = Mock()
        app._updater = Mock()
        app._updater.snapshot.return_value = {'status': 'ready'}
        finished = threading.Event()
        callbacks = []
        def destroy():
            callback = threading.Thread(target=lambda: (app.request_close(), finished.set()), daemon=True)
            callbacks.append(callback)
            callback.start()
            self.assertTrue(finished.wait(0.5), 'GUI close callback must not wait for a bridge-owned lock')
        app.window.destroy.side_effect = destroy
        with patch.object(support.app_module.sys, 'frozen', True, create=True):
            result = support.app_module.Api(app).install_update()
        for callback in callbacks:
            callback.join(1)
        self.assertTrue(result['ok'], result)

    def test_auto_check_starts_only_for_enabled_normal_frozen_app(self):
        app = support.app_module.App()
        with patch.object(app, 'check_update_async') as check:
            app.start_auto_update(hidden=False)
            check.assert_not_called()
            with patch.object(support.app_module.sys, 'frozen', True, create=True):
                app.start_auto_update(hidden=True)
                check.assert_not_called()
                app.start_auto_update(hidden=False)
                check.assert_called_once()
                check.reset_mock()
                app.preferences.set_auto_update(False)
                app.start_auto_update(hidden=False)
                check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
