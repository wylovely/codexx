import json
import io
from pathlib import Path
import sys
import time
import tempfile
import unittest
from unittest.mock import patch
from websockets.sync.client import connect
from codexx.cli import Bridge, Recovery, RunLog, arguments, direct_command, native_call, reset_time, credits_available, native_information, main, VERSION

ROOT = Path(__file__).resolve().parent


def failed(thread='A', info='usageLimitExceeded', status='failed'):
    return {'method': 'turn/completed', 'params': {'threadId': thread,
        'turn': {'id': 'T', 'status': status, 'error': {'codexErrorInfo': info}}}}


class StateTests(unittest.TestCase):
    def state(self):
        state = Recovery()
        state.client({'method': 'turn/start', 'params': {'threadId': 'A'}})
        return state

    def test_startup_does_not_arm(self):
        state = Recovery()
        state.server({'method': 'account/rateLimits/updated', 'params': {'rateLimits': {'primary': {'usedPercent': 100}}}})
        self.assertIsNone(state.pending)

    def test_quoted_error_text_never_arms(self):
        state = self.state()
        state.server({'method': 'item/agentMessage/delta', 'params': {'threadId': 'A', 'delta': "You've hit your usage limit"}})
        self.assertIsNone(state.pending)

    def test_only_structured_failed_quota_turn_arms(self):
        state = self.state()
        state.server(failed(info='rateLimitExceeded'))
        self.assertIsNone(state.pending)
        state.server(failed(status='completed'))
        self.assertIsNone(state.pending)
        self.assertTrue(state.server(failed()))
        self.assertFalse(state.pending['confirmed'])

    def test_background_thread_start_does_not_steal_working_thread(self):
        state = self.state()
        state.client({'id': 'blank', 'method': 'thread/start', 'params': {}})
        state.server({'id': 'blank', 'result': {'thread': {'id': 'blank-thread'}}})
        self.assertEqual(state.active_thread, 'A')
        self.assertTrue(state.server(failed()))

    def test_ephemeral_turn_does_not_steal_working_thread_or_cancel_countdown(self):
        state = self.state()
        state.server(failed())
        pending = state.pending
        state.client({'id': 'helper', 'method': 'thread/start', 'params': {'ephemeral': True}})
        state.server({'id': 'helper', 'result': {'thread': {'id': 'helper', 'ephemeral': True}}})
        for method in ('turn/start', 'turn/steer', 'turn/interrupt', 'thread/goal/set', 'thread/resume'):
            state.client({'id': method, 'method': method, 'params': {'threadId': 'helper'}})
        state.server(failed(thread='helper'))
        self.assertEqual(state.active_thread, 'A')
        self.assertIs(state.pending, pending)

    def test_ephemeral_response_without_flag_is_tracked_from_request(self):
        state = self.state()
        state.client({'id': 'helper', 'method': 'thread/start', 'params': {'ephemeral': True}})
        state.server({'id': 'helper', 'result': {'thread': {'id': 'helper'}}})
        state.client({'method': 'turn/start', 'params': {'threadId': 'helper'}})
        self.assertEqual(state.active_thread, 'A')
        self.assertTrue(state.server(failed()))

    def test_real_new_thread_turn_still_changes_working_thread(self):
        state = self.state()
        state.client({'id': 'new', 'method': 'thread/start', 'params': {}})
        state.server({'id': 'new', 'result': {'thread': {'id': 'new', 'ephemeral': False}}})
        state.client({'method': 'turn/start', 'params': {'threadId': 'new'}})
        self.assertEqual(state.active_thread, 'new')

    def test_credit_metadata_and_bucket_selection(self):
        for credits, expected in [({'hasCredits': True}, True), ({'unlimited': True}, True),
                ({'hasCredits': False, 'balance': '12'}, False), ({'balance': '0.25'}, True),
                ({'balance': '0'}, False), ({'balance': '-2'}, False), ({'balance': 'NaN'}, False),
                ({'balance': 'Infinity'}, False), ({'balance': 'bad'}, False), ({}, False)]:
            with self.subTest(credits=credits):
                self.assertEqual(credits_available({'rateLimits': {'credits': credits}}), expected)
        self.assertFalse(credits_available({'rateLimitsByLimitId': {
            'codex': {'credits': {'hasCredits': False}}, 'other': {'credits': {'hasCredits': True}}}}))

    def test_poll_option_is_wrapper_only_and_positive(self):
        _, args = arguments(['--codexx-poll-seconds', '15', 'resume'])
        self.assertEqual(args.poll_seconds, 15)
        self.assertEqual(args.args, ['resume'])
        self.assertEqual(arguments([])[1].poll_seconds, 30)

    def test_help_version_detection_ignores_values_and_literal_input(self):
        for argv in (['--', '--help'], ['-m', '--help'], ['-c', 'value=--version']):
            self.assertIsNone(native_information(argv)[0])
        self.assertEqual(native_information(['resume', '--help'])[0], 'help')
        self.assertEqual(native_information(['-v']), ('version', ['--version']))
        self.assertEqual(native_information(['--help', '--version'])[0], 'help')

    def test_combined_help_and_versions_bypass_bridge_and_logs(self):
        for flag in ('-h', '--help', '-v', '-V', '--version'):
            with self.subTest(flag=flag), patch('sys.argv', ['codexx', '--codexx-bin', 'native-codex', flag]), \
                    patch('sys.stdout', new_callable=io.StringIO) as output, \
                    patch('codexx.cli.native_call', return_value=7) as native, \
                    patch('codexx.cli.Bridge') as bridge, patch('codexx.cli.RunLog') as journal:
                self.assertEqual(main(), 7)
                self.assertTrue(output.getvalue().startswith('codexx ' + VERSION))
                if flag in ('-h', '--help'):
                    self.assertIn('--codexx-poll-seconds', output.getvalue())
                native.assert_called_once_with(['native-codex', '--version' if flag == '-v' else flag])
                bridge.assert_not_called()
                journal.assert_not_called()

    def test_wrapper_only_information_does_not_launch_codex(self):
        for flag in ('--codexx-help', '--codexx-version'):
            with patch('sys.argv', ['codexx', flag]), patch('sys.stdout', new_callable=io.StringIO) as output, patch('codexx.cli.native_call') as native:
                self.assertEqual(main(), 0)
                self.assertTrue(output.getvalue().startswith('codexx ' + VERSION))
                native.assert_not_called()

    def test_goal_pause_cancels_quota_wait(self):
        state = self.state()
        state.server(failed())
        state.client({'method': 'thread/goal/set', 'params': {'threadId': 'A', 'status': 'paused', 'origin': 'user'}})
        self.assertIsNone(state.pending)

    def test_other_threads_do_not_arm(self):
        state = self.state()
        state.server(failed('B'))
        self.assertIsNone(state.pending)

    def test_user_request_cancels_wait(self):
        state = self.state()
        state.server(failed())
        state.client({'method': 'turn/interrupt', 'params': {'threadId': 'A'}})
        self.assertIsNone(state.pending)

    def test_error_notification_requires_failed_completion(self):
        state = self.state()
        state.server({'method': 'error', 'params': {'threadId': 'A', 'turnId': 'T', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}})
        self.assertIsNone(state.pending)
        message = failed(info=None)
        self.assertTrue(state.server(message))

    def test_quota_waits_for_both_windows(self):
        self.assertEqual(reset_time({'rateLimits': {'primary': {'usedPercent': 100, 'resetsAt': 100},
            'secondary': {'usedPercent': 100, 'resetsAt': 200}}}), 200)
        with self.assertRaises(ValueError):
            reset_time({})

    def test_arguments_preserve_prompt_and_switches(self):
        project = str(ROOT / 'my project')
        values = ['-C', project, '-c', 'model="test"', 'resume', '--last']
        _, args = arguments(values)
        self.assertEqual(args.args, values)
        self.assertEqual(args.server_options, ['-C', project, '-c', 'model="test"'])
        self.assertTrue(direct_command(['exec', '--json', 'hello']))
        self.assertFalse(direct_command(['-m', 'exec', 'resume']))

    def test_native_call_inherits_all_console_handles(self):
        with patch('codexx.cli.subprocess.call', return_value=7) as call:
            self.assertEqual(native_call(['codex', 'resume']), 7)
        call.assert_called_once_with(['codex', 'resume'], env=None)

    def test_persistent_log_records_state_without_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = RunLog(directory)
            state = Recovery(journal.emit)
            state.client({'method': 'turn/start', 'params': {'threadId': 'A', 'input': [{'text': 'private task text'}]}})
            state.server(failed())
            state.cancel('test_cancel')
            text = journal.path.read_text(encoding='utf-8')
            events = [json.loads(line)['event'] for line in text.splitlines()]
            self.assertIn('quota_failure_detected', events)
            self.assertIn('recovery_cancelled', events)
            self.assertNotIn('private task text', text)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bridge = Bridge([sys.executable, str(ROOT / 'fixtures' / 'protocol_server.py')], margin=0, poll_seconds=0.05,
            log=lambda event, **fields: self.events.append({'event': event, **fields}))
        self.client = connect(self.bridge.url, additional_headers={'Authorization': 'Bearer ' + self.bridge.token}, proxy=None)
        self.client.send(json.dumps({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'test', 'version': '1'}}}))
        self.assertEqual(json.loads(self.client.recv())['id'], 1)
        self.client.send(json.dumps({'method': 'initialized', 'params': {}}))

    def tearDown(self):
        self.client.close()
        self.bridge.close()

    def test_startup_is_silent_and_no_quota_queries(self):
        with self.assertRaises(TimeoutError):
            self.client.recv(timeout=0.1)
        self.assertIsNone(self.bridge.state.pending)

    def test_transparent_unicode_and_color_payload(self):
        payload = {'text': '\x1b]11;rgb:2828/2c2c/2828\x1b\\中文😀' * 10000}
        self.client.send(json.dumps({'id': 'echo', 'method': 'echo', 'params': payload}))
        self.assertEqual(json.loads(self.client.recv())['result']['echo'], payload)

    def continue_after(self, text):
        self.client.send(json.dumps({'id': 2, 'method': 'turn/start', 'params': {'threadId': 'A', 'input': [{'type': 'text', 'text': text}]}}))
        observed = []
        end = time.monotonic() + 9
        while time.monotonic() < end:
            message = json.loads(self.client.recv(timeout=9))
            observed.append(message)
            if message.get('method') == 'item/agentMessage/delta':
                self.assertEqual(message['params']['threadId'], 'A')
                self.assertEqual(message['params']['delta'], 'continue')
                break
        else:
            self.fail(str(observed))
        self.assertTrue(all(not str(message.get('id', '')).startswith('codexx:') for message in observed))

    def test_quota_recovery_stays_on_same_thread(self):
        self.continue_after('hit')
        names = [event['event'] for event in self.events]
        self.assertIn('quota_failure_detected', names)
        self.assertIn('quota_recovered', names)
        self.assertIn('continue_submitted', names)
        self.assertIn('continue_acknowledged', names)

    def test_second_quota_cycle_after_automatic_continue(self):
        self.continue_after('hit')
        self.client.send(json.dumps({'id': 91, 'method': 'thread/start', 'params': {'ephemeral': True}}))
        deadline = time.monotonic() + 3
        while 'helper' not in self.bridge.state.ephemeral_threads and time.monotonic() < deadline:
            time.sleep(0.01)
        self.client.send(json.dumps({'id': 93, 'method': 'turn/start', 'params': {'threadId': 'helper', 'input': [{'type': 'text', 'text': 'helper-task'}]}}))
        self.client.send(json.dumps({'id': 92, 'method': 'test/quotaFailure', 'params': {}}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            msg = json.loads(self.client.recv(timeout=5))
            if msg.get('method') == 'item/agentMessage/delta' and sum(e['event'] == 'continue_submitted' for e in self.events) >= 2:
                break
        self.assertEqual(sum(e['event'] == 'quota_failure_detected' for e in self.events), 2)
        self.assertEqual(sum(e['event'] == 'continue_submitted' for e in self.events), 2)

    def test_usage_limited_goal_resumes_without_continue_text(self):
        self.client.send(json.dumps({'id': 11, 'method': 'test/goalStatus', 'params': {'status': 'usageLimited'}}))
        self.client.recv()
        self.client.send(json.dumps({'id': 12, 'method': 'turn/start', 'params': {'threadId': 'A', 'input': [{'type': 'text', 'text': 'hit'}]}}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            msg = json.loads(self.client.recv(timeout=5))
            self.assertNotEqual(msg.get('method'), 'item/agentMessage/delta')
            if msg.get('method') == 'test/goalResumed':
                self.assertEqual(msg['params'], {'threadId': 'A', 'status': 'active', 'origin': 'user'})
                break
        else:
            self.fail('Goal did not resume')
        self.assertIn('goal_resume_submitted', [e['event'] for e in self.events])
        self.assertNotIn('continue_submitted', [e['event'] for e in self.events])

    def test_paused_or_finished_goals_are_not_resumed(self):
        self.bridge.backend.rpc('account/rateLimits/read')  # Fixture's next query reports recovered quota.
        for status in ('paused', 'blocked', 'budgetLimited', 'complete'):
            with self.subTest(status=status):
                self.client.send(json.dumps({'id': 61, 'method': 'test/goalStatus', 'params': {'status': status}}))
                while json.loads(self.client.recv(timeout=2)).get('id') != 61:
                    pass
                with self.bridge.lock:
                    self.bridge.state.active_thread = 'A'
                    self.bridge.state.server(failed())
                    self.bridge.state.pending['confirmed'] = True
                self.bridge.changed.set()
                deadline = time.monotonic() + 3
                while self.bridge.state.pending is not None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIsNone(self.bridge.state.pending)
        self.assertNotIn('goal_resume_submitted', [e['event'] for e in self.events])
        self.assertNotIn('continue_submitted', [e['event'] for e in self.events])

    def test_rejected_submission_is_rearmed_unless_user_took_over(self):
        with self.bridge.lock:
            self.bridge.state.active_thread = 'A'
            self.bridge.state.server(failed())
            pending = dict(self.bridge.state.pending, confirmed=True)
            self.bridge.state.cancel('test_submission')
            self.bridge.recovery_attempt = (pending, self.bridge.state.generation)
            self.bridge.server_message({'method': 'codexx/recoveryResponse', 'params': {'accepted': False, 'error_code': -1}})
            self.assertIsNotNone(self.bridge.state.pending)
            self.bridge.state.cancel('test_submission')
            self.bridge.recovery_attempt = (pending, self.bridge.state.generation)
            self.bridge.state.client({'method': 'turn/interrupt', 'params': {'threadId': 'A'}})
            self.bridge.server_message({'method': 'codexx/recoveryResponse', 'params': {'accepted': False, 'error_code': -1}})
            self.assertIsNone(self.bridge.state.pending)

    def quota_result(self, used=100, paid=False, reset=None):
        return {'rateLimits': {'limitId': 'codex', 'credits': {'hasCredits': paid, 'balance': '10' if paid else '0'},
            'primary': {'usedPercent': used, 'resetsAt': reset or time.time() + 3600}}}

    def configure_quota_results(self, results):
        self.bridge.backend.rpc('test/quotaSequence', {'results': results})

    def test_top_up_recovers_before_scheduled_reset(self):
        self.configure_quota_results([self.quota_result(), self.quota_result(paid=True)])
        self.continue_after('hit')
        self.assertTrue(any(e['event'] == 'quota_recovered' and e.get('reason') == 'credits_available' for e in self.events))
        self.assertTrue(any(e['event'] == 'countdown_started' and e['resets_at'] > time.time() + 3000 for e in self.events))

    def test_manual_reset_recovers_before_old_timestamp(self):
        self.configure_quota_results([self.quota_result(), self.quota_result(used=0)])
        self.continue_after('hit')
        self.assertTrue(any(e['event'] == 'quota_recovered' and e.get('reason') == 'quota_available' for e in self.events))

    def test_paid_credits_at_first_read_do_not_need_window_metadata(self):
        self.configure_quota_results([{'rateLimits': {'limitId': 'codex', 'credits': {'hasCredits': True}}}])
        self.continue_after('hit')

    def test_reset_before_first_query_still_recovers(self):
        self.configure_quota_results([self.quota_result(used=0)])
        self.continue_after('hit')

    def test_zero_credits_keep_waiting_and_poll_without_starting_turns(self):
        self.configure_quota_results([self.quota_result()])
        self.client.send(json.dumps({'id': 76, 'method': 'turn/start', 'params': {'threadId': 'A', 'input': [{'type': 'text', 'text': 'hit'}]}}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and sum(e['event'] == 'quota_check_result' for e in self.events) < 3:
            time.sleep(0.02)
        self.assertGreaterEqual(sum(e['event'] == 'quota_check_result' for e in self.events), 3)
        self.assertIsNotNone(self.bridge.state.pending)
        self.assertNotIn('continue_submitted', [e['event'] for e in self.events])
        self.assertNotIn('goal_resume_submitted', [e['event'] for e in self.events])

    def test_goal_resumes_on_top_up(self):
        self.bridge.backend.rpc('test/goalStatus', {'status': 'usageLimited'})
        self.configure_quota_results([self.quota_result(), self.quota_result(paid=True)])
        self.client.send(json.dumps({'id': 77, 'method': 'turn/start', 'params': {'threadId': 'A', 'input': [{'type': 'text', 'text': 'hit'}]}}))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if json.loads(self.client.recv(timeout=3)).get('method') == 'test/goalResumed':
                break
        else:
            self.fail('Goal did not resume after top-up')
        self.assertNotIn('continue_submitted', [e['event'] for e in self.events])

    def test_transient_query_failure_does_not_cancel_recovery(self):
        self.continue_after('hit-retry')
        self.assertIn('quota_check_retry', [event['event'] for event in self.events])

    def test_premium_empty_snapshot_does_not_erase_codex_countdown(self):
        self.continue_after('hit-cached-retry')
        self.assertTrue(any(event['event'] == 'countdown_started' and event.get('source') == 'recent_server_snapshot' for event in self.events))
        self.assertIn('quota_check_retry', [event['event'] for event in self.events])

    def test_loopback_requires_token(self):
        from websockets.exceptions import InvalidStatus
        with self.assertRaises(InvalidStatus):
            connect(self.bridge.url, proxy=None)

    def second_client(self):
        client = connect(self.bridge.url, additional_headers={'Authorization': 'Bearer ' + self.bridge.token}, proxy=None)
        client.send(json.dumps({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'picker', 'version': '1'}}}))
        response = json.loads(client.recv())
        self.assertNotIn('error', response)
        self.assertEqual(response['id'], 1)
        client.send(json.dumps({'method': 'initialized', 'params': {}}))
        return client

    def test_picker_can_initialize_while_terminal_stays_connected(self):
        with self.second_client() as picker:
            picker.send(json.dumps({'id': 2, 'method': 'thread/list', 'params': {'limit': 10}}))
            self.assertEqual(json.loads(picker.recv())['id'], 2)
        self.client.send(json.dumps({'id': 3, 'method': 'echo', 'params': {'still': 'connected'}}))
        self.assertEqual(json.loads(self.client.recv())['result']['echo'], {'still': 'connected'})

    def test_cd_forms_reach_backend_and_choose_workspace(self):
        for options in (['-C', str(ROOT)], ['--cd=' + str(ROOT)], ['-C' + str(ROOT)]):
            _, args = arguments(options)
            self.assertEqual(args.workspace, str(ROOT.resolve()))
            self.assertEqual(args.server_options, options)
        _, args = arguments(['-c', 'value="-Cother"', '--', '-C', 'ignored'])
        self.assertEqual(args.workspace, str(Path.cwd()))

    def test_successful_resume_updates_picker_directory(self):
        self.bridge.routes['workspace-test'] = ('missing-client', 1, 'thread/resume')
        self.bridge.server_message({'id': 'workspace-test', 'result': {'thread': {'cwd': str(ROOT)}}})
        self.assertEqual(self.bridge.workspace, str(ROOT))
        self.bridge.routes['workspace-failure'] = ('missing-client', 1, 'thread/resume')
        self.bridge.server_message({'id': 'workspace-failure', 'error': {'code': -1}})
        self.assertEqual(self.bridge.workspace, str(ROOT))

    def test_session_picker_scopes_to_workspace(self):
        with self.second_client() as picker:
            picker.send(json.dumps({'id': 2, 'method': 'thread/list', 'params': {'cursor': 'page2'}}))
            params = json.loads(picker.recv())['result']['echo']
            self.assertEqual(params['cwd'], self.bridge.workspace)
            self.assertEqual(params['cursor'], 'page2')

    def test_explicit_session_directory_is_preserved(self):
        self.client.send(json.dumps({'id': 2, 'method': 'thread/list', 'params': {'cwd': ['other-project']}}))
        self.assertEqual(json.loads(self.client.recv())['result']['echo']['cwd'], ['other-project'])

    def test_all_sessions_bypasses_default_directory(self):
        self.bridge.all_sessions = True
        self.client.send(json.dumps({'id': 2, 'method': 'thread/list', 'params': {'cwd': None}}))
        self.assertIsNone(json.loads(self.client.recv())['result']['echo']['cwd'])

    def test_same_request_ids_on_two_connections_do_not_cross(self):
        with self.second_client() as picker:
            self.client.send(json.dumps({'id': 5, 'method': 'echo', 'params': {'owner': 'terminal'}}))
            picker.send(json.dumps({'id': 5, 'method': 'echo', 'params': {'owner': 'picker'}}))
            self.assertEqual(json.loads(self.client.recv())['result']['echo'], {'owner': 'terminal'})
            self.assertEqual(json.loads(picker.recv())['result']['echo'], {'owner': 'picker'})

    def test_picker_disconnect_does_not_cancel_terminal_wait(self):
        picker = self.second_client()
        with self.bridge.lock:
            self.bridge.state.active_thread = 'A'
            self.bridge.state.server(failed())
            self.bridge.state.pending.update(confirmed=True, target=time.time() + 60, next_check=time.time() + 60)
        picker.close()
        time.sleep(0.05)
        with self.bridge.lock:
            self.assertIsNotNone(self.bridge.state.pending)

    def test_approval_goes_to_terminal_and_answer_keeps_server_id(self):
        with self.second_client() as picker:
            self.client.send(json.dumps({'id': 8, 'method': 'test/requestApproval', 'params': {}}))
            self.assertEqual(json.loads(self.client.recv())['id'], 8)
            request = json.loads(self.client.recv())
            self.assertEqual(request['id'], 'approval-1')
            with self.assertRaises(TimeoutError):
                picker.recv(timeout=0.1)
            self.client.send(json.dumps({'id': request['id'], 'result': {'decision': 'decline'}}))
            answer = json.loads(self.client.recv())
            self.assertEqual(answer['method'], 'test/approvalAnswer')
            self.assertEqual(answer['params']['decision'], 'decline')


if __name__ == '__main__':
    unittest.main()
