# codexx

Codex CLI 启动器，在触发额度限制后等待恢复并自动继续工作，保留原生终端、快捷键和复制粘贴体验。

支持 Windows x64、macOS Intel / Apple Silicon、Linux x64 / ARM64。使用前需安装并登录 Codex CLI，确保 `codex` 命令可用。

## 自动恢复

- 触发额度限制后，默认每 **30 秒**检查一次，额度恢复即继续原会话。
- 充值后有可用 credits，或手动使用重置次数后额度提前恢复，无需等到原刷新时间。
- 普通会话自动发送 `continue`；goal 会话执行相当于 `/goal resume` 的操作。
- 手动提交新请求、切换会话或中断时取消对应等待；手动暂停、已结束或预算耗尽的 goal 不自动恢复。

codexx 不会自动兑换重置次数。使用 `--codexx-native` 或原生 remote/profile/no-daemon 参数时，不启用自动恢复。

## 安装

默认安装到 `~/.codexx` 并配置 PATH，完成后重新打开终端。无需放入 Codex 使用的 `.codex` 目录。

### 脚本安装（推荐）

自动下载 [最新正式版](https://github.com/wylovely/codexx/releases/latest) 的对应平台安装包，无需 Python。

**Windows PowerShell：**

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/wylovely/codexx/main/scripts/install.ps1 -OutFile install-codexx.ps1
powershell -ExecutionPolicy Bypass -File .\install-codexx.ps1 -Repository wylovely/codexx
```

已有 `codexx` 命令、只需升级程序时，可给上述命令加 `-NoPath`，跳过 PATH 修改。安装失败会显示原始错误并保留 `install.log`，按输出路径查看详情。

**macOS / Linux：**

```sh
curl -fsSL https://raw.githubusercontent.com/wylovely/codexx/main/scripts/install.sh -o install-codexx.sh
sh install-codexx.sh --repository wylovely/codexx
```

### 源码安装

需要 Python 3.9+ 和网络连接，安装脚本会在所选 Python 环境中安装依赖、构建并安装独立可执行文件。

```sh
git clone https://github.com/wylovely/codexx.git
cd codexx
```

**Windows PowerShell：**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe install.py --source --repository wylovely/codexx
```

**macOS / Linux：**

```sh
python3 -m venv .venv
.venv/bin/python install.py --source --repository wylovely/codexx
```

也可以用已有环境执行 `python install.py --source --repository wylovely/codexx`。安装完成后运行 codexx 不再需要 Python 或开发仓库。

### 手动安装 Windows 包

从 [Releases](https://github.com/wylovely/codexx/releases) 下载并解压 `codexx-windows-x86_64.zip`，在解压目录执行：

```powershell
.\codexx.exe --codexx-install --codexx-repository wylovely/codexx
```

Windows 安装或更新前请退出其他 codexx 会话。Linux 预构建需要 glibc 2.35+，不适用于 Alpine/musl。

## 使用

像使用 `codex` 一样启动：

```sh
codexx
codexx -C /path/to/project
codexx resume
codexx resume --all
```

启动后也可以使用 `/resume`，默认按当前项目筛选会话；`resume --all` 查看所有项目。

```sh
codexx --codexx-poll-seconds 15   # 将轮询间隔改为 15 秒
codexx --codexx-native           # 关闭自动恢复，直接使用原生 Codex
codexx --codexx-logs             # 查看日志目录
codexx -h                       # 查看 codexx 和 Codex 帮助
codexx -v                       # 查看两者版本
```

## 更新

上述安装命令会配置更新仓库。普通启动不会等待网络：启动时按需在后台检查，发现新版本后在下次启动时提示，运行期间不打断会话。正常检查间隔至少一小时，失败后五分钟内不再尝试；未配置仓库则跳过。

```sh
codexx --codexx-check-update    # 仅检查更新
codexx --codexx-update          # 检查并选择更新
codexx --codexx-no-update       # 本次跳过检查并启动
```

手动检查和更新命令会立即联网；只有显式执行更新命令时才询问安装。

旧安装未配置仓库时，先执行一次：

```sh
codexx --codexx-repository wylovely/codexx --codexx-check-update
```

更新完成后重新运行 `codexx`。

开发约定见 [AGENTS.md](AGENTS.md)，发布包格式见 [Release 协议](docs/release-protocol.md)。许可证：[MIT](LICENSE)。
