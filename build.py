"""Build on the target OS/CPU using the Python environment invoking this script."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-install', action='store_true', help='Dependencies are already installed')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not args.no_install:
        subprocess.run([sys.executable, '-m', 'pip', 'install', str(root) + '[build]'], check=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--console',
                    '--name', 'codexx', '--paths', str(root / 'src'),
                    '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
                    '--specpath', str(root / 'build'), str(root / 'src/codexx/__main__.py')],
                   cwd=str(root), check=True)
    print('Built:', root / 'dist' / ('codexx.exe' if os.name == 'nt' else 'codexx'))


if __name__ == '__main__':
    main()
