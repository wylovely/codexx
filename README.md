# codexx

Codex CLI 的轻量启动器。保留原生终端、快捷键和剪贴板体验，监听结构化限流事件，在额度恢复后继续原会话；goal 会话使用原生目标恢复接口。

支持 Windows x64、macOS Intel / Apple Silicon、Linux x64 / ARM64。运行前需自行安装并登录支持 `--remote` 和 `app-server --stdio` 的 Codex CLI。已在 Codex CLI 0.163.0 验证核心协议；其他版本由平台 CI 和实际使用继续验证。

## 目录约定

- `codexx/`：Git 开发仓库，包含 `src/`、`tests/`、构建和安装脚本。
- `~/.codexx/`：安装目录，仅存放可执行文件、运行配置、日志、更新暂存和上一个版本备份。
- 源码和单元测试不复制到安装目录。`CODEXX_HOME` 可更改安装及运行数据目录；不影响 Codex 自己的 `.codex`。

## 源码安装

仓库地址尚未配置，下列 `REPOSITORY_URL` 需要替换为将来公开的地址。

```sh
git clone REPOSITORY_URL codexx
cd codexx
```

选择一个 Python 3.9+ 环境；安装脚本在这个环境中安装构建依赖，构建本平台独立可执行文件，再安装到当前用户目录并配置 PATH。

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python install.py --source
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe install.py --source
```

也可直接用已有环境的 Python：`python install.py --source`。依赖下载需要联网；不要求管理员权限。安装完成后重新打开终端执行 `codexx`。源码安装的最终产物也是独立可执行文件，运行时无需保留 Python 环境或开发仓库。

如需自行管理 PATH，在源码安装命令加 `--no-path`；原生安装命令对应 `--codexx-no-path`。已有构建可用 `python install.py --source --skip-build` 安装。

如果要为安装启用远程检查，在上面的命令后添加 `--repository OWNER/REPO`。不提供地址时直接跳过更新检查。

仅开发、不安装本机命令：

```sh
python -m pip install -e ".[build]"
python -m unittest discover -s tests -v
python -m codexx --codexx-no-update --codexx-help
```

## Release 脚本安装

不需要 Python。脚本下载当前平台的 GitHub 最新**已发布稳定版**、验证 SHA256、调用下载的可执行文件进行安装。先下载仓库内脚本再执行，例如：

macOS / Linux：

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install.sh -o install-codexx.sh
sh install-codexx.sh --repository OWNER/REPO
```

Windows PowerShell：

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install.ps1 -OutFile install-codexx.ps1
powershell -ExecutionPolicy Bypass -File .\install-codexx.ps1 -Repository OWNER/REPO
```

当前没有实际远程地址，`OWNER/REPO` 是说明占位符，不能原样执行。脚本未收到地址时不下载任何内容。

Windows 安装器添加当前用户 PATH，并通知终端宿主环境变化；已有宿主可能仍需重开。POSIX 安装器按 `$SHELL` 向 `.zshrc`、`.bashrc` 或 `.profile` 追加 PATH 行；当前终端可执行 `export PATH="$HOME/.codexx:$PATH"`。自定义 `CODEXX_HOME` 时使用对应目录。POSIX 脚本依赖 curl、tar 和 sha256sum 或 shasum；Windows 脚本依赖 PowerShell 5.1+。

## 使用

```sh
codexx
codexx -C /path/to/project
codexx resume
codexx resume --all
codexx --codexx-poll-seconds 15
codexx -h
codexx -v
```

`-h/--help` 先显示 codexx 信息再显示原生帮助；`-v/-V/--version` 依次显示两个版本。`--codexx-help` 和 `--codexx-version` 只显示启动器信息。

仅在当前工作会话出现结构化额度失败后启动恢复；启动、普通错误和历史错误文本不会触发。默认每 30 秒读取最新额度：

1. 可用付费 credits 或无限 credits：不等旧刷新时间，恢复工作。
2. 免费额度提前恢复（例如用户主动兑换重置次数）：下次轮询即恢复。
3. 仍然限流：保留倒计时，刷新时间后额外等待 5 秒再查询；网络错误保留任务并退避重试。

codexx 不自动兑换重置次数。普通会话发送 `continue`；处于 `usageLimited` 或 `active` 的 goal 恢复为 active，保留目标和预算。手动暂停、blocked、预算耗尽或已完成的 goal 不自动恢复。临时辅助会话不会抢占工作会话。用户手动输入新请求、切换会话、暂停目标或中断会取消相应等待；尚未提交的输入草稿不在协议可见范围内。

`--codexx-native` 关闭恢复；指定原生 remote/profile/no-daemon 选项时也直接使用原生 Codex。远程 `/resume` 原生界面显示 All，codexx 默认按当前项目过滤；使用 `--codexx-all-sessions` 或 `resume --all` 可跨目录浏览。

## 更新

更新预检**先于帮助、版本、参数校验、额度监控和启动 Codex**。远程来源优先级：当前命令 `--codexx-repository` > 环境变量 `CODEXX_REPOSITORY` > `~/.codexx/config.json` > 源码 `DEFAULT_REPOSITORY`。当前默认远程地址为空，不联网、不提示。

```sh
codexx --codexx-repository OWNER/REPO --codexx-check-update
codexx --codexx-check-update
codexx --codexx-update
codexx --codexx-update --codexx-yes
codexx --codexx-no-update -h
```

命令行提供的仓库地址会持久化。可接受 `OWNER/REPO` 或 `https://github.com/OWNER/REPO`；仅支持公开 GitHub 稳定 Release。启动发现新版本会询问，默认不更新。非交互启动只提示，不自动更新；`--codexx-yes` 才允许无人值守安装。查询失败时正常启动继续；显式查询/更新失败返回非零状态。

同意更新后，会验证 Release archive 的 SHA256、协议、平台、版本和内部可执行文件哈希，然后启动新版本的后台更新助手，当前调用退出，**本次不启动 Codex**。助手原子替换安装文件，保留 `.previous` 备份，并写入 `update-status.json`；完成后重新运行 `codexx`。Windows 上请先退出其他 codexx 会话，助手最多等候 90 秒，不会结束任何进程。若失败，旧可执行文件保留，可关闭其他会话后重试。暂存包保留在 `updates/` 便于排错，可在没有更新进行时删除旧暂存目录。

配置例子（没有远程地址就保持空字符串）：

```json
{"repository": "", "check_updates": true}
```

将 `check_updates` 设为 false 可关闭自动检查；显式 `--codexx-check-update` / `--codexx-update` 仍有效。

## 构建和发布

```sh
python build.py
python scripts/package_release.py
```

独立程序输出到 `dist/`，Release 归档输出到 `release/`。每种 OS/CPU 需在对应系统构建，不能把 Windows EXE 复制到 Mac/Linux 使用。

- `Tests` 工作流：Windows、macOS、Linux 测试；另覆盖 Linux Python 3.9。
- `Native release builds`：手动触发仅生成可下载的 Actions 产物；推送与 `src/codexx/version.py` 一致的 `vX.Y.Z` tag 后，构建五个平台，集中校验和后创建 **draft Release**，供维护者检查后手动发布。
- 维护者未配置实际仓库前，不设置任何默认远程；提供仓库后可修改 `src/codexx/version.py` 的 `DEFAULT_REPOSITORY`。

Linux 预构建基于 Ubuntu 22.04（glibc 2.35+），不适用于 Alpine/musl。macOS ARM 构建基于 macOS 14，Intel 基于 macOS 15；没有 Apple Developer 签名或 notarization，跨机器下载可能触发系统安全检查。Windows 构建未作代码签名。其他平台可尝试源码构建，但不在预构建矩阵内。

Release 数据约定见 [协议文档](docs/release-protocol.md)。仓库初始化、离线测试和 Windows 本机构建可在当前环境完成；真实 GitHub 工作流、远程安装及 Mac/Linux 实机结果需提供地址后验证。

## 日志与协议

`codexx --codexx-logs` 输出日志目录。日志记录会话 ID、额度、倒计时、恢复原因、恢复提交和确认，不记录提示词、目标正文或认证令牌。Codex 的原生断连提示可能提到远程会话继续运行；codexx 退出时其私有 backend 会关闭，请通过新一次 `codexx resume` 恢复历史。

本项目独立于 OpenAI，不分发 Codex CLI。许可证：[MIT](LICENSE)。
