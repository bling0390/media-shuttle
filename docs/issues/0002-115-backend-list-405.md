# Issue #0002: 115 backend list 端点返回 405 Not Allowed

**Status**: ✅ **RESOLVED**
**Discovered**: 2026-06-09
**Severity**: ~~High~~ → resolved
**Owner**: OpenClaw

## 摘要

`rclone lsd/lf/cat/copyto 115:/...` 全部 405 Not Allowed，rclone 写操作依赖 list 端点所以上传挂掉。

## 根本原因

**不是 115 服务端限流，也不是 rclone 后端 bug** —— **115 cookie 部分失效**。同一份 rclone 后端二进制（wiserain/rclone v1.74.3-301），同一台机器：

- 2026-06-09 ~04:11 UTC 用 cookie A（UID=...1780975395）能 list 200+ 目录
- 2026-06-09 ~06:25 UTC 同一份 cookie A 在 rclone copyto 时 405
- 2026-06-09 ~07:35 UTC 用新 cookie B（UID=...1780989744）**立刻恢复正常**

**结论**：cookie A 里的某段 token 在 2 小时内失效了，导致 list 端点（115 web 端的目录查询 API）拒绝服务。**但 cookie 里的 upload 端点 token 仍然有效** —— 这就是为什么 rclone 早期在 405 之前还报"Transferred 100%"误导了。

## 修复

替换 rclone.conf 里的 cookie 段：
- UID/CID/SEID 都换（KID 设备 ID 不变）
- UID 时间戳从 `1780975395` → `1780989744`（约 40 分钟前导出）

不需要改任何代码。

## 验证

替换后立刻 list 通：
```
2024-08-02/
2024-08-03/
...
```

跑通了一次端到端（task 9ed9c208-7e9c-4e36-89f2-30edca05709a）：
- 解析 → cdn URL 拿对 ✅
- 下载 234 MB 真视频（8.5s）✅
- 上传 115，路径 `115:/media-shuttle-test/2026-06-09/How to Lick Pussy Right Let Lana _ Luna Teach You/How to Lick Pussy Right Let Lana _ Luna Teach You.mp4`（138s）✅
- 115 端确认：1 个对象，234.426 MiB ✅
- task 状态：SUCCEEDED ✅

## 副作用（清理）

之前 cookie A 失效时跑了 e2e #1 留下两个污染目录：
- `115:/media-shuttle-test/How to Lick Pussy Right Let Lana &amp; Luna Teach You.mp4/` （e2e #1 残留）
- `115:/media-shuttle-test/bunkr.si/` （e2e #0 残留）

新 cookie 下 rclone purge / rmdir 都报 "directory not found" 但 lsf 看到还在 —— **rclone 115 backend 的 purge/rmdir 走的又是另一段 API**，那段对当前 cookie 也不通。需要走 115 web 端或直接调 115 web API 清理。

## 建议长期改进

- **cookie 健康监控**：定期跑 `rclone lsf 115:/`（如 5 分钟一次）作为探针，405 时立即告警
- **cookie TTL 监控**：记录每次成功 cookie 的时间戳，提醒用户每 N 小时 refresh 一次
- **#0001 修复的 content-type/size 校验**继续保持（之前 cookie A 失效期间 405 容易掩盖 content-type 错乱）
- 升级 rclone 也没必要，wiserain 1.74.3-301 是稳的
