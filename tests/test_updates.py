import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codexx import installation, updates
from codexx.cli import main
from codexx.paths import executable_name
from codexx.version import VERSION


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'CODEXX_HOME': str(self.root), 'CODEXX_REPOSITORY': ''})
        self.environment.start()
        self.output = patch('sys.stdout', new_callable=io.StringIO)
        self.output.start()

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

    def test_declining_optional_update_preserves_native_arguments(self):
        self.configured()
        with patch('codexx.releases.latest', return_value={'tag_name': 'v99.0.0'}), \
                patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='n'), \
                patch('codexx.updates.prepare_update') as install:
            self.assertEqual(updates.preflight(['resume', '--last']), (None, ['resume', '--last']))
            install.assert_not_called()

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
