import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codexx.releases import platform_key
from codexx.version import VERSION

if platform_key() != os.environ['EXPECTED_PLATFORM']:
    raise SystemExit('Runner architecture does not match the intended release asset')
if os.environ.get('GITHUB_REF', '').startswith('refs/tags/'):
    if os.environ['GITHUB_REF_NAME'] != 'v' + VERSION:
        raise SystemExit('Tag must equal v' + VERSION + '; update src/codexx/version.py first')
