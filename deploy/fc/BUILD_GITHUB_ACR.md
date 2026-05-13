# 用 GitHub Actions 构建并推送到阿里云 ACR（无需本机 Docker）

> 适用：阿里云 ACR **个人版** 已不再支持「源代码构建」。本方案在 GitHub Actions 里
> 跑 `docker build`，构建完成后自动推到你自己的 ACR 仓库；全过程免费、不依赖本机 Docker。

仓库内已包含 workflow：`.github/workflows/build-and-push-acr.yml`

---

## 一、在 GitHub 仓库里配置 5 个 Secret

仓库页 → **Settings** → 左侧 **Secrets and variables → Actions** → **New repository secret**，依次添加：

| Secret 名称 | 取值（参考你 ACR 仓库页 “操作指南”） |
|--------------|---------------------------------------|
| `ACR_REGISTRY` | 形如 `crpi-xxxxxx.cn-hangzhou.personal.cr.aliyuncs.com`（**只填域名**，不带 `https://`） |
| `ACR_USERNAME` | ACR 登录名，例如 `aliyunXXXXXXXXX` |
| `ACR_PASSWORD` | 你在 ACR「访问凭证」里设置的 **固定密码** |
| `ACR_NAMESPACE` | 命名空间，例如 `caigouhetong` |
| `ACR_REPO` | 镜像仓库名，例如 `contract-fc` |

> 这些 Secret 仅 Actions 运行时可读取，不会出现在日志或仓库代码里。

---

## 二、触发构建

两种触发方式：

1. **手动触发（推荐第一次用）**：仓库页 → **Actions** → 选 `Build & Push to Aliyun ACR` → 右上 **Run workflow** → `tag` 填 `v1` → 点 **Run workflow**。
2. **自动触发**：往 `main` 分支 push 任何代码（仅 `*.py`、`requirements.txt`、`deploy/fc/Dockerfile`、`template/**`、`assets/**` 改动会触发；推到 `latest`）。

构建大约 **4~8 分钟**（第一次最慢，要装 LibreOffice 与字体）；后续命中缓存通常 **30~90 秒**。

完成后到 ACR 控制台 → 该镜像仓库 → **镜像版本** 应该能看到：

- `v1`（或你手动填的 tag）
- 一个 `<git-sha>` 形式的 tag（用于追溯具体哪次提交）

---

## 三、下一步：在函数计算 FC 使用镜像

完整地址形如：

```
${ACR_REGISTRY}/${ACR_NAMESPACE}/${ACR_REPO}:v1
```

后续 FC 控制台 → 创建函数 → **自定义容器** → 选用这个镜像（**端口 9000**，环境变量见 `deploy/fc/README.md`）。

---

## 四、常见问题

| 现象 | 处理 |
|------|------|
| Actions 报 `denied: requested access to the resource is denied` | 检查 `ACR_USERNAME` / `ACR_PASSWORD`；ACR「访问凭证」页 **重置固定密码** 再更新 Secret |
| 推送地址错 | `ACR_REGISTRY` **不要带** `https://`，**不要带** `/` 结尾 |
| 想换 tag 推 `v2` | Actions 页面 **Run workflow**，`tag` 填 `v2` |
| 构建超时 | Dockerfile 里安装 LibreOffice 较慢，已开启 GHA 缓存；首次 8 分钟内属正常 |
| 想关闭自动构建 | 删除 workflow 文件里的 `push:` 部分，仅保留 `workflow_dispatch:` |
