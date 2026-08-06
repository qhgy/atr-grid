# 经验教训：GitHub Actions 与 Dev Pages 恢复

日期：2026-06-03  
仓库：qhgy/atr-grid  
范围：`dev` 分支、GitHub Actions、GitHub Pages `/dev/` 与 `/dev/515880/`

## 结论

这次停更不是单一问题，而是两个问题叠加：

1. `dev` 分支里的 workflow 不会自动按计划运行，因为 GitHub Actions 的 `schedule` 事件只会在默认分支上的 workflow 生效。
2. GitHub Secret `XUEQIUTOKEN` 虽然存在，但内容不是本机验证过的新雪球 cookie，导致 runner 取不到雪球日线数据。

修复后：

- `main` 增加了 `Update Dev Pages` workflow，负责从 `dev` checkout 源码并发布到 `main/docs/dev/`。
- GitHub Secret `XUEQIUTOKEN` 已用本机可用的 `xq_token.txt` 重新写入。
- `dev` 推送了 cookie 格式兼容修复，支持 Cookie Editor JSON 导出。
- 手动触发的 Actions run 成功，并在 `main` 生成提交 `b32759f chore: update dev pages 2026-06-03 17:17`。

## 时间线

1. 发现线上页面停更：
   - `/dev/` 停在 2026-04-27 左右。
   - `/dev/515880/` 停在 2026-04-27 10:37。

2. 初步判断：
   - 以为主因可能是雪球 cookie 过期。
   - 后续确认：cookie 过期是阻塞之一，但 workflow 调度位置也有问题。

3. 梳理 GitHub Pages 来源：
   - 线上页面不是直接读取 `dev` 分支。
   - `/dev/` 和 `/dev/515880/` 实际是 `main/docs/dev/` 下的静态文件路径。

4. 修复发布链路：
   - 在 `main` 上新增 `.github/workflows/update-dev-pages.yml`。
   - workflow 在默认分支运行，但显式 checkout `dev` 分支源码。
   - 生成 `/dev/` 多标的页和 `/dev/515880/` 专页，再提交回 `main/docs/dev/`。

5. 第一次手动触发失败：
   - run `26875225762` 失败。
   - 日志显示三只 ETF 均无法获取日线数据：
     - `SH515880`
     - `SH513500`
     - `SZ159915`
   - 因为没有生成 `output/atr_grid.html`，后续 `cp` 报错。

6. 推送 dev 修复：
   - 提交 `93f673e fix: support xueqiu cookie json exports`。
   - 修复内容包括：
     - 支持 Cookie Editor JSON 格式导出的雪球 cookie。
     - Windows 终端输出编码兼容。
     - 增加 cookie 解析测试。

7. 再次触发仍失败：
   - run `26875352292` 失败。
   - Secret 名存在，日志里显示 `XUEQIUTOKEN: ***`，但这只能说明 Secret 被传入，不能说明内容正确或有效。

8. 重新写入 Secret 后成功：
   - 使用本机已验证可用的 `D:\000\atr\atr-grid\xq_token.txt` 写入 GitHub Secret。
   - 再次触发 run `26875429570`。
   - 结果：success。
   - 生成并推送 `main` 提交 `b32759f chore: update dev pages 2026-06-03 17:17`。

## 根因

### 1. 误以为 dev 分支 workflow 会自动调度

GitHub Actions 的 `schedule` 事件只在默认分支上的 workflow 文件生效。  
如果 workflow 只存在于 `dev` 分支，即使写了 cron，也不会按计划运行。

这解释了为什么页面长期停在 4 月 27 日。

### 2. 误把 Secret 存在等同于 Secret 可用

Actions 日志里出现：

```text
XUEQIUTOKEN: ***
```

只能说明：

- Secret 名称存在。
- workflow 能读取这个 Secret。

不能说明：

- cookie 是新的。
- cookie 格式能被程序解析。
- cookie 对雪球 API 仍有效。
- cookie 里的关键字段未过期。

这次真正跑通，是因为重新用本机验证过的 `xq_token.txt` 写入了 Secret。

### 3. 远端 dev 代码与本机修复存在时间差

本机已经能解析 Cookie Editor JSON，但 workflow checkout 的是远端 `dev`。  
在修复提交推送前，远端 runner 仍使用旧代码。

因此以后排障 CI 时，要确认：

- 本机修复是否已 commit。
- 修复是否已 push 到 workflow checkout 的分支。
- workflow checkout 的 ref 是否符合预期。

## 已完成修复

### main 分支

新增 workflow：

```text
.github/workflows/update-dev-pages.yml
```

作用：

- 在默认分支 `main` 上接收 schedule 和 workflow_dispatch。
- checkout `dev` 分支源码。
- 生成 `/dev/` 多标的 dashboard。
- 生成 `/dev/515880/` 单标的 trend_hybrid dashboard。
- 将结果复制到 `main/docs/dev/`。
- 如有变更，提交并 push 回 `main`。

相关提交：

```text
9cd69b9 ci: add scheduled dev pages publisher
b32759f chore: update dev pages 2026-06-03 17:17
```

### dev 分支

修复 cookie 解析与 Windows 输出：

```text
93f673e fix: support xueqiu cookie json exports
```

本地验证：

```text
uv run pytest -q
70 passed
```

## 验证方法

### 1. 检查 Actions

```powershell
cd D:\000\atr\atr-grid-pages-main
gh run view 26875429570 --json status,conclusion,url
```

期望：

```text
status: completed
conclusion: success
```

### 2. 检查 main 源文件

```powershell
cd D:\000\atr\atr-grid-pages-main
git fetch origin main
git show origin/main:docs/dev/515880/index.html
```

确认包含：

```text
更新于 2026-06-03 17:17
SH515880
¥1.716
```

### 3. 检查线上页面

页面：

- https://qhgy.github.io/atr-grid/dev/
- https://qhgy.github.io/atr-grid/dev/515880/

如果源文件已经更新但页面仍显示旧日期，大概率是 GitHub Pages/CDN 缓存延迟。

## 下次排障清单

遇到 Pages 停更时，按这个顺序查：

1. 看 GitHub Actions 最近一次 run 是否存在。
2. 如果没有 run，优先检查 workflow 是否在默认分支。
3. 如果 run 失败，先看失败步骤，不要直接假设是 cookie。
4. 如果日志显示 `XUEQIUTOKEN: ***`，只说明 Secret 存在，不代表内容有效。
5. 在本机用同一份 cookie 跑：

```powershell
cd D:\000\atr\atr-grid
uv run python -m atr_grid multi SH515880 SH513500 SZ159915
uv run python -m atr_grid multi SH515880 --profile trend_hybrid
```

6. 本机可用但 CI 不可用时，检查：
   - Secret 是否刚刚更新。
   - Secret 写入方式是否保留了完整 cookie。
   - CI checkout 的分支是否包含本机修复。
   - workflow 是否使用了正确的 Secret 名称。

7. 重新写入 Secret 时，优先用 stdin，避免 cookie 出现在命令历史或日志里：

```powershell
Get-Content -Raw D:\000\atr\atr-grid\xq_token.txt | gh secret set XUEQIUTOKEN
```

8. 手动触发：

```powershell
cd D:\000\atr\atr-grid-pages-main
gh workflow run update-dev-pages.yml --ref main
```

9. 查看失败日志：

```powershell
gh run view <run_id> --log-failed
```

## 工程经验

### 1. CI 的信息要能证明问题，而不是只证明表象

这次最有价值的日志不是 `cp` 失败，而是前面的三行：

```text
[SH515880] 生成失败: 无法获取 SH515880 的日线数据
[SH513500] 生成失败: 无法获取 SH513500 的日线数据
[SZ159915] 生成失败: 无法获取 SZ159915 的日线数据
```

`cp output/atr_grid.html` 失败只是结果，不是根因。  
真正要追的是为什么没有生成 HTML。

### 2. Secret 要区分“存在”和“有效”

GitHub 会把 Secret 隐藏成 `***`。  
这会让人误以为 Secret 正常，但它只证明读取到了某个值。

以后对依赖外部登录态的 workflow，要建立一个更明确的健康检查：

- 不输出 cookie 值。
- 输出 cookie 是否为空。
- 输出解析出的 cookie 名称列表。
- 输出 HTTP 状态码。
- 输出 API 返回错误码或错误摘要。

### 3. dev 页面应该由 main 调度、dev 产出

合理结构是：

```text
main: 负责 GitHub Pages 静态发布与 Actions 调度
dev : 负责正在开发的业务代码
```

workflow 放在 `main`，但 checkout `dev`，这样既满足 GitHub Actions schedule 规则，又能让线上 `/dev/` 真正反映开发分支内容。

### 4. 本机验证成功之后，要确认同一份状态已推到远端

本机成功不等于 CI 成功。  
CI 只认识远端 commit、远端 Secret、远端 workflow。

下次本机修复后，最少确认：

```powershell
git status --short --branch
git log --oneline -3
git push origin dev
```

## 后续建议

1. 给 workflow 增加无敏诊断步骤，用于定位雪球 API 失败原因。
2. 把 `gh.exe` 的安装路径加入 Codex/系统 PATH，避免以后必须写完整路径。
3. 增加一个单独的 smoke test workflow，只检查雪球 cookie 是否能拿到 `SH515880` 的 kline，不发布页面。
4. 对 Pages 更新增加最后校验：检查生成 HTML 是否包含当天日期和目标 symbol。
5. 记录 cookie 过期时间，如果 cookie 文件格式包含 expiry，就在 CI 或本机运行时提前告警。
