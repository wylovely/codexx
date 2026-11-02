"""Runtime paths; never put development sources in the installation directory."""
import os
from pathlib import Path


def home():
    return Path(os.environ.get('CODEXX_HOME') or Path.home() / '.codexx').expanduser().resolve()


def executable_name():
    return 'codexx.exe' if os.name == 'nt' else 'codexx'
