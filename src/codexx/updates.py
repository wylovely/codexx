"""Optional startup update checks; run before help, version, recovery or Codex."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import releases
from .installation import copy_binary, install, write_json
from .paths import executable_name, home
from .version import DEFAULT_REPOSITORY, RELEASE_PROTOCOL, VERSION


def options(argv):
    from .cli import VALUE_FLAGS
    flags = {'--codexx-check-update': 'check', '--codexx-update': 'update',
             '--codexx-no-update': 'skip', '--codexx-yes': 'yes', '--codexx-install': 'install',
             '--codexx-no-path': 'no_path'}
    result, rest = {}, []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--':
            rest.extend(argv[index:])
            break
        if arg in flags:
            result[flags[arg]] = True
        elif arg == '--codexx-repository' or arg.startswith('--codexx-repository='):
            if '=' in arg:
                result['repository'] = arg.split('=', 1)[1]
            else:
                index += 1
                if index >= len(argv):
                    raise ValueError('--codexx-repository requires OWNER/REPO')
                result['repository'] = argv[index]
        else:
            rest.append(arg)
            if arg in VALUE_FLAGS or arg in ('--codexx-bin', '--codexx-bucket', '--codexx-poll-seconds'):
                if index + 1 < len(argv):
                    index += 1
                    rest.append(argv[index])
        index += 1
    return result, rest


def apply_plan(plan_path, wait_seconds=90):
    directory = home()
    plan_path = Path(plan_path).resolve()
    try:
        if plan_path.parent.parent != directory / 'updates' or plan_path.name != 'plan.json':
            raise ValueError('Update plan is outside the installation staging directory')
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        if plan.get('protocol') != RELEASE_PROTOCOL or plan.get('version') != VERSION:
            raise ValueError('Update plan version/protocol mismatch')
        source = plan_path.parent / executable_name()
        if hashlib.sha256(source.read_bytes()).hexdigest() != plan['sha256']:
            raise ValueError('Staged executable checksum mismatch')
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                copy_binary(source, directory / executable_name())
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Other codexx sessions still hold the executable; exit them and retry')
                time.sleep(0.25)
        write_json(directory / 'install.json', {'version': VERSION, 'kind': 'binary'})
        write_json(directory / 'update-status.json', {'status': 'installed', 'version': VERSION})
        return 0
    except Exception as exc:
        write_json(directory / 'update-status.json', {'status': 'failed', 'error': str(exc)})
        return 1


def prepare_update(repo, release):
    stage = home() / 'updates' / uuid.uuid4().hex
    binary, manifest = releases.download(repo, release, stage)
    result = subprocess.run([str(binary), '--codexx-internal-version'], capture_output=True,
                            text=True, timeout=30, check=True)
    if result.stdout.strip() != manifest['version']:
        raise ValueError('Downloaded executable version does not match its manifest')
    plan = stage / 'plan.json'
    write_json(plan, {'protocol': RELEASE_PROTOCOL, 'version': manifest['version'],
                      'sha256': manifest['sha256']})
    log_dir = home() / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / 'updater.log').open('ab') as output:
        subprocess.Popen([str(binary), '--codexx-internal-apply', str(plan)], stdin=subprocess.DEVNULL,
                         stdout=output, stderr=output, start_new_session=os.name != 'nt',
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print('codexx: update staged. This invocation will exit; restart codexx after installation finishes.')
    print('codexx: update result: ' + str(home() / 'update-status.json'))


def preflight(argv):
    # Helpers never contact the network, prompt, parse Codex options, or start a backend.
    if argv == ['--codexx-internal-version']:
        print(VERSION)
        return 0, []
    if len(argv) == 2 and argv[0] == '--codexx-internal-apply':
        return apply_plan(argv[1]), []
    try:
        own, rest = options(argv)
    except ValueError as exc:
        print('codexx: ' + str(exc), file=sys.stderr)
        return 2, []
    explicit = own.get('check') or own.get('update')
    try:
        path = home() / 'config.json'
        config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        configured = own.get('repository', os.environ.get('CODEXX_REPOSITORY',
                             config.get('repository') or DEFAULT_REPOSITORY))
        repo = releases.repository(configured) if configured else ''
        if own.get('install'):
            if not getattr(sys, 'frozen', False):
                raise ValueError('Use python install.py --source to build and install from source')
            install(sys.executable, repo, configure_path=not own.get('no_path'))
            return 0, rest
        if 'repository' in own:
            config['repository'] = repo
            write_json(path, config)
        if own.get('skip') or not repo or (config.get('check_updates') is False and not explicit):
            if explicit:
                print('codexx: update check skipped (disabled or no repository configured).')
                return 0, rest
            return None, rest
        release = releases.latest(repo)
        if releases.version(release['tag_name']) <= releases.version(VERSION):
            if explicit:
                print('codexx: already up to date (' + VERSION + ').')
                return 0, rest
            return None, rest
        print('codexx: update available: ' + VERSION + ' -> ' + release['tag_name'], flush=True)
        if own.get('check'):
            return 0, rest
        accepted = own.get('yes', False)
        if not accepted and sys.stdin.isatty():
            accepted = input('Install codexx update before starting Codex? [y/N] ').strip().lower() in ('y', 'yes')
        if accepted:
            prepare_update(repo, release)
            return 0, rest
        if own.get('update'):
            print('codexx: update not installed. Use --codexx-update --codexx-yes for unattended installation.')
            return 1, rest
        return None, rest
    except (KeyboardInterrupt, EOFError):
        return 130, rest
    except Exception as exc:
        print('codexx: update/install unavailable: ' + str(exc), file=sys.stderr)
        return (1 if explicit or own.get('install') or 'repository' in own else None), rest
