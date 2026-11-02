"""Pack a native build according to docs/release-protocol.md."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codexx.releases import asset_name, platform_key
from codexx.version import RELEASE_PROTOCOL, VERSION


def main():
    key = platform_key()
    name = 'codexx.exe' if key.startswith('windows-') else 'codexx'
    binary = (ROOT / 'dist' / name).read_bytes()
    manifest = json.dumps({'protocol': RELEASE_PROTOCOL, 'version': VERSION, 'platform': key,
        'executable': name, 'sha256': hashlib.sha256(binary).hexdigest()}, indent=2).encode('utf-8')
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    target = output / asset_name(key)
    if target.suffix == '.zip':
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, binary)
            archive.writestr('manifest.json', manifest)
    else:
        with tarfile.open(target, 'w:gz') as archive:
            for filename, data in ((name, binary), ('manifest.json', manifest)):
                info = tarfile.TarInfo(filename)
                info.size = len(data)
                info.mode = 0o755 if filename == name else 0o644
                archive.addfile(info, io.BytesIO(data))
    (output / ('SHA256SUMS-' + key)).write_text(
        hashlib.sha256(target.read_bytes()).hexdigest() + '  ' + target.name + '\n', encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
