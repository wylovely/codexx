"""Install a native binary and expose it on the current user's PATH."""
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

from .paths import executable_name, home
from .version import VERSION


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def copy_binary(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, stage = tempfile.mkstemp(prefix='.codexx-', dir=str(target.parent))
    os.close(fd)
    try:
        shutil.copyfile(source, stage)
        os.chmod(stage, 0o755)
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + '.previous'))
        os.replace(stage, target)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)


def add_path(directory):
    directory = str(Path(directory).resolve())
    if os.name == 'nt':
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            try:
                value, kind = winreg.QueryValueEx(key, 'Path')
            except FileNotFoundError:
                value, kind = '', winreg.REG_EXPAND_SZ
            if directory.casefold() not in [p.rstrip('\\').casefold() for p in value.split(';')]:
                winreg.SetValueEx(key, 'Path', 0, kind, value.rstrip(';') + ';' + directory)
        # Notify shell hosts that future terminals should inherit the new user environment.
        import ctypes
        from ctypes import wintypes
        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(wintypes.HWND(0xFFFF), 0x001A, 0,
            ctypes.c_wchar_p('Environment'), 0x0002, 2000, ctypes.byref(result))
    else:
        shell = Path(os.environ.get('SHELL', '/bin/sh')).name
        profile = Path.home() / ('.zshrc' if shell == 'zsh' else '.bashrc' if shell == 'bash' else '.profile')
        line = 'export PATH=' + shlex.quote(directory) + ':"$PATH"'
        text = profile.read_text(encoding='utf-8') if profile.exists() else ''
        if line not in text.splitlines():
            with profile.open('a', encoding='utf-8') as stream:
                stream.write('\n# codexx\n' + line + '\n')
    print('PATH configured for future terminals. Open a new terminal to use codexx.')


def install(source, repo='', directory=None, configure_path=True):
    directory = Path(directory) if directory is not None else home()
    target = directory / executable_name()
    config_path = directory / 'config.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    if repo:
        config['repository'] = repo
    config.setdefault('repository', '')
    copy_binary(source, target)
    write_json(config_path, config)
    write_json(directory / 'install.json', {'version': VERSION, 'kind': 'binary'})
    if configure_path:
        add_path(directory)
    print('Installed:', target)
    return target
