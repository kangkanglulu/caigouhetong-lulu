# 阿里云函数计算 FC（自定义镜像 · LibreOffice PDF）

本目录用于将 **飞书 Webhook** 部署到阿里云函数计算（**自定义容器**），实现 **关机后仍响应** 多维表格事件；容器内通过 **LibreOffice** 将 docx 转为 PDF（与 Windows Word 导出相比版式可能略有差异）。

主项目根目录的 `webhook_app.py` 已支持 `POST /`（根路径），便于 FC 绑定域名时少写路径。

---

## 一、前置条件

1. 阿里云账号，已开通 **函数计算 FC**、**容器镜像服务 ACR**（个人版即可）。
2. 本机已安装 **Docker**（用于构建与推送镜像）。
3. 飞书应用权限、多维表格、事件订阅等已按根目录 `README.md` 配置完成。

---

## 二、构建镜像

### 方式 A：本机 / Cloud Shell 有 Docker 时

在项目根目录（与 `Dockerfile` 同级为 **`deploy/fc`** 时，构建命令需指定上下文为仓库根目录）：

```bash
cd /path/to/采购合同   # 仓库根目录，内含 webhook_app.py、template/ 等

docker build -f deploy/fc/Dockerfile -t procurement-contract-fc:latest .
```

### 方式 B：无 Docker → GitHub + ACR 云端构建（推荐给你）

见 **[BUILD_GITHUB_ACR.md](./BUILD_GITHUB_ACR.md)**：代码推 GitHub，在 ACR 控制台绑定仓库并构建，无需本机 `docker build`。

---

本地试跑（映射 9000 端口，需已本地 `docker build`）：

```bash
docker run --rm -p 9000:9000 ^
  -e FEISHU_APP_ID=cli_xxx ^
  -e FEISHU_APP_SECRET=xxx ^
  -e BITABLE_APP_TOKEN=xxx ^
  -e BITABLE_TABLE_ID=tblxxx ^
  -e FEISHU_RECEIVER_OPEN_IDS=ou_xxx ^
  -e PDF_ENGINE=libreoffice ^
  -e DELIVERY_FORMAT=pdf ^
  procurement-contract-fc:latest
```

另开终端测试 challenge（飞书 URL 校验）：

```bash
curl -s -X POST http://127.0.0.1:9000/ -H "Content-Type: application/json" -d "{\"challenge\":\"test123\"}"
```

应返回：`{"challenge":"test123"}`

---

## 三、推送镜像到 ACR

1. 在阿里云 ACR 创建命名空间与镜像仓库（例如 `procurement/contract-fc`）。
2. 登录、打 tag、推送（控制台「镜像仓库」页有完整命令，以下为示例）：

```bash
docker login --username=你的ACR登录名 registry.cn-hangzhou.aliyuncs.com
docker tag procurement-contract-fc:latest registry.cn-hangzhou.aliyuncs.com/你的命名空间/procurement-contract-fc:v1
docker push registry.cn-hangzhou.aliyuncs.com/你的命名空间/procurement-contract-fc:v1
```

---

## 四、创建 FC 函数（自定义容器）

1. 登录 [函数计算控制台](https://fcnext.console.aliyun.com/) → 创建服务（如 `feishu-contract`）。
2. **创建函数** → 使用 **自定义容器** → 选择上述 ACR 镜像与 tag。
3. **请求处理程序类型**：HTTP 函数（或通过「函数 URL / API 网关」暴露 HTTPS，以你所在地域控制台为准）。
4. **监听端口**：与 Dockerfile 中一致，默认 **9000**（环境变量 `LISTEN_PORT` 可改，需与 FC 控制台「端口」一致）。
5. **实例规格**：建议至少 **1 vCPU / 1 GB 内存**；首次冷启动 + LibreOffice 转换较慢，**超时时间建议 ≥ 300 秒**。
6. **并发**：建议单实例并发为 **1** 或较低值，避免同一多维表格并发写（飞书侧可能冲突）。

---

## 五、配置环境变量（函数「环境变量」页）

与根目录 `.env.example` 键名一致，至少包括：

| 变量名 | 说明 |
|--------|------|
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 应用 Secret |
| `BITABLE_APP_TOKEN` | 多维表格 app_token |
| `BITABLE_TABLE_ID` | 数据表 table_id |
| `FEISHU_RECEIVER_OPEN_IDS` | 接收合同 IM 的 open_id，多个逗号分隔 |
| `PDF_ENGINE` | 建议固定为 **`libreoffice`** |
| `DELIVERY_FORMAT` | 建议 **`pdf`** |
| `FEISHU_DRIVE_FOLDER_TOKEN` | （可选）云盘文件夹 token |

**不要在镜像里 baked 密钥**；一律用 FC 环境变量或 KMS（进阶）。

---

## 六、HTTPS 与飞书事件订阅 URL

飞书要求订阅地址为 **HTTPS**。常见做法：

- 为函数绑定 **自定义域名** + **SSL 证书**（阿里云可免费申请或使用已有证书）；
- 或使用 API 网关对 FC 做 HTTPS 暴露（按控制台向导）。

**请求 URL 示例**（任选其一，与代码路由一致即可）：

- `https://contract.example.com/`（根路径，`POST /`）
- `https://contract.example.com/webhook/feishu`

在飞书开放平台 → 事件订阅 → 填写上述 URL；订阅 **多维表格 · 记录新增**（`bitable.record.created`）。

---

## 七、FC 上为何优先用 Webhook

- **轮询** `main.py` 依赖本地 `state.json`；FC 实例磁盘非持久时，幂等文件会丢失。**云上关机也跑** 推荐 **Webhook**。
- 若必须用轮询，请挂载 **NAS** 到 `state.json` 路径，或放弃本地 state、仅靠表格「合同状态」字段做幂等。

---

## 八、PDF 与 Word 版式说明（LibreOffice）

- Linux / FC **无法**安装 Microsoft Word，因此使用 **LibreOffice** 导出 PDF。
- 中文建议模板使用 **Noto Sans CJK / 思源黑体** 等镜像内已装字体；复杂表格分页可能与 Word 略有不同，上线前请用真实合同在 FC 环境做一次验收。

---

## 九、排错

| 现象 | 处理 |
|------|------|
| 日志报未找到 soffice | 镜像未正确安装 LibreOffice，检查 Dockerfile `apt-get` 是否执行成功 |
| 转换超时 | 增大 FC 函数超时；或设置环境变量 `LIBREOFFICE_TIMEOUT_SEC=300` |
| 飞书校验 URL 失败 | 确认 HTTPS、路径为 `POST /` 或 `/webhook/feishu`，且返回 JSON 含 `challenge` |
| IM 发不出 | 检查应用机器人能力、权限、`FEISHU_RECEIVER_OPEN_IDS` |

更多飞书侧排查见根目录 `README.md`。
