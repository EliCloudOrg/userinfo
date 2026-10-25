# EliCloud 用户资料服务（`userinfo`）

用户在桌面客户端上填写的**昵称、简介、头像、banner 头栏图**。

* 对外路径：`https://146.56.237.33/userinfo/*`（网关剥掉 `/userinfo`，容器内是 `/v1/...`）
* 容器 / 项目 / 镜像：`userinfo` / `userinfo` / `ghcr.io/elicloudorg/userinfo:prod`
* 文字资料：本地 SQLite（`/data/userinfo.db`）
* 图片：**只存 file 服务的文件元数据**；字节在 file 服务的私有 COS 桶里
* 部署手册：[README-deploy.md](README-deploy.md)

## 一、边界

| 数据 | 存在哪里 | 由谁管理 |
|---|---|---|
| 昵称、简介 | userinfo SQLite | userinfo |
| 头像、banner | file 服务（`file_id`） | file；userinfo 只保存 id/类型/大小/sha256 |
| 图片字节 | 私有 COS / file 本地后端 | file |

两条硬规则：

1. **图片一律 `public` + 永久**。上传时 userinfo 强制 `visibility=public`、`ttl_seconds=0`，
   并校验 file 返回 `permanent=true`；旧版 file 没有 `permanent` 字段时直接 502，不会降级。
2. **`public` 不是匿名**。读图仍要求有效 SSO access token + `files:read`；
   资料接口要求 `userinfo:read`，写入要求 `userinfo:write`。
   头像 / banner 的写接口额外要求 `files:write`。

## 二、接口

| 方法 | 路径（容器内） | scope | 说明 |
|---|---|---|---|
| GET | `/v1/profile` | `userinfo:read` | 自己的资料；没有记录时字段为 null |
| PUT | `/v1/profile` | `userinfo:write` | 全量更新昵称 + 简介 |
| GET | `/v1/profile/avatar` | `userinfo:read` + `files:read` | 自己的头像；支持 `If-None-Match` |
| PUT | `/v1/profile/avatar` | `userinfo:write` + `files:write` | multipart `file`，替换头像 |
| DELETE | `/v1/profile/avatar` | `userinfo:write` + `files:write` | 删除头像，幂等 |
| GET | `/v1/profile/banner` | `userinfo:read` + `files:read` | 自己的头图 |
| PUT | `/v1/profile/banner` | `userinfo:write` + `files:write` | multipart `file`，替换头图 |
| DELETE | `/v1/profile/banner` | `userinfo:write` + `files:write` | 删除头图，幂等 |
| GET | `/v1/profiles/{sub}` | `userinfo:read` | 其他已登录用户的公开资料 |
| GET | `/v1/profiles/{sub}/avatar` | `userinfo:read` + `files:read` | 他人的 public 头像 |
| GET | `/v1/profiles/{sub}/banner` | `userinfo:read` + `files:read` | 他人的 public 头图 |
| GET | `/v1/me` | `userinfo:read` | 当前令牌身份自检 |
| GET | `/healthz` | 无 | DB 必检；file 不可达只标记 degraded |

### 请求 / 响应

```http
PUT /userinfo/v1/profile
Authorization: Bearer <access token>
Content-Type: application/json

{"nickname": "秋风", "bio": "你好，这里是我的简介。"}
```

```json
{
  "user_id": "user_0001",
  "nickname": "秋风",
  "bio": "你好，这里是我的简介。",
  "avatar": {
    "content_type": "image/png",
    "size_bytes": 45678,
    "sha256": "…",
    "updated_at": "2026-10-25T05:00:00Z"
  },
  "banner": null,
  "created_at": "2026-10-25T05:00:00Z",
  "updated_at": "2026-10-25T05:00:00Z"
}
```

图片上传是 `multipart/form-data`，字段名固定 `file`：

```bash
curl -X PUT https://146.56.237.33/userinfo/v1/profile/avatar \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@avatar.png;type=image/png"
```

### 错误码

| 状态 | error | 场景 |
|---|---|---|
| 400 | `invalid_request` | 请求体/表单不合法 |
| 400 | `invalid_nickname` | 昵称空、过长、含控制字符/换行 |
| 400 | `invalid_bio` | 简介过长、含控制字符 |
| 400 | `unsupported_image_type` | 非 PNG/JPEG/WebP，或声明类型与内容不一致 |
| 400 | `invalid_image` | 空文件、文件头不合法 |
| 404 | `profile_not_found` | 按 sub 查不到资料 |
| 404 | `image_not_found` | 没设置该图片，或 file 对象已丢失 |
| 409 | `image_quota_exceeded` | file 配额满 |
| 413 | `image_too_large` | 超过头像 5 MiB / 头图 10 MiB |
| 429 | `rate_limited` | 写/上传限流 |
| 502 | `file_service_error` | file 非预期错误 / 未按 public+permanent 保存 |
| 503 | `file_service_unavailable` | file 网络/超时/503 |
| 503 | `database_unavailable` | SQLite 不可用 |

## 三、数据

```text
profiles        user_id(PK) / nickname / bio / created_at / updated_at
profile_images  (user_id, kind) PK / file_id / content_type / size_bytes / sha256 / updated_at
                kind ∈ {avatar, banner}；FK profiles.user_id ON DELETE CASCADE
audit_logs      profile_update / image_update / image_delete / deny
```

* 以 SSO `sub` 作为唯一用户键；不保存 SSO 用户名/邮箱。
* 审计只记录长度、文件 id、大小、类型，不记录正文或令牌。
* 图片写入顺序：file 先成功 → DB 提交；DB 失败时删除刚上传的 file 对象，避免孤儿。
* 新图生效后旧图删除失败只记日志，不影响新图；旧对象最多占 file 配额。

## 四、配置

见 [`.env.example`](.env.example)。生产值放 `/srv/userinfo/app.env`（600，`deploy:deploy`），
不进仓库、不进 Actions。

## 五、本地开发

单独跑：

```powershell
copy .env.example .env.local
# 把 FILE_SERVICE_BASE_URL 改成可达的 file 服务
docker compose up -d --build
curl http://127.0.0.1:8003/healthz
```

本地构建默认用清华 PyPI 源（compose 的 `PIP_INDEX_URL`），不需要能直连官方 PyPI。

完整联调（SSO + email + file + userinfo + 网关）：

```powershell
docker compose -f ..\EliCloudDesktop\local-dev\docker-compose.yml up -d --build
```

然后用桌面客户端的 `Debug-Local` 构建登录。注意本地 `file` 服务使用 `STORAGE_BACKEND=local`，
不需要 COS 凭据。

## 六、测试

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

测试使用真实 RS256 验签路径（假 JWKS HTTP 服务）+ 内存版 file 服务，不访问真实 SSO/COS。
