"""Source installer; use the Python environment in which you want build dependencies."""
import argparse
from pathlib import Path
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='store_true', required=True)
    parser.add_argument('--repository', default='', help='Optional GitHub OWNER/REPO for future updates')
    parser.add_argument('--skip-build', action='store_true', help='Install an already built dist/codexx binary')
    parser.add_argument('--no-path', action='store_true', help='Do not modify the user PATH or shell profile')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not args.skip_build:
        subprocess.run([sys.executable, str(root / 'build.py')], check=True)
    binary = root / 'dist' / ('codexx.exe' if os.name == 'nt' else 'codexx')
    command = [str(binary), '--codexx-install', '--codexx-no-update']
    if args.repository:
        command += ['--codexx-repository', args.repository]
    if args.no_path:
        command += ['--codexx-no-path']
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
