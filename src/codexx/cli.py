"""codexx: native Codex terminal UI with structured quota recovery."""
import argparse
import contextlib
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from .version import VERSION
from .paths import home


class RunLog:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / ('codexx-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()) + '.jsonl')
        self.lock = threading.Lock()
        self.last_write_error = None
        self.emit('process_started', version=VERSION)

    def emit(self, event, **fields):
        record = {'timestamp': datetime.now().astimezone().isoformat(timespec='milliseconds'),
                  'event': event, 'pid': os.getpid(), **fields}
        with self.lock:
            try:
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=True) + '\n')
            except OSError as exc:
                self.last_write_error = type(exc).__name__


def quota_summary(result):
    values = dict(result.get('rateLimitsByLimitId') or {})
    legacy = result.get('rateLimits')
    if legacy:
        values.setdefault(legacy.get('limitId') or 'codex', legacy)
    return {name: {key: ({field: value[key].get(field) for field in ('usedPercent', 'resetsAt', 'windowDurationMins')}
                if value.get(key) else None) for key in ('primary', 'secondary')}
            for name, value in values.items()}
NONINTERACTIVE = {'agents', 'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin',
                  'app-server', 'remote-control', 'app', 'completion', 'update', 'doctor',
                  'sandbox', 'debug', 'apply', 'a', 'queue', 'archive', 'delete',
                  'migrate-rollouts', 'unarchive', 'cloud', 'exec-server', 'features', 'help'}
VALUE_FLAGS = {'-c', '--config', '--enable', '--disable', '--remote', '--remote-auth-token-env',
               '-i', '--image', '-m', '--model', '--local-provider', '-p', '--profile',
               '-s', '--sandbox', '-C', '--cd', '--add-dir', '-a', '--ask-for-approval'}


def arguments(argv):
    parser = argparse.ArgumentParser(prog='codexx', add_help=False, description=__doc__)
    parser.add_argument('--codexx-bin', dest='codex', default=os.environ.get('CODEXX_CODEX') or shutil.which('codex'))
    parser.add_argument('--codexx-bucket', dest='bucket', default='codex')
    parser.add_argument('--codexx-poll-seconds', dest='poll_seconds', type=int, default=30, help='Poll credits and early quota resets every N seconds (default: 30).', metavar='N')
    descriptions = {
        'check': 'Query account limits and credits, then exit.',
        'help': 'Show only codexx help.',
        'version': 'Show only the codexx version.',
        'native': 'Run native Codex without automatic recovery.',
        'logs': 'Print the recovery log directory.',
        'all-sessions': 'Show sessions from all project directories.',
    }
    for name, description in descriptions.items():
        parser.add_argument('--codexx-' + name, dest=name, action='store_true', help=description)
    own, forwarded = [], []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--':
            forwarded.extend(argv[index:])
            break
        if arg.split('=', 1)[0] in ('--codexx-bin', '--codexx-bucket', '--codexx-poll-seconds'):
            own.append(arg)
            if '=' not in arg:
                index += 1
                if index >= len(argv):
                    parser.error(arg + ' requires a value')
                own.append(argv[index])
        elif arg in {'--codexx-' + name for name in ('check', 'help', 'version', 'native', 'logs', 'all-sessions')}:
            own.append(arg)
        else:
            forwarded.append(arg)
        index += 1
    args = parser.parse_args(own)
    if args.poll_seconds < 1:
        parser.error('--codexx-poll-seconds must be at least 1')
    args.args = forwarded
    args.server_options = []
    args.workspace = str(Path.cwd())
    index = 0
    while index < len(forwarded):
        arg = forwarded[index]
        if arg == '--':
            break
        if arg in ('-C', '--cd') and index + 1 < len(forwarded):
            args.workspace = str(Path(forwarded[index + 1]).expanduser().resolve())
            args.server_options.extend(forwarded[index:index + 2])
            index += 1
        elif arg.startswith('--cd=') or (arg.startswith('-C') and len(arg) > 2):
            value = arg.split('=', 1)[1] if arg.startswith('--cd=') else arg[2:]
            args.workspace = str(Path(value).expanduser().resolve())
            args.server_options.append(arg)
        elif arg in ('-c', '--config', '--enable', '--disable'):
            if index + 1 < len(forwarded):
                args.server_options.extend(forwarded[index:index + 2])
                index += 1
        elif arg.startswith(('--config=', '--enable=', '--disable=')) or (arg.startswith('-c') and not arg.startswith('--') and len(arg) > 2):
            args.server_options.append(arg)
        elif arg == '--strict-config':
            args.server_options.append(arg)
        index += 1
    return parser, args


def direct_command(argv):
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == '--':
            return False
        if arg in ('--help', '-h', '--version', '-V'):
            return True
        if arg in VALUE_FLAGS:
            skip = True
        elif not arg.startswith('-'):
            return arg in NONINTERACTIVE
    return False


def quota_bucket(result, bucket='codex'):
    value = (result.get('rateLimitsByLimitId') or {}).get(bucket)
    if value is None:
        value = result.get('rateLimits') or {}
        if value.get('limitId', 'codex') != bucket:
            raise ValueError('Requested quota bucket is unavailable')
    return value


def credits_available(result, bucket='codex'):
    credits = quota_bucket(result, bucket).get('credits') or {}
    if credits.get('unlimited') is True:
        return True
    if isinstance(credits.get('hasCredits'), bool):
        return credits['hasCredits']
    try:
        balance = Decimal(str(credits.get('balance')))
        return balance.is_finite() and balance > 0
    except InvalidOperation:
        return False


def reset_time(result, bucket='codex'):
    value = quota_bucket(result, bucket)
    windows = [w for w in (value.get('primary'), value.get('secondary')) if w]
    if not windows or any(not isinstance(w.get('usedPercent'), (int, float)) for w in windows):
        raise ValueError('Quota window data is unavailable')
    exhausted = [w for w in windows if w['usedPercent'] >= 100]
    if not exhausted:
        return None
    times = [w.get('resetsAt') for w in exhausted]
    if any(not isinstance(t, (int, float)) or t <= 0 for t in times):
        raise ValueError('Exhausted quota has no reset timestamp')
    return max(times)


def is_quota_error(error):
    info = (error or {}).get('codexErrorInfo')
    if isinstance(info, dict):
        info = info.get('type')
    return info in ('usageLimitExceeded', 'UsageLimitExceeded')


class RpcError(RuntimeError):
    def __init__(self, error):
        self.code = error.get('code')
        super().__init__('Codex app-server request failed (code %s)' % self.code)


class Recovery:
    """Protocol state only. Terminal bytes and clipboard events never enter here."""
    def __init__(self, log=lambda event, **fields: None):
        self.log = log
        self.active_thread = None
        self.pending = None
        self.errors = set()
        self.generation = 0
        self.requests = {}
        self.ephemeral_requests = set()
        self.ephemeral_threads = set()

    def cancel(self, reason='cancelled'):
        if self.pending is not None:
            self.log('recovery_cancelled', reason=reason, thread=self.pending['thread'],
                     turn=self.pending['turn'], confirmed=self.pending['confirmed'])
        self.generation += 1
        self.pending = None

    def client(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if params.get('threadId') in self.ephemeral_threads:
            self.log('auxiliary_request_ignored', method=method, thread=params['threadId'])
            return
        if method in ('thread/start', 'thread/resume', 'thread/fork'):
            self.requests[message.get('id')] = method
            if params.get('ephemeral'):
                self.ephemeral_requests.add(message.get('id'))
            if method != 'thread/start':
                self.cancel('client:' + method)
                self.active_thread = params.get('threadId')
        elif method in ('thread/goal/set', 'thread/goal/clear'):
            self.cancel('client:' + method)
            self.active_thread = params.get('threadId', self.active_thread)
        elif method in ('turn/start', 'turn/steer', 'turn/interrupt'):
            self.cancel('client:' + method)
            self.active_thread = params.get('threadId', self.active_thread)

    def server(self, message):
        if message.get('id') in self.requests:
            request_method = self.requests.pop(message['id'], None)
            thread_info = (message.get('result') or {}).get('thread') or {}
            ephemeral = message['id'] in self.ephemeral_requests or thread_info.get('ephemeral') is True
            self.ephemeral_requests.discard(message['id'])
            if ephemeral and thread_info.get('id'):
                self.ephemeral_threads.add(thread_info['id'])
                self.log('auxiliary_thread_registered', thread=thread_info['id'])
            elif 'error' not in message and (request_method != 'thread/start' or self.active_thread is None):
                self.active_thread = thread_info.get('id', self.active_thread)
        method = message.get('method')
        params = message.get('params') or {}
        thread = params.get('threadId')
        if method == 'thread/started':
            info = params.get('thread') or {}
            if info.get('ephemeral') and info.get('id'):
                self.ephemeral_threads.add(info['id'])
        if thread in self.ephemeral_threads:
            return False
        if method == 'error' and is_quota_error(params.get('error')):
            self.errors.add((thread, params.get('turnId')))
        elif method == 'turn/completed':
            turn = params.get('turn') or {}
            key = (thread, turn.get('id'))
            exhausted = is_quota_error(turn.get('error')) or key in self.errors
            self.errors.discard(key)
            self.log('turn_completed', thread=thread, turn=turn.get('id'), status=turn.get('status'),
                     quota_error=exhausted, active_thread=self.active_thread)
            if thread == self.active_thread and turn.get('status') == 'failed' and exhausted and self.pending is None:
                self.generation += 1
                self.pending = {'thread': thread, 'turn': turn.get('id'), 'generation': self.generation,
                                'confirmed': False, 'target': None, 'next_check': 0, 'failures': 0}
                self.log('quota_failure_detected', thread=thread, turn=turn.get('id'))
                return True
        return False


class Backend:
    def __init__(self, command, sink=lambda message: None, log=lambda event, **fields: None):
        self.log = log
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), start_new_session=os.name != 'nt')
        self.sink = sink
        self.lock = threading.RLock()
        self.pending = {}
        self.silent = {}
        self.stopped = threading.Event()
        self.diagnostics = []
        self.log('backend_started', backend_pid=self.process.pid)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()

    def _errors(self):
        for line in self.process.stderr:
            self.diagnostics = (self.diagnostics + [line.strip()])[-8:]

    def _read(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                with self.lock:
                    response = self.pending.get(message.get('id'))
                    silent = message.get('id') in self.silent
                    silent_method = self.silent.pop(message.get('id'), None)
                if response is not None:
                    response.put(message)
                elif silent:
                    error = message.get('error') or {}
                    self.log('goal_resume_acknowledged' if silent_method == 'thread/goal/set' else 'continue_acknowledged', accepted='error' not in message,
                             error_code=error.get('code'),
                             turn=((message.get('result') or {}).get('turn') or {}).get('id'))
                    self.sink({'method': 'codexx/recoveryResponse', 'params': {'accepted': 'error' not in message, 'error_code': error.get('code')}})
                    if 'error' in message:
                        self.diagnostics = (self.diagnostics + ['Automatic turn rejected: ' + str(message['error'])])[-8:]
                else:
                    self.sink(message)
        except (ValueError, OSError) as exc:
            self.log('backend_read_failed', error_type=type(exc).__name__)
        finally:
            self.log('backend_stream_closed', exit_code=self.process.poll())
            self.stopped.set()
            with self.lock:
                for response in self.pending.values():
                    response.put({'error': {'message': 'Codex app-server exited'}})

    def send(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def rpc(self, method, params=None):
        identity = 'codexx:' + uuid.uuid4().hex
        response = queue.Queue()
        with self.lock:
            self.pending[identity] = response
        try:
            self.send({'id': identity, 'method': method, 'params': params if params is not None else {}})
            message = response.get(timeout=20)
            if 'error' in message:
                raise RpcError(message['error'])
            return message['result']
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def send_silent(self, method, params):
        identity = 'codexx:' + uuid.uuid4().hex
        with self.lock:
            self.silent[identity] = method
            self.send({'id': identity, 'method': method, 'params': params})
        self.log('goal_resume_submitted' if method == 'thread/goal/set' else 'continue_submitted', request_id=identity, thread=params.get('threadId'))

    def close(self):
        self.stopped.set()
        with contextlib.suppress(OSError):
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()


class Bridge:
    def __init__(self, command, bucket='codex', margin=5, title=lambda value: None, log=lambda event, **fields: None, workspace=None, all_sessions=False, poll_seconds=30):
        self.poll_seconds = max(0.01, poll_seconds)
        self.last_recovery_at = {}
        self.workspace = str(Path(workspace or Path.cwd()).resolve())
        self.all_sessions = all_sessions
        from websockets.sync.server import serve
        self.log = log
        self.state = Recovery(log)
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.closed = threading.Event()
        self.connection = None
        self.connections = {}
        self.ready_connections = set()
        self.routes = {}
        self.server_routes = {}
        self.initialize_reply = None
        self.initialize_inflight = False
        self.initialize_waiters = []
        self.initialized_sent = False
        self.token = secrets.token_urlsafe(32)
        self.bucket = bucket
        self.margin = margin
        self.title = title
        self.last_error = None
        self.recovery_attempt = None
        self.cache = {}
        self.backend = Backend(command, self.server_message, log)
        self.server = serve(self.handle, '127.0.0.1', 0, origins=[None],
            process_request=self.authenticate, compression=None, max_size=64 * 1024 * 1024,
            close_timeout=1, ping_interval=None)
        self.url = 'ws://127.0.0.1:%d' % self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self.monitor, daemon=True).start()

    def authenticate(self, connection, request):
        if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self.token):
            self.log('connection_rejected', reason='authentication', authorization_header_present='Authorization' in request.headers)
            return connection.respond(401, 'Unauthorized')
        return None

    def handle(self, connection):
        from websockets.exceptions import ConnectionClosed
        identity = uuid.uuid4().hex
        with self.lock:
            self.connections[identity] = connection
            if self.connection is None:
                self.connection = connection
            self.log('terminal_connected', connection=identity, connection_count=len(self.connections))
        try:
            for data in connection:
                message = json.loads(data)
                self.client_message(connection, identity, message)
                self.changed.set()
        except ConnectionClosed:
            pass
        finally:
            with self.lock:
                self.connections.pop(identity, None)
                self.ready_connections.discard(identity)
                if self.connection is connection:
                    self.state.cancel('terminal_disconnected')
                    self.connection = None
                self.initialize_waiters = [item for item in self.initialize_waiters if item[0] != identity]
                for key, route in list(self.routes.items()):
                    if route[0] == identity and route[2] != 'initialize':
                        self.routes.pop(key, None)
                        self.state.requests.pop(key, None)
                self.log('terminal_disconnected', connection=identity, connection_count=len(self.connections))
            self.changed.set()

    def client_message(self, connection, identity, message):
        method = message.get('method')
        if method in ('thread/list', 'thread/start', 'thread/resume', 'config/read'):
            params = message.get('params') or {}
            self.log('workspace_request', method=method, cwd=params.get('cwd'), ephemeral=params.get('ephemeral'), thread=params.get('threadId'))
        original_id = message.get('id')
        reply = None
        outgoing = None
        with self.lock:
            if method == 'initialize' and self.initialize_reply is not None:
                reply = dict(self.initialize_reply, id=original_id)
            elif method == 'initialize' and self.initialize_inflight:
                self.initialize_waiters.append((identity, original_id))
            elif method == 'initialized':
                self.ready_connections.add(identity)
                if not self.initialized_sent:
                    self.initialized_sent = True
                    outgoing = message
            elif method is None and 'id' in message:
                if self.server_routes.get(original_id) == identity:
                    self.server_routes.pop(original_id, None)
                    outgoing = message
                else:
                    self.log('unrouted_client_response', connection=identity)
            else:
                outgoing = dict(message)
                if method == 'thread/list':
                    params = dict(message.get('params') or {})
                    if not self.all_sessions and not params.get('cwd'):
                        params['cwd'] = self.workspace
                    outgoing['params'] = params
                    self.log('session_list_filter', cwd=params.get('cwd'), all_sessions=self.all_sessions)
                if 'id' in message:
                    upstream_id = 'codexx-client:' + uuid.uuid4().hex
                    self.routes[upstream_id] = (identity, original_id, method)
                    outgoing['id'] = upstream_id
                if method == 'initialize':
                    self.initialize_inflight = True
                if method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start', 'turn/steer', 'turn/interrupt', 'thread/goal/set', 'thread/goal/clear') and not (message.get('params') or {}).get('ephemeral') and (message.get('params') or {}).get('threadId') not in self.state.ephemeral_threads:
                    self.log('workspace_action', method=method, thread=(message.get('params') or {}).get('threadId'))
                    self.connection = connection
                if self.connection is connection:
                    self.state.client(outgoing)
        if reply is not None:
            connection.send(json.dumps(reply, ensure_ascii=False))
        if outgoing is not None:
            self.backend.send(outgoing)

    def server_message(self, message):
        with self.lock:
            if message.get('method') == 'codexx/recoveryResponse':
                attempt = self.recovery_attempt
                self.recovery_attempt = None
                if attempt and not message['params']['accepted'] and self.state.generation == attempt[1] and self.state.active_thread == attempt[0]['thread']:
                    self.state.pending = dict(attempt[0], generation=self.state.generation, next_check=time.time() + max(2, self.poll_seconds))
                    self.log('recovery_submission_retry', thread=attempt[0]['thread'], error_code=message['params']['error_code'])
                    self.changed.set()
                return
            if message.get('method') == 'account/rateLimits/updated':
                value = (message.get('params') or {}).get('rateLimits') or {}
                if value.get('primary') or value.get('secondary'):
                    bucket = value.get('limitId') or 'codex'
                    previous = self.cache.get(bucket, ({}, 0))[0]
                    # Sparse updates must not erase a recently known window.
                    merged = dict(previous)
                    merged.update({key: item for key, item in value.items() if item is not None})
                    self.cache[bucket] = (merged, time.time())
                    self.log('quota_snapshot', buckets=quota_summary({'rateLimits': merged}))
            armed = self.state.server(message)
            if armed:
                cached = self.cache.get(self.bucket)
                if cached and time.time() - cached[1] <= 120:
                    try:
                        target = reset_time({'rateLimitsByLimitId': {self.bucket: cached[0]}}, self.bucket)
                        if target and target > time.time():
                            self.state.pending.update(confirmed=True, target=target + self.margin, next_check=0)
                            self.log('countdown_started', source='recent_server_snapshot', thread=self.state.active_thread,
                                     resets_at=target, check_at=target + self.margin)
                    except ValueError:
                        pass
            if armed:
                self.state.pending['next_check'] = max(self.state.pending['next_check'],
                    self.last_recovery_at.get(self.state.pending['thread'], 0) + self.poll_seconds)
            targets = []
            route = self.routes.pop(message.get('id'), None) if message.get('method') is None else None
            if route is not None:
                if route[2] in ('thread/start', 'thread/resume', 'thread/fork') and 'error' not in message:
                    cwd = ((message.get('result') or {}).get('thread') or {}).get('cwd')
                    info = (message.get('result') or {}).get('thread') or {}
                    if isinstance(cwd, str) and cwd and not info.get('ephemeral') and info.get('id') not in self.state.ephemeral_threads:
                        self.workspace = cwd
                        self.log('workspace_selected', cwd=cwd)
                connection = self.connections.get(route[0])
                if connection is not None:
                    targets.append((connection, dict(message, id=route[1])))
                if route[2] == 'initialize':
                    self.initialize_inflight = False
                    if 'error' not in message:
                        self.initialize_reply = dict(message)
                    for identity, request_id in self.initialize_waiters:
                        waiter = self.connections.get(identity)
                        if waiter is not None:
                            targets.append((waiter, dict(message, id=request_id)))
                    self.initialize_waiters.clear()
            elif message.get('method') is not None and 'id' in message:
                connection = self.connection
                if connection is not None:
                    identity = next((key for key, value in self.connections.items() if value is connection), None)
                    self.server_routes[message['id']] = identity
                    targets.append((connection, message))
            elif message.get('method') is not None:
                targets = [(value, message) for key, value in self.connections.items() if key in self.ready_connections]
        for connection, outgoing in targets:
            with contextlib.suppress(Exception):
                connection.send(json.dumps(outgoing, ensure_ascii=False))
        self.changed.set()

    def monitor(self):
        previous_tick = time.time()
        heartbeat = previous_tick
        while not self.closed.is_set():
            self.changed.wait(1)
            self.changed.clear()
            now = time.time()
            if now - previous_tick > 120:
                self.log('execution_gap', seconds=round(now - previous_tick, 3))
            previous_tick = now
            if self.backend.stopped.is_set():
                with self.lock:
                    connections = list(self.connections.values())
                for connection in connections:
                    connection.close(1011, 'Codex app-server exited')
                return
            with self.lock:
                pending = self.state.pending
                if now - heartbeat >= 60:
                    self.log('heartbeat', active_thread=self.state.active_thread,
                        phase=('waiting' if pending['confirmed'] else 'checking_quota') if pending else 'idle',
                        check_at=pending.get('next_check') if pending else None)
                    heartbeat = now
                if pending is None:
                    continue
                token = pending['generation']
                due = now >= pending.get('next_check', pending['target'] or 0)
                target = pending['target']
            if not due:
                remaining = max(0, int((target or pending['next_check']) - time.time()))
                self.title('codexx - quota reset %02d:%02d:%02d' % (remaining // 3600, remaining // 60 % 60, remaining % 60))
                continue
            try:
                self.log('quota_check_started', thread=pending['thread'], confirmed=pending['confirmed'])
                result = self.backend.rpc('account/rateLimits/read')
                self.log('quota_check_result', requested_bucket=self.bucket, buckets=quota_summary(result))
                paid = credits_available(result, self.bucket)
                target = None if paid else reset_time(result, self.bucket)
                ready = paid or target is None
                self.log('recovery_availability', thread=pending['thread'], credits_available=paid, quota_available=target is None and not paid)
                goal = None
                if ready:
                    goal = self.backend.rpc('thread/goal/get', {'threadId': pending['thread']}).get('goal')
                    self.log('recovery_goal_checked', thread=pending['thread'], status=goal.get('status') if goal else None)
                with self.lock:
                    pending = self.state.pending
                    if pending is None or pending['generation'] != token:
                        continue
                    if not ready:
                        was_confirmed = pending['confirmed']
                        pending['confirmed'] = True
                        pending['target'] = max(target + self.margin, time.time() + self.poll_seconds if target + self.margin <= time.time() else 0)
                        pending['next_check'] = min(pending['target'], time.time() + self.poll_seconds)
                        pending['failures'] = 0
                        self.log('countdown_extended' if was_confirmed else 'countdown_started', source='quota_query',
                            thread=pending['thread'], resets_at=target, check_at=pending['next_check'], target_at=pending['target'])
                    else:
                        # Serialize against manual requests; all settings remain on the same thread.
                        thread = pending['thread']
                        self.log('quota_recovered', thread=thread, reason='credits_available' if paid else 'quota_available')
                        if goal is not None:
                            status = goal.get('status')
                            if status not in ('usageLimited', 'active'):
                                self.state.cancel('goal_not_eligible:' + str(status))
                                continue
                            self.last_recovery_at[thread] = time.time()
                            self.backend.send_silent('thread/goal/set', {'threadId': thread, 'status': 'active', 'origin': 'user'})
                            self.state.cancel('goal_resume_submitting')
                            self.recovery_attempt = (dict(pending), self.state.generation)
                            self.title('codexx - goal resume submitted after quota reset')
                        else:
                            self.last_recovery_at[thread] = time.time()
                            self.backend.send_silent('turn/start', {'threadId': thread,
                                'input': [{'type': 'text', 'text': 'continue', 'text_elements': []}]})
                            self.state.cancel('continue_submitting')
                            self.recovery_attempt = (dict(pending), self.state.generation)
                            self.title('codexx - continue submitted after quota reset')
            except Exception as exc:
                self.last_error = str(exc)
                with self.lock:
                    if self.state.pending and self.state.pending['generation'] == token:
                        self.state.pending['failures'] += 1
                        delay = min(60, 2 ** min(self.state.pending['failures'], 6))
                        self.state.pending['next_check'] = time.time() + delay
                        self.log('quota_check_retry', thread=self.state.pending['thread'], error_type=type(exc).__name__,
                                 error_code=getattr(exc, 'code', None),
                                 failures=self.state.pending['failures'], retry_at=self.state.pending['next_check'])
                self.title('codexx - quota check failed; retry pending')

    def close(self):
        self.closed.set()
        self.changed.set()
        with self.lock:
            self.state.cancel('launcher_closing')
            connections = list(self.connections.values())
        for connection in connections:
            connection.close()
        self.server.shutdown()
        self.backend.close()
        self.log('bridge_closed')


def set_title(value):
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(value)


def native_call(command, env=None):
    """Inherit console handles. Never read keys or touch console modes."""
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        return subprocess.call(command, env=env)
    finally:
        signal.signal(signal.SIGINT, previous)


def native_information(argv):
    """Recognize real help/version options, excluding option values and literal input."""
    command = list(argv)
    kind = None
    index = 0
    while index < len(command):
        arg = command[index]
        if arg == '--':
            break
        if arg in VALUE_FLAGS:
            index += 2
            continue
        if arg in ('-h', '--help'):
            kind = 'help'
        elif arg in ('-v', '-V', '--version'):
            if kind is None:
                kind = 'version'
            if arg == '-v':
                command[index] = '--version'
        index += 1
    return kind, command


def print_wrapper_help(parser):
    print('codexx ' + VERSION)
    parser.print_help()
    print('Update/install: --codexx-check-update, --codexx-update, --codexx-no-update,')
    print('  --codexx-repository OWNER/REPO, --codexx-yes, --codexx-install')
    print('  --codexx-no-path (install without changing PATH or shell profiles)')
    print('\nUse like codex. -h/--help shows both help pages; -v/-V/--version shows both versions.')
    print('After a quota failure, poll credits/early resets and resume the same conversation or goal.')
    print('Native Codex owns terminal input, shortcuts and clipboard.\n', flush=True)


def main():
    from .updates import preflight
    handled, argv = preflight(sys.argv[1:])
    if handled is not None:
        return handled
    parser, args = arguments(argv)
    if args.version:
        print('codexx ' + VERSION)
        return 0
    log_directory = home() / 'logs'
    if args.logs:
        print(log_directory)
        return 0
    if args.help:
        print_wrapper_help(parser)
        return 0
    information, native_args = native_information(args.args)
    if information == 'help':
        print_wrapper_help(parser)
    elif information == 'version':
        print('codexx ' + VERSION, flush=True)
    if not args.codex:
        parser.error('codex executable not found; use CODEXX_CODEX or --codexx-bin')
    if information:
        return native_call([args.codex] + native_args)
    log = RunLog(log_directory)
    server_command = [args.codex] + args.server_options + ['app-server', '--stdio']
    if args.check:
        backend = Backend(server_command, log=log.emit)
        try:
            backend.rpc('initialize', {'clientInfo': {'name': 'codexx', 'version': VERSION}})
            backend.send({'method': 'initialized', 'params': {}})
            print(json.dumps(backend.rpc('account/rateLimits/read'), indent=2, ensure_ascii=False))
        finally:
            backend.close()
        return 0
    unsupported = any(arg.split('=', 1)[0] in ('--remote', '--remote-auth-token-env', '-p', '--profile', '--no-daemon') for arg in args.args)
    if args.native or unsupported or direct_command(args.args) or not sys.stdin.isatty() or not sys.stdout.isatty():
        log.emit('recovery_disabled', native=args.native, unsupported_options=unsupported,
                 administrative=direct_command(args.args), terminal=sys.stdin.isatty() and sys.stdout.isatty())
        if unsupported and not args.native:
            print('codexx: recovery disabled for remote/profile/no-daemon options; using native Codex.', file=sys.stderr)
        return native_call([args.codex] + args.args)
    bridge = Bridge(server_command, args.bucket, title=set_title, log=log.emit,
                    workspace=args.workspace, poll_seconds=args.poll_seconds, all_sessions=getattr(args, 'all-sessions') or ('resume' in args.args and '--all' in args.args))
    try:
        env = dict(os.environ, CODEXX_BRIDGE_TOKEN=bridge.token)
        command = [args.codex, '--remote', bridge.url, '--remote-auth-token-env', 'CODEXX_BRIDGE_TOKEN'] + args.args
        return native_call(command, env)
    finally:
        bridge.close()


if __name__ == '__main__':
    sys.exit(main())
