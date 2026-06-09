# Issue #0001: Bunkr parser 失效 + 失败被当作 SUCCEEDED 上传 115

**Status**: Open
**Discovered**: 2026-06-09 (e2e test on `feat/openclaw`)
**Severity**: High
**Owner**: TBD

## 摘要

跑端到端测试时（用测试 URL `https://bunkr.si/v/example-album-abc123`），任务**整条流水线都显示 `SUCCEEDED`**，但实际下载到的是 bunkr 404 错误页 HTML（57 字节），被原样上传到 115。

## 复现步骤

1. `docker compose up -d api core-worker`（用 `feat/openclaw` 分支的 `docker-compose.local.yml`）
2. `curl -X POST http://localhost:8000/v1/tasks/parse -H 'Content-Type: application/json' -d '{
     "url": "https://bunkr.si/v/example-album-abc123",
     "requester_id": "jason_e2e_test",
     "target": "RCLONE",
     "destination": "115:/media-shuttle-test"
   }'`
3. `curl http://localhost:8000/v1/tasks/<task_id>` → 看到 `status: SUCCEEDED`
4. 实际去 115 端看 → `115:/media-shuttle-test/bunkr.si/example-album-abc123` 是 57 字节的 HTML 错误页，**不是视频**

## 观察到的现象

```
[06:10:51] GET https://bunkr.si/v/example-album-abc123  →  301 Moved Permanently
[06:10:52] GET https://bunkr.si/f/example-album-abc123  →  404 Not Found
[06:10:52] download finished site=GENERIC local_path=.../tmp.part size_bytes=57
[06:11:06] upload finished location=rclone://115:/media-shuttle-test/bunkr.si/example-album-abc123
[06:11:06] task finalize succeeded
```

**关键错误链路**：
1. Bunkr parser 走正则匹配 `bunkr.si/v/<slug>` 这种**老格式**（现在的真实格式已经统一重定向到 `/f/<slug>` 或 `/a/<album_id>`）
2. URL 不被识别 → **fallback 到 GENERIC handler**（而不是 raise 错误）
3. GENERIC handler 拿到 `bunkr.si/v/...` 这个 301 重定向的 URL，把它当直链下载
4. 跟随重定向到 404 页 → 把 **404 HTML 当成 57 字节的"视频"** 存到本地
5. download 阶段**没有 content-type / content-length 校验**，没发现这是 HTML 错误页
6. upload 阶段把错误页当成功产物上传到 115
7. task 标 SUCCEEDED

## 根因（推测）

具体需要看代码确认：

1. `media-shuttle-core/core/providers/parsers_sites/bunkr.py` 的 URL 正则过时（猜测 `r"bunkr\.[a-z]+/v/([A-Za-z0-9_-]+)"` 这种）
2. `media-shuttle-core/core/providers/downloaders_sites/bunkr.py` 不存在或为空 → fallthrough 到 GENERIC
3. GENERIC downloader 没做 content-type 校验，没把 HTML 错误页识别为失败

## 修复方向（待实施）

- [ ] 抓取 bunkr 当前 mirror 的实际 HTML 结构（至少 3 个 mirror），更新 parser 正则
- [ ] 给 bunkr parser 加 "marker" 检查（解析 HTML 找 `<video>` 标签或 CDN URL）
- [ ] downloader 阶段加 content-type 校验：`text/html` 必须 fail 而不是 pass
- [ ] downloader 阶段加最小 size 校验（< 1KB 视为无效）
- [ ] 即使 parser 失败也应当 mark FAILED / RETRY，而不是 fallback 到 GENERIC

## 关联信息

- 搜到公开报告 https://github.com/mikf/gallery-dl/issues/6344 说 bunkr "every url returns error" —— 但当时是 2024 年，可能现在恢复了
- 测试中 115 cookie 仍然有效（`rclone lsd 115:/` 成功列目录）
- Docker image `media-shuttle-core:dev` 装的是 wiserain/rclone `v1.74.3-301`，115 backend 工作正常

## 污染清理

115 端有 1 个 57 字节污染文件 `115:/media-shuttle-test/bunkr.si/example-album-abc123`，删不掉 —— 115 backend 偶发列目录 hang / directory not found。后续可能需要手动清理或写 cleanup 脚本。
