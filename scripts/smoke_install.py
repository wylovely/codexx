"""Exercise a built native binary in an isolated install root; never touch user PATH."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codexx.paths import executable_name
from codexx.version import VERSION


def main():
    binary = ROOT / 'dist' / executable_name()
    with tempfile.TemporaryDirectory(prefix='codexx-install-smoke-') as temp:
        home = Path(temp)
        env = dict(os.environ, CODEXX_HOME=str(home), CODEXX_REPOSITORY='')
        subprocess.run([str(binary), '--codexx-install', '--codexx-no-path', '--codexx-no-update'],
                       env=env, check=True, timeout=45)
        installed = home / executable_name()
        output = subprocess.check_output([str(installed), '--codexx-no-update', '--codexx-version'],
                                         env=env, text=True, timeout=30)
        assert output.strip() == 'codexx ' + VERSION
        assert {p.name for p in home.iterdir()} == {executable_name(), 'config.json', 'install.json'}
        stage = home / 'updates' / 'smoke-test'
        stage.mkdir(parents=True)
        helper = stage / executable_name()
        shutil.copy2(binary, helper)
        plan = stage / 'plan.json'
        plan.write_text(json.dumps({'protocol': 1, 'version': VERSION,
            'sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}), encoding='utf-8')
        subprocess.run([str(helper), '--codexx-internal-apply', str(plan)], env=env, check=True, timeout=45)
        assert json.loads((home / 'update-status.json').read_text())['status'] == 'installed'
        assert (home / (executable_name() + '.previous')).exists()
        print('Native install, version, clean runtime layout, and update helper: passed')


if __name__ == '__main__':
    main()
