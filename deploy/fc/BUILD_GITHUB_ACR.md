# 用 GitHub + 阿里云 ACR「云端构建」镜像（无需本机 Docker）

适用于：Cloud Shell 里没有 Docker、又不想装 Docker Desktop 时，把代码放在 **GitHub**，由 **容器镜像服务 ACR** 在云端执行 `docker build`，再推到你自己的镜像仓库。

---

## 一、在 GitHub 建仓库并推送代码

### 1.1 在 GitHub 网页创建仓库

1. 打开 https://github.com/new  
2. **Repository name**：例如 `procurement-contract`  
3. 选 **Private**（推荐，避免仓库公开）  
4. **不要**勾选「Add a README」（本地已有代码时用空仓库更好合并）  
5. 点 **Create repository**

### 1.2 在 Windows 上初始化 Git 并推送

在 **PowerShell** 中执行（把 `你的GitHub用户名` 和仓库名改成你的）：

```powershell
cd d:\采购合同

# 若尚未安装 Git：先安装 https://git-scm.com/download/win

git init
git branch -M main

# 首次使用建议配置身份（邮箱可用 GitHub 提供的 noreply 邮箱）
git config user.email "you@users.noreply.github.com"
git config user.name "你的名字"

git add .
git status
```

**务必看一眼 `git status`：** 不应出现 `.env`、`state.json`、`output/` 等；若出现 `.env`，**不要继续 push**，先删掉或检查 `.gitignore`。

```powershell
git commit -m "feat: 飞书合同 FC 镜像与 Webhook"

# 远程地址在 GitHub 仓库页「Code」里复制 HTTPS
git remote add origin https://github.com/你的GitHub用户名/procurement-contract.git
git push -u origin main
```

推送时若提示登录：用 **GitHub 网页 → Settings → Developer settings → Personal access tokens** 生成 **classic token**，勾选 **`repo`**，把 token 当密码输入（用户名填 GitHub 用户名）。

---

## 二、阿里云 ACR 侧准备（杭州）

1. 打开 https://cr.console.aliyun.com/ ，地域选 **华东1（杭州）**  
2. 确认已有 **命名空间** 与 **镜像仓库**（例如 `procurement/contract-fc`），没有则先创建「本地仓库」  
3. 进入该 **镜像仓库** → 左侧或顶部找 **「构建」/「镜像构建」**（不同控制台版本文案略有差异）

---

## 三、在 ACR 里添加「从 GitHub 构建」规则

在镜像仓库的 **构建** 页面：

1. **添加构建规则** / **创建构建规则**  
2. **代码源**：选 **GitHub**（首次需按页面提示完成 **GitHub 授权**，允许阿里云读取你的仓库；私有仓库需勾选相应权限）  
3. **命名空间/仓库**：选你在 GitHub 上刚建的 `你的用户名/procurement-contract`  
4. **分支**：`main`（若你用 `master` 则选 `master`）  
5. **Dockerfile 路径**：填 **`deploy/fc/Dockerfile`**（与仓库内路径一致）  
6. **构建目录 / 上下文 / Dockerfile 所在目录**（名称因控制台而异）：选 **仓库根目录** 或填 **`/`**、**`.`**，含义必须是：**构建上下文为整个仓库根目录**（与本地命令 `docker build -f deploy/fc/Dockerfile .` 里的 **最后一个 `.`** 一致）  
7. **镜像 Tag**：例如 `v1` 或 `latest`  
8. 保存后点击 **立即构建** / **运行构建**

构建成功后在同一页面可看到 **构建日志**；完成后镜像 Tag 会出现在该仓库的 **镜像版本** 列表里。

> 官方说明可参考（以控制台实际为准）：  
> https://help.aliyun.com/zh/acr/user-guide/create-an-image-building-rule-based-on-the-github-code-repository

---

## 四、常见问题

| 现象 | 处理 |
|------|------|
| 私有仓库拉不到代码 | 重新做 GitHub OAuth，确认授权了 **private repo** |
| 构建报 `COPY requirements.txt` 找不到 | **构建上下文** 不是仓库根；改回根目录 `.` |
| 构建超时 | Dockerfile 里 `apt-get` + `pip` 较慢，在构建规则里把 **超时时间** 调大（如 30 分钟） |
| 不想把 `config.py` 推上去 | 本仓库 `.gitignore` 已忽略 `config.py`；云上只用 **FC 环境变量** 注入密钥即可 |

---

## 五、构建完成后

1. 在 ACR 镜像仓库 → **镜像版本** 里复制完整地址，例如：  
   `registry.cn-hangzhou.aliyuncs.com/你的命名空间/contract-fc:v1`  
2. 打开 **函数计算 FC** → 创建/更新函数 → **自定义容器** → 选择上述镜像  
3. 监听端口 **9000**，环境变量见根目录 `deploy/fc/README.md`
