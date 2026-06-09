# Issue #0001: Bunkr parser/downloader 失效 + 路径里 `&` 在 115 端被拒

**Status**: ✅ **RESOLVED** (parser 修了，path sanitize 修了)
**Discovered**: 2026-06-09
**Severity**: ~~High~~ → resolved
**Owner**: OpenClaw

## 摘要（修复版）

Bunkr 现在用 lv.js + dl.bunkr.cr 的 click handler 隐藏 cdn URL —— 真正的下载流：
1. `bunkr.cr/f/<slug>` 页面带 `data-file-id="<id>"`
2. 浏览器 POST `dl.bunkr.cr/api/_001_v2 {id}` → 拿 `mediafiles + path + original`
3. GET `glb-apisign.cdn.cr/sign?path=...` → 拿 `token + ex`
4. 拼成 signed CDN URL 才能 GET 到真文件

## 修复（已合入 `feat/openclaw`）

### `core/providers/parsers_sites/bunkr.py`

- 加 `_bunkr_origin_from_url()`, `_bunkr_file_id_from_html()`, `_bunkr_sign_via_api()` 三个 helper
- 改主路径 `_bunkr_resolve_single_file_download_url`：先抓 `data-file-id`，调 `/api/_001_v2` + `glb-apisign` 拿 signed URL
- 加 `_bunkr_file_name()`：抓 `var ogname = "..."` 拿真文件名
- 改 `_bunkr_folder_name()`：HTML entity decode (`&amp;` → `&`)，剥 `.mp4` 后缀当作 folder
- 改 `_parse_bunkr_single_page()`：folder = title 去扩展，file_name = ogname
- 删掉失效的 `/api/vs` XOR 解密分支（`/f/` 路径现在走 file_id → sign 流程）

### `core/providers/downloaders_sites/common.py`

- `http_download()` 加 content-type 校验：`text/html` 拒绝，size < 1024 拒绝
- 错误信息包含前 200 字节便于诊断

### `core/providers/uploaders_sites/common.py`

- `build_remote_name()` 改用 `_rclone_path_safe()`：替换 `&?#%+\[\]<>:\"\\|*` 等字符为 `_`
- 这避免了 115 / rclone 把 `&` 当 query separator 的坑

### `core/providers/uploaders_sites/rclone.py`

- 修 rclone 路径拼接 bug：原来 `f"{destination}:{remote_name}"` 在 destination 本身含 `:` 时会重复加冒号（变成 `115:/...:2026-06-09/...`）
- 加 `_split_remote_and_path()` 在第一个 `:` 切分

### `core.env`

- `MEDIA_SHUTTLE_USE_DATE_CATEGORY=1` 自动加 `<date>/<folder>/<file>` 路径

## 验证

**e2e #5 (task 48e837a3)** 全链路：

```
06:53:18  download finished  size_bytes=245813920 (234 MB - 真视频)
06:53:22  upload failed      rclone copyto ... 115:/media-shuttle-test/2026-06-09/How to Lick Pussy Right Let Lana _ Luna Teach You/...mp4
                              exit 1
```

**Download ✅ 真视频下来（234 MB 对得上页面 "234.43 MB"）**
**Upload ❌ rclone list 端点 405（不是 #0001 范围，是 #0002）**

## 后续

#0001 已 closed。下一步去修 #0002（115 backend list 405）。
