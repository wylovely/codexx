import hashlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codexx import installation, updates
from codexx.cli import main
from codexx.paths import executable_name
from codexx.version import VERSION

REAL_SCHEDULE = updates.schedule_background


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'CODEXX_HOME': str(self.root), 'CODEXX_REPOSITORY': ''})
        self.environment.start()
        self.output = patch('sys.stdout', new_callable=io.StringIO)
        self.capture = self.output.start()
        self.schedule_patch = patch('codexx.updates.schedule_background')
        self.scheduler = self.schedule_patch.start()
        self.addCleanup(self.schedule_patch.stop)

    def tearDown(self):
        self.output.stop()
        self.environment.stop()
        self.temp.cleanup()

    def configured(self):
        installation.write_json(self.root / 'config.json', {'repository': 'owner/repo'})
        os.environ.pop('CODEXX_REPOSITORY', None)

    def test_no_repository_means_no_network_or_files(self):
        with patch('codexx.releases.latest') as latest:
            self.assertEqual(updates.preflight(['--help']), (None, ['--help']))
            self.assertEqual(updates.preflight(['--codexx-check-update']), (0, []))
            latest.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_skip_bypasses_configured_remote(self):
        self.configured()
        with patch('codexx.releases.latest') as latest:
            self.assertEqual(updates.preflight(['--codexx-no-update', '-v']), (None, ['-v']))
            latest.assert_not_called()

    def test_update_arguments_are_not_consumed_from_prompt_or_values(self):
        self.assertEqual(updates.options(['--', '--codexx-update']), ({}, ['--', '--codexx-update']))
        self.assertEqual(updates.options(['-m', '--codexx-update']), ({}, ['-m', '--codexx-update']))

    def test_priority_even_before_help_or_invalid_codex_arguments(self):
        with patch('sys.argv', ['codexx', '--invalid-native-option', '--help']), \
                patch('codexx.updates.preflight', return_value=(0, [])) as first, \
                patch('codexx.cli.arguments') as parser, patch('codexx.cli.Bridge') as bridge:
            self.assertEqual(main(), 0)
            first.assert_called_once()
            parser.assert_not_called()
            bridge.assert_not_called()

    def test_check_only_does_not_install_or_prompt(self):
        self.configured()
        with patch('codexx.releases.latest', return_value={'tag_name': 'v99.0.0'}), \
                patch('builtins.input') as prompt, patch('codexx.updates.prepare_update') as install:
            self.assertEqual(updates.preflight(['--codexx-check-update', '-h']), (0, ['-h']))
            prompt.assert_not_called()
            install.assert_not_called()

    def test_cached_update_does_not_prompt_or_change_native_arguments(self):
        self.configured()
        installation.write_json(self.root / 'update-check.json',
            {'repository': 'owner/repo', 'latest_version': 'v99.0.0'})
        with patch('codexx.releases.latest') as latest, \
                patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='n'), \
                patch('codexx.updates.prepare_update') as install:
            self.assertEqual(updates.preflight(['resume', '--last']), (None, ['resume', '--last']))
            install.assert_not_called()
            latest.assert_not_called()

    def test_explicit_yes_schedules_update_and_exits_before_codex(self):
        self.configured()
        release = {'tag_name': 'v99.0.0'}
        with patch('codexx.releases.latest', return_value=release), \
                patch('codexx.updates.prepare_update') as install:
            self.assertEqual(updates.preflight(['--codexx-update', '--codexx-yes', '--version']), (0, ['--version']))
            install.assert_called_once_with('owner/repo', release)

    def test_network_failure_does_not_block_normal_codex_but_explicit_check_fails(self):
        self.configured()
        with patch('codexx.releases.latest', side_effect=TimeoutError('offline')), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(updates.preflight(['-h']), (None, ['-h']))
            self.assertEqual(updates.preflight(['--codexx-check-update']), (1, []))

    def test_startup_only_uses_cache_and_never_prompts_or_fetches(self):
        self.configured()
        with patch('codexx.releases.latest') as latest, patch('builtins.input') as prompt:
            self.assertEqual(updates.preflight(['resume', '--last']), (None, ['resume', '--last']))
            self.assertEqual(self.capture.getvalue(), '')
            latest.assert_not_called()
            prompt.assert_not_called()
        self.scheduler.assert_called_once_with('owner/repo')

    def test_background_result_is_only_announced_on_the_next_start(self):
        self.configured()
        self.assertEqual(updates.preflight([]), (None, []))
        with patch('codexx.releases.latest', return_value={'tag_name': 'v99.0.0'}):
            updates.check_background('owner/repo', self.root)
        self.assertEqual(self.capture.getvalue(), '')
        with patch('builtins.input') as prompt, patch('codexx.updates.prepare_update') as install:
            updates.preflight(['--version'])
            self.assertIn('v99.0.0', self.capture.getvalue())
            self.assertIn('--codexx-update', self.capture.getvalue())
            prompt.assert_not_called()
            install.assert_not_called()

    def test_background_timeout_is_silent_and_retains_known_update(self):
        self.configured()
        installation.write_json(self.root / 'update-check.json',
            {'repository': 'owner/repo', 'latest_version': 'v99.0.0', 'checked_at': 12})
        with patch('codexx.releases.latest', side_effect=TimeoutError('SSL timed out')), \
                patch('sys.stderr', new_callable=io.StringIO) as errors:
            updates.check_background('owner/repo', self.root)
            self.assertEqual(errors.getvalue(), '')
        value = updates.read_cache('owner/repo', self.root)
        self.assertEqual(value['latest_version'], 'v99.0.0')
        self.assertEqual(value['error_type'], 'TimeoutError')
        self.assertEqual(self.capture.getvalue(), '')

    def test_cache_cannot_announce_another_repository(self):
        self.configured()
        installation.write_json(self.root / 'update-check.json',
            {'repository': 'other/repo', 'latest_version': 'v99.0.0'})
        updates.preflight([])
        self.assertEqual(self.capture.getvalue(), '')

    def test_cache_throttles_checks_and_retries_errors_after_five_minutes(self):
        with patch('codexx.updates.threading.Thread') as thread:
            installation.write_json(self.root / 'update-check.json',
                {'repository': 'owner/repo', 'last_attempt_at': time.time()})
            REAL_SCHEDULE('owner/repo')
            thread.assert_not_called()
            installation.write_json(self.root / 'update-check.json',
                {'repository': 'owner/repo', 'last_attempt_at': time.time() - 301, 'error_type': 'TimeoutError'})
            REAL_SCHEDULE('owner/repo')
            self.assertTrue(thread.call_args.kwargs['daemon'])
            thread.return_value.start.assert_called_once()

    def test_slow_background_request_does_not_block_startup(self):
        self.configured()
        entered, proceed = threading.Event(), threading.Event()
        workers = []
        def fetch(repo):
            entered.set()
            proceed.wait(3)
            return {'tag_name': 'v99.0.0'}
        def schedule(repo):
            workers.append(REAL_SCHEDULE(repo))
        try:
            with patch('codexx.releases.latest', side_effect=fetch), \
                    patch('codexx.updates.schedule_background', side_effect=schedule):
                self.assertEqual(updates.preflight([]), (None, []))
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.capture.getvalue(), '')
                self.assertFalse((self.root / 'update-check.json').exists())
                proceed.set()
                workers[0].join(2)
                self.assertFalse(workers[0].is_alive())
        finally:
            proceed.set()
            for worker in workers:
                worker.join(2)
        self.assertEqual(updates.read_cache('owner/repo', self.root)['latest_version'], 'v99.0.0')

    def test_skip_and_unconfigured_start_never_schedule_checks(self):
        updates.preflight([])
        self.configured()
        updates.preflight(['--codexx-no-update'])
        self.scheduler.assert_not_called()

    def test_up_to_date_release_does_not_prompt(self):
        self.configured()
        with patch('codexx.releases.latest', return_value={'tag_name': 'v' + VERSION}), patch('builtins.input') as prompt:
            self.assertEqual(updates.preflight(['--codexx-update']), (0, []))
            prompt.assert_not_called()

    def test_install_copies_only_binary_and_runtime_metadata_preserving_logs(self):
        source = self.root / 'build-output'
        source.write_bytes(b'new executable')
        destination = self.root / 'installation'
        (destination / 'logs').mkdir(parents=True)
        (destination / 'logs' / 'old.jsonl').write_text('keep')
        installed = installation.install(source, 'owner/repo', destination, configure_path=False)
        self.assertEqual(installed.read_bytes(), b'new executable')
        self.assertEqual((destination / 'logs' / 'old.jsonl').read_text(), 'keep')
        self.assertEqual({p.name for p in destination.iterdir()},
                         {executable_name(), 'config.json', 'install.json', 'logs'})

    def test_failed_replacement_keeps_previous_binary(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'new')
        target.write_bytes(b'old')
        with patch('codexx.installation.os.replace', side_effect=PermissionError('in use')), self.assertRaises(PermissionError):
            installation.copy_binary(source, target)
        self.assertEqual(target.read_bytes(), b'old')
        self.assertEqual(target.with_name('target.previous').read_bytes(), b'old')

    def test_invalid_existing_config_is_detected_before_replacing_binary(self):
        source, target = self.root / 'source', self.root / executable_name()
        source.write_bytes(b'new')
        target.write_bytes(b'old')
        (self.root / 'config.json').write_text('invalid json')
        with self.assertRaises(ValueError):
            installation.install(source, directory=self.root, configure_path=False)
        self.assertEqual(target.read_bytes(), b'old')

    @unittest.skipUnless(os.name == 'nt', 'Windows image locking behavior')
    def test_loaded_windows_executable_can_be_upgraded_without_stopping_it(self):
        import ctypes
        from ctypes import wintypes
        import shutil
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
        kernel.LoadLibraryExW.restype = wintypes.HMODULE
        kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
        target, source = self.root / 'loaded.exe', self.root / 'new.exe'
        shutil.copyfile(sys.executable, target)
        original = target.read_bytes()
        source.write_bytes(b'new executable')
        image = kernel.LoadLibraryExW(str(target), None, 1)  # Map only; do not execute code.
        self.assertTrue(image)
        try:
            with self.assertRaises(PermissionError):
                os.replace(source, target)
            installation.copy_binary(source, target)
            self.assertEqual(target.read_bytes(), b'new executable')
            parked = list(self.root.glob('loaded.exe.in-use-*'))
            self.assertEqual(len(parked), 1)
            self.assertEqual(parked[0].read_bytes(), original)
        finally:
            kernel.FreeLibrary(image)

    def plan(self, digest=None):
        stage = self.root / 'updates' / 'test-stage'
        stage.mkdir(parents=True)
        binary = stage / executable_name()
        binary.write_bytes(b'new executable')
        plan = stage / 'plan.json'
        installation.write_json(plan, {'protocol': 1, 'version': VERSION,
            'sha256': digest or hashlib.sha256(binary.read_bytes()).hexdigest()})
        return plan

    def test_helper_applies_validated_plan_without_network(self):
        plan = self.plan()
        (self.root / executable_name()).write_bytes(b'old executable')
        with patch('codexx.releases.latest') as latest:
            self.assertEqual(updates.preflight(['--codexx-internal-apply', str(plan)]), (0, []))
            latest.assert_not_called()
        self.assertEqual((self.root / executable_name()).read_bytes(), b'new executable')
        self.assertEqual(json.loads((self.root / 'update-status.json').read_text())['status'], 'installed')

    def test_helper_rejects_tampered_stage_and_foreign_plan_paths(self):
        plan = self.plan('0' * 64)
        self.assertEqual(updates.apply_plan(plan, 0), 1)
        self.assertFalse((self.root / executable_name()).exists())
        self.assertEqual(updates.apply_plan(self.root / 'foreign.json', 0), 1)

    def test_helper_handles_busy_binary_without_stopping_any_process(self):
        plan = self.plan()
        with patch('codexx.updates.copy_binary', side_effect=PermissionError('busy')), patch('codexx.updates.time.monotonic', side_effect=[0, 100]):
            self.assertEqual(updates.apply_plan(plan, 1), 1)
        self.assertIn('Other codexx sessions', json.loads((self.root / 'update-status.json').read_text())['error'])


if __name__ == '__main__':
    unittest.main()
