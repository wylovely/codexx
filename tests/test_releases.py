import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from codexx import releases


def archive(key='windows-x86_64', extra=None, digest=None, manifest_overrides=None):
    binary = b'offline fake executable'
    name = 'codexx.exe' if key.startswith('windows') else 'codexx'
    manifest = {'protocol': 1, 'version': '0.4.1', 'platform': key, 'executable': name,
                'sha256': digest or hashlib.sha256(binary).hexdigest()}
    manifest.update(manifest_overrides or {})
    files = {name: binary, 'manifest.json': json.dumps(manifest).encode()}
    files.update(extra or {})
    output = io.BytesIO()
    if key.startswith('windows'):
        with zipfile.ZipFile(output, 'w') as stream:
            for filename, data in files.items():
                stream.writestr(filename, data)
    else:
        with tarfile.open(fileobj=output, mode='w:gz') as stream:
            for filename, data in files.items():
                member = tarfile.TarInfo(filename)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
    return output.getvalue()


class ReleaseTests(unittest.TestCase):
    def test_supported_platforms(self):
        for system, arch, expected in [('Windows', 'AMD64', 'windows-x86_64'),
                ('Darwin', 'arm64', 'macos-arm64'), ('Darwin', 'x86_64', 'macos-x86_64'),
                ('Linux', 'aarch64', 'linux-arm64'), ('Linux', 'x86_64', 'linux-x86_64')]:
            self.assertEqual(releases.platform_key(system, arch), expected)
        with self.assertRaises(ValueError):
            releases.platform_key('Unknown', 'x86_64')

    def test_repository_and_version_validation(self):
        self.assertEqual(releases.repository('https://github.com/owner/repo.git'), 'owner/repo')
        self.assertGreater(releases.version('v0.10.0'), releases.version('0.9.0'))
        for value in ('https://example.org/owner/repo', 'owner/repo/extra', '../repo?x', '../repo', 'owner/..'):
            with self.assertRaises(ValueError):
                releases.repository(value)
        for value in ('latest', 'v1.0.0-rc1', '../1.0.0'):
            with self.assertRaises(ValueError):
                releases.version(value)

    def test_missing_and_duplicate_checksums_fail(self):
        digest = 'a' * 64
        line = digest + '  test.zip\n'
        self.assertEqual(releases.checksum(line, 'test.zip'), digest)
        for text in ('', line + line, 'bad test.zip'):
            with self.assertRaises(ValueError):
                releases.checksum(text, 'test.zip')

    def test_unpack_supported_archives(self):
        for key in ('windows-x86_64', 'macos-arm64', 'linux-x86_64'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                binary, manifest = releases.unpack(archive(key), releases.asset_name(key), temp, 'v0.4.1', key)
                self.assertEqual(binary.read_bytes(), b'offline fake executable')
                self.assertEqual(manifest['platform'], key)

    def test_archive_paths_manifest_and_hash_are_verified(self):
        cases = [archive(extra={'../escape': b'bad'}), archive(digest='0' * 64),
                 archive(manifest_overrides={'version': '9.9.9'}),
                 archive(manifest_overrides={'platform': 'linux-arm64'}),
                 archive(manifest_overrides={'protocol': 2})]
        for data in cases:
            with self.subTest(data=data[:8]), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    releases.unpack(data, 'test.zip', temp, '0.4.1', 'windows-x86_64')
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_tar_symlink_is_rejected(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as stream:
            member = tarfile.TarInfo('codexx')
            member.type = tarfile.SYMTYPE
            member.linkname = '/tmp/unrelated'
            stream.addfile(member)
            member = tarfile.TarInfo('manifest.json')
            stream.addfile(member, io.BytesIO())
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            releases.unpack(output.getvalue(), 'test.tar.gz', temp, '0.4.1', 'linux-x86_64')

    def test_download_uses_matching_release_assets_and_archive_hash(self):
        key = 'windows-x86_64'
        name = releases.asset_name(key)
        data = archive(key)
        digest = hashlib.sha256(data).hexdigest()
        prefix = 'https://github.com/owner/repo/releases/download/v0.4.1/'
        release = {'tag_name': 'v0.4.1', 'assets': [
            {'name': file, 'browser_download_url': prefix + file} for file in (name, 'SHA256SUMS')]}
        with tempfile.TemporaryDirectory() as temp, patch('codexx.releases.request',
                side_effect=[(digest + '  ' + name).encode(), data]):
            self.assertTrue(releases.download('owner/repo', release, temp, key)[0].exists())
        with tempfile.TemporaryDirectory() as temp, patch('codexx.releases.request',
                side_effect=[('0' * 64 + '  ' + name).encode(), data]), self.assertRaises(ValueError):
            releases.download('owner/repo', release, temp, key)

    def test_asset_url_cannot_point_to_an_unrelated_repository(self):
        with self.assertRaises(ValueError):
            releases.asset_url('owner/repo', {'tag_name': 'v0.4.1', 'assets': [
                {'name': 'test.zip', 'browser_download_url': 'https://example.org/test.zip'}]}, 'test.zip')


if __name__ == '__main__':
    unittest.main()
