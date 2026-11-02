"""Offline JSON-RPC fixture. Never imported by codexx."""
import json
import sys
import time

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

queries = 0
fail_first = False
cached_mode = False
initialized = False
goal_status = None
quota_sequence = None


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    params = message.get('params') or {}
    identity = message.get('id')
    if identity is None:
        continue
    if method is None and identity == 'approval-1':
        emit({'method': 'test/approvalAnswer', 'params': message.get('result')})
    elif method == 'initialize':
        if initialized:
            emit({'id': identity, 'error': {'code': -32600, 'message': 'Already initialized'}})
            continue
        initialized = True
        emit({'id': identity, 'result': {'userAgent': 'offline-test'}})
    elif method == 'test/requestApproval':
        emit({'id': identity, 'result': {}})
        emit({'id': 'approval-1', 'method': 'item/commandExecution/requestApproval',
              'params': {'threadId': 'A', 'turnId': 'T', 'itemId': 'I'}})
    elif method == 'thread/start' and params.get('ephemeral'):
        emit({'id': identity, 'result': {'thread': {'id': 'helper', 'ephemeral': True}}})
    elif method == 'test/goalStatus':
        goal_status = params.get('status')
        emit({'id': identity, 'result': {}})
    elif method == 'thread/goal/get':
        emit({'id': identity, 'result': {'goal': {'status': goal_status} if goal_status else None}})
    elif method == 'thread/goal/set':
        goal_status = params['status']
        emit({'id': identity, 'result': {'goal': {'status': goal_status}}})
        emit({'method': 'test/goalResumed', 'params': params})
    elif method == 'test/quotaFailure':
        queries = 0
        emit({'id': identity, 'result': {}})
        emit({'method': 'turn/completed', 'params': {'threadId': 'A', 'turn': {'id': 'second-quota', 'status': 'failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}}})
    elif method == 'test/quotaSequence':
        quota_sequence = list(params['results'])
        emit({'id': identity, 'result': {}})
    elif method == 'account/rateLimits/read':
        if quota_sequence:
            result = quota_sequence.pop(0) if len(quota_sequence) > 1 else quota_sequence[0]
            emit({'id': identity, 'result': result})
            continue
        queries += 1
        if fail_first and queries == 1:
            emit({'id': identity, 'error': {'code': -32001, 'message': 'Temporary unavailable'}})
            continue
        effective_queries = queries - int(fail_first)
        emit({'id': identity, 'result': {'rateLimits': {'limitId': 'codex',
            'primary': {'usedPercent': 100 if effective_queries == 1 and not cached_mode else 0, 'resetsAt': time.time() + 0.05}}}})
    elif method == 'turn/start':
        turn = {'id': 'turn-' + str(identity), 'status': 'inProgress'}
        thread = params['threadId']
        text = params['input'][0]['text']
        emit({'id': identity, 'result': {'turn': turn}})
        emit({'method': 'turn/started', 'params': {'threadId': thread, 'turn': turn}})
        if text.startswith('hit'):
            fail_first = 'retry' in text
            cached_mode = 'cached' in text
            if cached_mode:
                emit({'method': 'account/rateLimits/updated', 'params': {'rateLimits': {'limitId': 'codex',
                    'primary': {'usedPercent': 100, 'resetsAt': time.time() + 0.05}}}})
                emit({'method': 'account/rateLimits/updated', 'params': {'rateLimits': {'limitId': 'premium', 'primary': None, 'secondary': None}}})
            error = {'message': 'Usage exhausted', 'codexErrorInfo': 'usageLimitExceeded'}
            emit({'method': 'error', 'params': {'threadId': thread, 'turnId': turn['id'], 'error': error, 'willRetry': False}})
            turn.update(status='failed', error=error)
        else:
            emit({'method': 'item/agentMessage/delta', 'params': {'threadId': thread, 'delta': text}})
            turn.update(status='completed', error=None)
        emit({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': turn}})
    else:
        emit({'id': identity, 'result': {'echo': params}})
