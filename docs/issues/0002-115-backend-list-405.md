# Issue #0002: 115 backend list 端点返回 405 Not Allowed，rclone 写操作依赖 list 才能完成

**Status**: Open / **Blocker** (无法验证，无法上传)
**Discovered**: 2026-06-09
**Severity**: **High** — 整个 RCLONE 上传通道因此不可用
**Owner**: TBD

## 摘要

`rclone lsd/lf/cat/copyto 115:/` 全部失败，list 端点返回 `Status 405 "405 Not Allowed"`。rclone 的 `copyto` 在写文件前需要先 list 父目录来区分 overwrite/create —— 因此 **rclone 写操作也连带挂掉**（"Failed to create file system for destination"）。

## 时间线

- **2026-06-09 ~04:11 UTC**：rclone 115 backend list/copy 正常（tmp-core-worker-1 探针测试通过）
- **2026-06-09 ~04:12 UTC**：tmp 测试已 down
- **2026-06-09 ~06:25 UTC**：core-worker 跑 e2e 时 **upload 报 "Transferred 100%"** （log 看着像成功），但其实可能写到了 rclone cache 或 write-only path，并未真正落到 115 主存储
- **2026-06-09 ~06:29 UTC**：手工 `rclone copy 115:/` —— **list 失败**，"Failed to create file system" —— 自此写操作也都失败
- **2026-06-09 ~06:50 UTC**：rclone 路径里的 `&` 被替换成 `_` 后，命令形式正确了（`115:/.../Lana _ Luna...`），但 rclone 仍 405 失败

## 真正根因（升级）

之前以为是"cookie 部分失效"，但**稳定 405** 表明更可能的是 **115 web 端 API 限流/下线了 list endpoint**，影响所有调用此 endpoint 的操作。

**rclone 115 backend 写入流程**：
1. 调 list endpoint 拿目标父目录信息
2. 调 upload endpoint PUT 文件
3. （可选）调 list endpoint 验证写入

**第 1 步就 405，rclone 直接放弃整个操作** —— 这是为什么 upload 看似成功实则不成功。

## 跟 #0001 修复的关系

修了 #0001（bunkr parser 拿 cdn URL）后，**e2e 全链路 100% 跑通**（下载 234MB 真视频用了 8.7s），但 rclone 上传 115 这最后一步被 #0002 拦截：

```
22:42.695 | upload failed ... reason=Command 'rclone copyto ... 115:/media-shuttle-test/2026-06-09/...mp4' returned non-zero exit status 1.
```

## 验证失败的副产物

- 115 上**有一个 e2e 早期任务残留的污染文件**（`How to Lick Pussy Right Let Lana & Luna Teach You.mp4/...`，但名字被 rclone 截断）
- 用 rclone 删不掉（list 405 没法 `delete`）
- **必须 115 web 端手动清理**，或者用 115 web API（绕过 rclone）

## 修复方向

### 短期（让当前 e2e 跑通）

- **更新 rclone 115 cookie**：用新 cookie 重生 rclone.conf（**最可能解决问题**）
- 升级 rclone 到 1.74.3 之后的 wiserain release（如果有）
- 临时换用 `rclone move` 或 `rclone sync` 看是不是 list 调用方式不同

### 中期

- 替换 115 后端为更稳的实现（alist/panindex 等），不走 rclone
- 给上传加 idempotency + 重试（部分 list 失败应该 fallback 到 PUT-only）

### 长期

- 监控 115 backend 健康（list 成功率 < 95% 时告警）
- 加 e2e 验证（下载后回读 115 端 hash）—— 但 405 时这条 verification 自身也跑不了

## 现状

core-worker 仍在 retry task `48e837a3-d47c-4f1e-abd7-a306ebaa46d3`（attempt 1/2），**每个 attempt 下载 234MB 一次**（retry 会重做 parse → download）。如果 #0002 不修，这个 retry 永远拿不到真成功，最终 FAILED。

## 建议

最实用路径：**先**确认 115 cookie 是否过期（web 端 115 登录看下），**再**做一次 cookie refresh 看 405 是否消失。如果 cookie 是新的但还是 405，说明 115 web 端 list API 限流/下线，需要等或换后端。
