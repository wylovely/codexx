# 项目协作与提交规范

## 项目约定

- 开发源码位于 `src/codexx/`，测试位于 `tests/`；安装、打包脚本及协议说明分别位于 `scripts/` 和 `docs/`。
- `.codexx` 是用户安装及运行数据目录，不作为开发目录，不向其中复制源码或测试。
- 保持 Windows、macOS、Linux 的兼容性。使用 `pathlib`、当前用户目录或明确配置解析路径，不硬编码盘符、用户名或开发机目录。
- 未配置远程仓库时，更新检查不得联网。保留原生 Codex 的参数、终端和剪贴板行为。
- 修复问题时增加能够复现实际行为的必要测试；文档等低风险修改不必新增实现镜像测试。

## Commit 格式

使用 Conventional Commits，格式为：

```text
<type>(<scope>): <subject>

<可选正文：修改原因、行为变化、验证结果>

<可选页脚：关联 issue 或 BREAKING CHANGE>
```

- `type`：`feat`、`fix`、`refactor`、`test`、`docs`、`build`、`ci`、`chore`、`perf` 或 `revert`。
- `scope` 使用受影响模块，例如 `cli`、`recovery`、`updates`、`install`、`release`、`tests`、`docs` 或 `repo`；涉及整个仓库的初始化使用 `repo`。
- 标题使用简洁的英文祈使句，首行不超过 72 个字符，不以句号结尾。
- 每次提交表达一个明确目的，不混入无关修改。首次提交可以包含完整的项目初始化内容。
- 非简单修改在正文说明原因、主要行为和实际运行的验证命令。未执行或尚不可验证的检查必须如实说明。
- 不兼容变更使用 `type(scope)!:`，并在正文或页脚解释迁移方式；不要仅为适配标题而制造不兼容变更。

示例：

```text
chore(repo): initialize codexx project and contribution rules
fix(recovery): preserve the working thread during helper turns
feat(updates): detect credits before the scheduled quota reset
```

## 提交前检查

1. 检查 `git status` 和暂存差异，只提交本任务相关内容，不撤销或覆盖用户的其他修改。
2. 检查拟提交文件是否包含本机绝对路径、真实用户名、个人邮箱、令牌、密钥、会话日志、认证配置或其他用户信息。示例使用占位符，测试路径从测试目录或临时目录生成。
3. 确认虚拟环境、缓存、构建目录、打包产物及 `.codexx` 运行数据没有进入暂存区。不要把本地审计输出中的个人信息复制进提交说明。
4. 运行与改动相关的测试；项目初始化及跨模块行为变更运行 `python -m unittest discover -s tests -v`。平台未实际验证时不得声称验证通过。
5. 运行 `git diff --cached --check`，检查暂存区无空白错误，并核对最终提交内容与标题一致。
6. 使用现有 Git 身份提交；身份缺失时询问用户，不编造姓名或邮箱。提交后报告提交短哈希、标题和验证结果。

默认不推送远程、不创建发布、不改写已有提交历史；这些操作需要用户的相应指示。
