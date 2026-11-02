"""GitHub stable-release protocol and bounded, checksummed downloads (stdlib only)."""
import hashlib
import io
import json
import platform
import re
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .version import RELEASE_PROTOCOL


def repository(value):
    value = value.strip().rstrip('/')
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):]
    if value.endswith('.git'):
        value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('Repository must be OWNER/REPO or https://github.com/OWNER/REPO')
    if any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('Repository cannot contain path traversal components')
    return value


def version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Expected a stable vMAJOR.MINOR.PATCH version')
    return tuple(map(int, match.groups()))


def platform_key(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    systems = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}
    architectures = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'arm64', 'aarch64': 'arm64'}
    if system not in systems or machine not in architectures:
        raise ValueError('Unsupported platform: %s %s' % (system, machine))
    return systems[system] + '-' + architectures[machine]


def asset_name(key=None):
    key = key or platform_key()
    return 'codexx-' + key + ('.zip' if key.startswith('windows-') else '.tar.gz')


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != 'https':
            raise ValueError('Refusing a non-HTTPS download redirect')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def request(url, limit=2 * 1024 * 1024, timeout=4):
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('HTTPS is required')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'codexx-release-client'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(SecureRedirect()).open(req, timeout=timeout) as response:
        chunks, total = [], 0
        while True:
            chunk = response.read(min(65536, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError('Release response exceeds size limit')
            chunks.append(chunk)
    return b''.join(chunks)


def latest(repo):
    repo = repository(repo)
    result = json.loads(request('https://api.github.com/repos/' + repo + '/releases/latest'))
    if result.get('draft') or result.get('prerelease'):
        raise ValueError('Expected a published stable release')
    version(result['tag_name'])
    return result


def asset_url(repo, release, name):
    prefix = 'https://github.com/' + repository(repo) + '/releases/download/' + release['tag_name'] + '/'
    for asset in release.get('assets', []):
        if asset.get('name') == name:
            url = asset['browser_download_url']
            if url != prefix + name:
                raise ValueError('Unexpected release asset URL')
            return url
    raise ValueError('Release is missing ' + name)


def checksum(text, name):
    matches = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == name:
            if not re.fullmatch(r'[a-fA-F0-9]{64}', fields[0]):
                raise ValueError('Invalid SHA256 checksum')
            matches.append(fields[0].lower())
    if len(matches) != 1:
        raise ValueError('Missing or duplicate checksum for ' + name)
    return matches[0]


def unpack(data, name, destination, expected_version, key=None):
    """Extract only two exact regular-file names; never extract archive paths."""
    key = key or platform_key()
    executable = 'codexx.exe' if key.startswith('windows-') else 'codexx'
    expected = {executable, 'manifest.json'}
    if name.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != 2 or {m.filename for m in members} != expected:
                raise ValueError('Unexpected archive entries')
            files = {}
            for member in members:
                mode = member.external_attr >> 16
                if (mode & 0o170000) == 0o120000 or member.file_size > 128 * 1024 * 1024:
                    raise ValueError('Invalid archive member')
                files[member.filename] = archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = archive.getmembers()
            if len(members) != 2 or {m.name for m in members} != expected:
                raise ValueError('Unexpected archive entries')
            files = {}
            for member in members:
                if not member.isfile() or member.size > 128 * 1024 * 1024:
                    raise ValueError('Invalid archive member')
                files[member.name] = archive.extractfile(member).read()
    if len(files['manifest.json']) > 65536:
        raise ValueError('Manifest is too large')
    manifest = json.loads(files['manifest.json'])
    if (manifest.get('protocol') != RELEASE_PROTOCOL or manifest.get('platform') != key
            or version(manifest.get('version', '')) != version(expected_version)
            or manifest.get('executable') != executable):
        raise ValueError('Release manifest does not match requested version/platform/protocol')
    if hashlib.sha256(files[executable]).hexdigest() != manifest.get('sha256'):
        raise ValueError('Executable checksum mismatch')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / executable
    target.write_bytes(files[executable])
    target.chmod(0o755)
    return target, manifest


def download(repo, release, destination, key=None):
    name = asset_name(key)
    sums = request(asset_url(repo, release, 'SHA256SUMS')).decode('utf-8')
    data = request(asset_url(repo, release, name), limit=256 * 1024 * 1024, timeout=15)
    if hashlib.sha256(data).hexdigest() != checksum(sums, name):
        raise ValueError('Release archive checksum mismatch')
    return unpack(data, name, destination, release['tag_name'], key)
