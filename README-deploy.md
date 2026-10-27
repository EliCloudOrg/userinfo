# userinfo 部署说明

本仓库使用「GitHub Actions 触发 + 服务器自管」的自托管部署框架。

> **当前状态（2026-10-27 已上线）**：`deploy/deploy.sh` 已补齐真实部署逻辑
> （拉镜像 → 生成 compose → 等 healthy → 回环自检），`prod` 已推送并完成首次部署，
> 容器 `userinfo`（healthy）与 Caddy 的 `/userinfo/*` 路由均已在线上。
>
> **镜像源（本机实测差异）**：Actions 仍推 `ghcr.io/elicloudorg/userinfo`；但服务器直连 ghcr.io
> 拉大层会停在 `Pulling fs layer` 不动，因此 `/srv/userinfo/app.env` 里设了
> `USERINFO_IMAGE_REPO=ghcr.nju.edu.cn/elicloudorg/userinfo`（公共 ghcr 加速源，包必须保持 public）。
> 想改回直连只改这一行并重新部署即可。

## 部署架构

```text
      feature/* ──PR──▶ main ──PR──▶ prod
                         │            │
                    push │            │ push / workflow_dispatch(ref)
                         ▼            ▼
              ┌──────────────────┐  ┌───────────────────────────────────────┐
              │ CI  ci.yml       │  │ Deploy  deploy-prod.yml               │
              │  安装 Python 依赖│  │  ① 构建镜像 → ghcr.io                 │
              │  编译 / 单测     │  │     仅当 vars.DEPLOY_MODE == image    │
              │  导入检查        │  │  ② scp deploy/deploy.sh → /srv/userinfo│
              └──────────────────┘  │  ③ ssh 执行 deploy.sh REF              │
                                    │  environment: production（人工审批）  │
                                    └──────────────────┬────────────────────┘
                                                       │ SSH（deploy 用户 + 私钥）
                                                       ▼
                                    ┌───────────────────────────────────────┐
                                    │ 服务器  /srv/userinfo/                 │
                                    │   deploy.sh   ← 骨架（待补项目逻辑）   │
                                    │   app.env     ← 运行时环境变量（600）  │
                                    │   容器 / 数据目录（未来由 deploy 逻辑）│
                                    └───────────────────────────────────────┘
```

运行时拓扑（**需先部署 file**）：

```text
Caddy  /userinfo/*  →  userinfo:8000  →  file:8000  →  私有 COS
```

## 分支模型

| 分支 | 用途 | 合并规则 |
|---|---|---|
| `main` | 集成分支，日常 PR 合入，跑 CI | 需要 PR + CI 通过 |
| `prod` | 发布分支，受保护 | 只允许从 `main` 发 PR 合并；合并即触发部署 |
| `feature/*` | 功能分支 | 从 `main` 拉出，PR 回 `main` |

> `userinfo/` 目前**没有 `.git` 目录，也没有 GitHub 远端**。本仓库内文件已生成，
> 但 GitHub 侧的仓库创建、分支推送、Secrets/Variables/Environment/分支保护需要人工完成。

## 首次部署

1. **确认前置依赖：先部署 file 服务**
   当前服务器上 `/srv/file` 不存在、也没有 `file` 容器；userinfo 的头像/头图上传依赖 file。
   在 file 部署并验证 `/file/healthz` 之前，userinfo 只能提供文字资料。

2. **服务器初始化（root）**

   ```bash
   scp scripts/bootstrap-server.sh deploy@146.56.237.33:/tmp/
   ssh deploy@146.56.237.33 'sudo bash /tmp/bootstrap-server.sh userinfo'
   ```

   脚本会创建 `deploy` 用户（若不存在）、`/srv/userinfo`（deploy:deploy 750）、
   空的 `/srv/userinfo/app.env`，并把 deploy 加入 docker 组（若存在）。
   **脚本不会安装 Docker、不会拉代码、不会启动服务。**

3. **配置 SSH 免密**
   在本机生成部署专用密钥对，把公钥写入 `/home/deploy/.ssh/authorized_keys`，
   私钥全文写入 GitHub Secrets `SSH_KEY`。不要复用个人密钥。

4. **填写 GitHub 配置**：见下面的「GitHub 配置清单」。

5. **填充运行时环境变量**：按 [`.env.example`](.env.example) 写 `/srv/userinfo/app.env`
   （600，`deploy:deploy`）。不要把它提交到仓库。

6. **补齐部署逻辑**：在 `deploy/deploy.sh` 的「项目自定义部署逻辑」段实现拉镜像、
   写 compose、启动/健康检查。**本次没有补**，见 §8。

7. **首次发布**：GitHub → Actions → **Deploy to production** → *Run workflow*，
   `ref` 填 `prod`。人工审批通过后，Actions 会先构建并推送镜像，再把
   `deploy.sh` 上传到 `/srv/userinfo/deploy.sh` 并执行。

## GitHub 配置清单

> 仓库创建后，按下表填真实值；不要留示例值。

### Secrets（Settings → Secrets and variables → Actions → Secrets）

| Secret | 值 | 说明 |
|---|---|---|
| `SSH_HOST` | `146.56.237.33` | 目标服务器 |
| `SSH_USER` | `deploy` | `bootstrap-server.sh` 创建/使用的系统用户 |
| `SSH_PORT` | `22` | SSH 端口 |
| `SSH_KEY` | 部署私钥**全文** | 单独生成，不复用个人密钥；公钥放 `/home/deploy/.ssh/authorized_keys` |
| `GHCR_TOKEN` | 不需要 | 同仓库推 `ghcr.io` 用内置 `GITHUB_TOKEN` |

```bash
gh secret set SSH_HOST --body '146.56.237.33'
gh secret set SSH_USER --body 'deploy'
gh secret set SSH_PORT --body '22'
gh secret set SSH_KEY < ./deploy_key
```

### Variables（同一页面的 Variables 标签）

| Variable | 值 | 说明 |
|---|---|---|
| `DEPLOY_MODE` | `image` | 阶段一构建并推镜像；其它值会跳过阶段一，只执行 `deploy.sh` |

```bash
gh variable set DEPLOY_MODE --body 'image'
```

> ⚠️ 必须配成**仓库级** Variable：`image` job 没有 `environment:`，读不到 Environment 级变量。

### Environment：`production`

- Required reviewers：部署前必须有人 Approve。
- Deployment branches：限制为 `prod`。
- ⚠️ 限制部署分支后，用 `workflow_dispatch` 回滚时**必须在 UI 里把分支选成 `prod`**，
  否则 job 会被策略直接拒绝（2 秒内失败、无步骤日志）。
- ⚠️ GitHub 免费版账号的 **private** 仓库不支持分支保护与 Required reviewers
  （API 403/422），需要 public 仓库或升级 GitHub Pro。
- ⚠️ 单账号无法给自己的 PR 审批，`required_approving_review_count` 只能先设 0
  （仍然必须走 PR）；加协作者后再调到 1（main）/ 2（prod）。

### 分支保护

| 分支 | 规则 |
|---|---|
| `main` | 需要 PR；必过检查 `Test and build`；勾选 Require branches to be up to date；禁 force push / 禁删除 |
| `prod` | 需要 PR；必过检查 `Test and build` + `Guard prod source`；禁 force push / 禁删除 |

> `Guard prod source` 是「prod 只允许从 main 合并」的唯一强制手段，必须加进
> `prod` 的 Required status checks。

## 配置与密钥放置表

| 变量 / 配置 | 位置 | 用途 |
|---|---|---|
| `SSH_HOST` / `SSH_USER` / `SSH_PORT` | GitHub Secrets | Actions 连哪台机器、以谁登录 |
| `SSH_KEY` | GitHub Secrets | 部署私钥（Actions 侧唯一凭据） |
| 对应公钥 | 服务器 `/home/deploy/.ssh/authorized_keys`（600） | 校验上面的私钥 |
| `GITHUB_TOKEN` | Actions 内置 | 同仓库推 `ghcr.io` 镜像（`packages: write`） |
| `DEPLOY_MODE` | GitHub Variables（**仓库级**） | `image` → 阶段一构建推镜像 |
| `SSO_ISSUER` / `SSO_JWKS_URL` / `JWT_AUDIENCE` | 服务器 `/srv/userinfo/app.env` | 令牌验签 |
| `REQUIRED_READ_SCOPE` / `REQUIRED_WRITE_SCOPE` | 同上 | `userinfo:read` / `userinfo:write` |
| `REQUIRED_FILE_READ_SCOPE` / `REQUIRED_FILE_WRITE_SCOPE` | 同上 | `files:read` / `files:write` |
| `DATABASE_URL` | 同上 | SQLite 路径（容器内 `/data/userinfo.db`） |
| `FILE_SERVICE_BASE_URL` | 同上 | 生产为 `http://file:8000` |
| `MAX_AVATAR_BYTES` / `MAX_BANNER_BYTES` | 同上 | 5 MiB / 10 MiB |
| `MAX_NICKNAME_CHARS` / `MAX_BIO_CHARS` | 同上 | 32 / 500 |
| `WRITE_ATTEMPTS_PER_WINDOW` / `IMAGE_ATTEMPTS_PER_WINDOW` | 同上 | 写/上传限流 |
| `CORS_ORIGINS` / `LOG_LEVEL` | 同上 | 通用 |
| 部署 ref（`prod` / tag / commit SHA） | Actions 参数 | 决定这次部署哪个版本 |

规则：**部署凭据只走 GitHub Secrets；应用运行时变量只在服务器 `app.env`。**
两边都不进仓库，也不在 workflow 里 `echo`。

## 服务器侧前置检查（2026-10-25 实测）

| 检查 | 命令 | 实际结果 |
|---|---|---|
| root / sudo 可用 | `sudo -n true` | ✅ 可用 |
| **Docker 是否 snap** | `readlink -f "$(command -v docker)"` | ❌ **snap 安装**（`/snap/bin/docker`）：docker 看不到 `/srv`；`deploy.sh` 不能把 `/srv` 路径交给 docker，必须先用 shell 读 `app.env`，再以 `-e` 传入，项目目录放 `/home` |
| docker 组 | `getent group docker` | ✅ `deploy` 已在 docker 组（重新登录后生效） |
| `ghcr.io` 可达性 | `curl -s -o /dev/null -w '%{http_code}' https://ghcr.io/v2/` | ✅ `401`（匿名探测的正常响应，不代表不可达） |
| `/srv` | `ls -ld /srv` | ✅ 存在 |
| `/srv/userinfo` | `ls -ld /srv/userinfo` | ❌ 不存在；需 `bootstrap-server.sh userinfo` |
| `/srv/file`、file 容器 | `ls -ld /srv/file` / `docker ps` | ❌ 不存在 / 未运行；**生产前置依赖** |

> 服务器上 Docker 是 snap 版，这是本项目部署逻辑最大的环境约束。不要照搬
> `docker build /srv/...`、`docker run -v /srv/...` 或 `--env-file /srv/...`。

## 日常发布流程

```bash
git switch main && git pull
git switch -c feature/profile-editor
# ... 开发 ...
git push -u origin feature/profile-editor   # 开 PR → main，等 CI 通过并合并
```

然后发 PR：`main` → `prod`。合并到 `prod` 即触发 `deploy-prod.yml`：
先（可选）推镜像，再上传并执行 `/srv/userinfo/deploy.sh prod`。

## 回滚流程

两种方式，都只是把同一个 `deploy.sh` 用另一个 ref 再跑一次：

1. **GitHub Actions 回滚（推荐）**
   Actions → *Deploy to production* → *Run workflow* →
   `ref` 填上一个可用版本（tag / commit SHA / 分支）。
   - 若 Environment 限制了 Deployment branches，UI 里的**分支必须选 `prod`**。
   - 阶段一会按 `ref` 输入检出并构建**那个版本**（`ref: ${{ github.event.inputs.ref || github.ref }}`），
     `sha-<short>` 取自该 commit；阶段二仍然用当前分支的 `deploy.sh`。

2. **服务器上手工回滚**

   ```bash
   sudo -u deploy /srv/userinfo/deploy.sh <ref>
   ```

   适合 Actions 不可用时应急；这是绕过审批的路径，操作后请补记录。

镜像 tag 保留策略（`DEPLOY_MODE=image` 时）：每次部署推两个 tag —— `prod`（移动）
与 `sha-<short>`（不可变）。`sha-<short>` 是回滚锚点，建议保留最近 ≥10 个。

## 常见问题排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| Actions 里 scp 一步失败 | `SSH_HOST`/`SSH_USER`/`SSH_PORT` 写错；私钥与服务器公钥不匹配；`/srv/userinfo` 不存在或不可写 | 用 `ssh -i deploy_key deploy@146.56.237.33` 复现；确认目录 `deploy:deploy 750` |
| `deploy.sh: Permission denied` | 服务器上文件没有可执行位 | workflow 已 `chmod +x`；手工上传时记得 `chmod +x` |
| `deploy.sh` 报 `bad interpreter: ...^M` | 上传/提交时带了 CRLF 换行 | `.gitattributes` 已含 `*.sh text eol=lf`，重新提交 |
| **部署成功但服务没变** | `deploy.sh` 仍是骨架，只打印 `done` | 属于当前已知状态；需补 §8 的项目部署逻辑 |
| `docker pull` 报 `error from registry: denied` | 该包还不存在（首次部署、阶段一还没跑过），**不是权限问题** | 先让阶段一跑一次创建包 |
| `pull` 长时间停在 `Pulling fs layer` | blob 下载慢，最终可能成功 | 给 `docker pull` 加 timeout；超时回退本机已有镜像并打日志 |
| 头像/头图上传 503 | file 服务未部署、不可达、或令牌缺 `files:*` | 先部署 file；检查 `/srv/file/app.env` 与令牌 scope |
| 头像/头图上传 502 | file 是旧版本，未返回 `permanent=true` | 升级 file 到支持 `ttl_seconds=0` 的版本 |
| 所有资料接口 403 | 令牌是加 scope 之前签发的 | 退出并重新登录，让 SSO 重新签发带 `userinfo:*` 的令牌 |
| `/healthz` 返回 `degraded` | file 不可达，但 DB 正常 | 文本资料仍可用；恢复 file 后自动恢复 |
| Actions 拿不到运行时变量 | 运行时变量属于服务器 `app.env`，不在 Actions 里 | 在服务器上编辑 `/srv/userinfo/app.env` |

## 未来部署逻辑（尚未实现，需另行批准）

`deploy.sh` 的「项目自定义部署逻辑」段应完成：

1. 用 shell 从 `/srv/userinfo/app.env` 读取变量（snap docker 看不到 `/srv`）。
2. `docker pull ghcr.io/elicoudorg/userinfo:${TAG}`（`:prod` 或 `:sha-*`）。
3. 在 `/home/deploy/elicoud-userinfo` 生成 docker 可见的 `.env` + compose。
4. 数据目录 `/home/docker-admin/elicoud/userinfo/data`，容器 uid `1002:1003`。
5. 接入网关网络 `dsh-nas_dsh-net`，宿主调试端口 `127.0.0.1:8003`。
6. 健康检查 `/healthz`，自检通过后才算完成。
7. 失败时非 0 退出，并保留上一版容器/镜像以便回滚。

## 相关文件与配置位置

| 文件 / 配置 | 位置 | 说明 |
|---|---|---|
| CI 工作流 | `.github/workflows/ci.yml` | PR 到 main/prod、push 到 main |
| 部署工作流 | `.github/workflows/deploy-prod.yml` | push 到 prod、workflow_dispatch |
| 部署脚本（版本管理） | `deploy/deploy.sh` | 上传到 `/srv/userinfo/deploy.sh` 执行（当前为骨架） |
| 服务器初始化脚本 | `scripts/bootstrap-server.sh` | 在服务器上以 root 跑一次 |
| 部署密钥与服务器信息 | GitHub Secrets | `SSH_HOST`/`SSH_USER`/`SSH_KEY`/`SSH_PORT` |
| 部署模式开关 | GitHub Variables | `DEPLOY_MODE=image` |
| 运行时环境变量 | 服务器 `/srv/userinfo/app.env` | 600，`deploy:deploy`，不进仓库 |
| Caddy 片段 | `deploy/userinfo-caddy-block.Caddyfile` | `/userinfo/*` → `userinfo:8000` |
