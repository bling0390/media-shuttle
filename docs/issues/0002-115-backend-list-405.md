# Issue #0002: 115 backend list 端点返回 405 Not Allowed

**Status**: Open / Confirmed
**Discovered**: 2026-06-09
**Severity**: **High** (写操作能成功但 list 端点 405，无法验证，且 4 小时后 list 失败说明 cookie 鉴权部分已失效)
**Owner**: TBD

## 摘要

用 `rclone ls/lf/lsd/cat/size 115:/...` 验证 115 端上传结果时，**所有 list 类型的操作都抛 405 Not Allowed**：

```
ERROR : error listing: couldn't get files: Status 405 "405 Not Allowed"
NOTICE: Failed to lsf with 2 errors: last error was: error in ListJSON: couldn't get files: Status 405 "405 Not Allowed"
```

但 **upload 阶段本身能成功**（rclone 报 "Transferred: ..." 200 OK）。

## 时间线（关键观察）

- **2026-06-09 ~04:11 UTC**（第一次 tmp 容器 e2e 探针）：`rclone lsd 115:/` **成功**，列出了 200+ 目录
- **2026-06-09 ~04:11 UTC**：`rclone copy` + `rclone ls 115:/e2e-probe/` 验证上传 200 OK
- **2026-06-09 ~06:25 UTC**（e2e 真实 bunkr 任务）：core 上传报 "Transferred 100% 12.5s"
- **2026-06-09 ~06:25 UTC**（同一个 task 结束后立刻）：`rclone ls 115:/media-shuttle-test/` **开始 405**
- **2026-06-09 ~06:29 UTC**：`rclone copy 115:/round-trip-test.txt` 上传**本身也失败** —— "Failed to create file system ... 405"

**意味着 list 失败从大约 04:12 → 06:25 之间某时点开始出现**，且后续连 write 端点也报 405。**最可能的解释**：

- cookie 中的 **list 权限/会话鉴权**用了另一组 token（更短 TTL）
- 12 小时或更短时间内部分 cookie 已失效
- **上传 rclone 看到的"成功"** 在某些情况下是**乐观写**（提交但未确认到主存储）

## 复现

```bash
docker exec media-shuttle-core-worker-1 rclone lsf 115:/ 2>&1
# → Status 405 "405 Not Allowed"
docker exec media-shuttle-core-worker-1 rclone lsd 115:/ 2>&1
# → Status 405
docker exec media-shuttle-core-worker-1 rclone size 115:/media-shuttle-test 2>&1
# → couldn't get files: Status 405
docker exec media-shuttle-core-worker-1 rclone cat "115:/path/to/file" 2>&1
# → Failed to cat ... Status 405
```

## 影响

- 没法在本地用 rclone 验证上传结果（必须去 115 web 端看）
- 没法用 `rclone cleanup` 删污染文件
- 不影响 core 业务逻辑（rclone 上传走的是不同 API endpoint，看起来是 OK 的）

## 可能原因

- 115 web API 改版了，wiserain/rclone `v1.74.3-301` 的 list 实现没跟上
- 115 端 list 频率/路径触发反爬（虽然单次请求）
- 鉴权 cookie 部分失效（upload 用一段 API，list 用另一段）

## 验证 115 上传是否真成功的替代方案

1. **手动 115 web** 去看 `media-shuttle-test/` 目录
2. rclone download 试拉一个具体已知路径的 file（如果路径猜得对）
3. 写一个简单的 Python 脚本，直接调 115 web API（用同一个 cookie）列目录
4. 升级到更新的 wiserain/rclone release（v1.74.3-301 之后看看）

## 临时 workaround

不验证 115 端内容，**信任 core 的 upload 阶段日志**（rclone Transferred: 信息）。但这违反了 "validate that critical side effects actually happened" 的原则。

## 后续

- 跟 #0001 修完 bunkr parser 之后，重新跑一次 e2e，然后再看 115 list 是否恢复
- 如果 405 一直存在，可能要把 115 验证改成异步 webhook / 定期 webhook 回调
