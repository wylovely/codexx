# Release protocol v1

## Discovery

Repository configuration is an empty string or GitHub `OWNER/REPO`. Empty means **zero network access** for update discovery. A configured client reads `GET https://api.github.com/repos/OWNER/REPO/releases/latest` with a bounded timeout and response size. Only published stable `vMAJOR.MINOR.PATCH` tags are accepted; compare integer tuples, never lexicographic strings. Do not downgrade or treat prereleases as stable.

Version is defined in `src/codexx/version.py`. Tag builds must match it exactly. No default remote is currently baked in.

## Assets

Each release contains `SHA256SUMS` (UTF-8, standard `hex  filename` rows) and these native archives:

| Platform | Asset | Executable inside |
| --- | --- | --- |
| Windows x64 | codexx-windows-x86_64.zip | codexx.exe |
| macOS Intel | codexx-macos-x86_64.tar.gz | codexx |
| macOS Apple Silicon | codexx-macos-arm64.tar.gz | codexx |
| Linux x64 | codexx-linux-x86_64.tar.gz | codexx |
| Linux ARM64 | codexx-linux-arm64.tar.gz | codexx |

Archives contain exactly the named executable and `manifest.json`, both regular files at the archive root. No enclosing folder, links or extra paths. SHA256SUMS identifies each compressed archive. The manifest identifies the uncompressed executable:

```json
{
  "protocol": 1,
  "version": "0.4.0",
  "platform": "linux-x86_64",
  "executable": "codexx",
  "sha256": "64 lowercase hexadecimal characters"
}
```

The runtime updater checks both hashes, manifest version against the selected release tag, expected platform and protocol, and the new binary's internal version before scheduling replacement. Bootstrap scripts validate the archive SHA256 and extract only the single exact executable name; native self-install writes runtime metadata. Release trust is rooted in the configured GitHub repository and HTTPS; checksums detect corruption and mismatched assets, and are not an independent signing authority.

## Native installation / replacement

`--codexx-install` is exposed by the native binary; source users invoke `python install.py --source` to build it first. It copies only the executable and writes `config.json` / `install.json`, retaining existing logs and settings. No source tree or Python environment is installed under `.codexx`.

All persistent runtime files resolve under `CODEXX_HOME`, default `~/.codexx`:

```text
codexx[.exe]
codexx[.exe].previous     # optional rollback copy
config.json
install.json
logs/
updates/<uuid>/          # verified native executable and plan.json
update-status.json
```

Private helper commands are not Codex options: `--codexx-internal-version` and `--codexx-internal-apply PLAN`. They bypass all network checks. The helper accepts only a plan named `plan.json` directly inside an update stage under this installation's `updates/`, matching its own protocol and version; it rechecks the executable hash. Replacement uses a sibling temporary file plus `os.replace`. Windows file locking is retried up to 90 seconds without stopping user processes. Completion/failure is recorded, and users restart the command. Pending updates never start a Codex session.

Installers and updater must not read or mutate Codex's authentication, `.codex`, or other sessions. Remote checks only access release metadata/assets, not account information.
