# 购销合同自动生成（飞书多维表格 · Python）

本项目对应桌面 PRD《购销合同自动生成 · 极简版》，在用户选定 **方案 C（Python + 飞书开放平台 API）** 的前提下实现：**多维表格新增一行 → 自动生成 Word 合同 →（可选）上传云空间并回写链接 →（可选）通过飞书 IM 将文件发给指定成员**，并在表格中更新 **合同状态**。

> **与 PRD 的差异说明**  
> PRD 原文建议使用 **HTML 模板 + Puppeteer 生成 PDF**、**Node.js + 阿里云函数计算**。当前仓库采用 **docxtpl + Word 模板（.docx）→ PDF**：Windows 下用 **Microsoft Word COM** 导出（与 Word 版式一致）；Linux / **阿里云函数计算 FC** 下用 **LibreOffice headless**（版式与 Word 可能略有差异）。部署到 FC 的步骤见 **`deploy/fc/README.md`**。如临时只要 docx，可在 `config.py` 把 `DELIVERY_FORMAT` 改为 `"docx"`。

---

## 项目结构

```
d:\采购合同\
├── README.md                 # 本说明
├── requirements.txt
├── config.example.py         # 复制为 config.py 后填写
├── .env.example              # 可选：环境变量方式注入密钥
├── .gitignore
├── feishu_client.py          # 飞书 API（令牌缓存、重试、多维表格、IM、云空间）
├── field_mapping.py          # PRD 字段 → 模板变量映射
├── contract_generator.py     # docxtpl 渲染
├── main.py                   # 轮询模式入口
├── webhook_app.py            # 事件订阅（Webhook）入口
├── deploy/fc/                # 阿里云函数计算（自定义镜像 + LibreOffice）
│   ├── Dockerfile
│   └── README.md             # FC 部署步骤（ACR、环境变量、HTTP 触发器、飞书订阅 URL）
├── build_template.py         # 一键生成示例模板 template/contract_template.docx
├── template/
│   └── contract_template.docx  # 运行 build_template.py 后生成；可自行美化
├── output/                   # 生成的合同文件（运行后自动创建）
└── state.json                # 轮询幂等状态（自动生成，勿提交仓库）
```

---

## PRD 要点摘要（已实现或可配置）

| PRD 条目 | 实现说明 |
|----------|----------|
| 字段：甲方←采购公司、乙方←供应商、合同号码←采购订单号、签约日期←订单创建日期 | `field_mapping.py` + `config.*` 列名 |
| 明细：采购行号、物料编码、物料名称、交期、数量、金额 | 同上 |
| 合同金额 = 当前行金额 | 映射为 `合同金额`/`total_amount` |
| 触发：表格新增行 | **推荐**：飞书事件订阅 `bitable.record.created` → `webhook_app.py`；**备选**：`main.py` 定时轮询「待生成」 |
| 交付：飞书文件消息（默认 PDF） | `feishu_client.im_upload_file` + `im_send_file_message`，`DELIVERY_FORMAT` 控制 docx / pdf |
| 一个订单号 → 一份合同 | `main.process_one_order` 按 `采购订单号` 分组，多行合并到 `items` 行循环 |
| 状态回写：待生成 / 已生成 / 生成失败 | 通过「更新记录」接口（HTTP PUT），同订单号下所有行一起回写 |
| 幂等 | 本地 `state.json` + 跳过已「已生成」记录 |

---

## 飞书自建应用：创建与权限

1. 打开 [飞书开放平台](https://open.feishu.cn/) → **创建企业自建应用**。  
2. 记录 **App ID**、**App Secret**（勿写入仓库；可用 `.env`）。  
3. **权限管理** 中至少开通（与 PRD 一致，名称以控制台为准）：

   - `bitable:record`（读取多维表格记录）
   - `bitable:record:write`（回写合同状态 / 链接）
   - `im:message`（发送消息）
   - `im:resource`（上传 IM 文件；上传合同附件）

4. **版本发布**：申请权限后需重新发布应用，权限才会生效。  
5. 打开目标 **多维表格** → **更多** → **添加应用**，将自建应用加为表格协作者。

---

## 多维表格字段配置（默认列名）

请在表格中建立如下列（名称需与 `config.py` 中 `FIELD_*` 一致，或改配置以匹配你的列名）：

| 建议列名 | 用途 |
|----------|------|
| 采购公司 | 甲方 |
| 供应商 | 乙方 |
| 采购订单号 | 合同号码 |
| 订单创建日期 | 签约日期（程序格式化为「YYYY年MM月DD日」） |
| 采购行号、物料编码、物料名称、交期、数量、金额 | 明细 |
| 合同状态 | 单选：`待生成` / `已生成` / `生成失败` |
| 合同链接 | （可选）文本或链接类型，用于回写云文档 URL |

新增行时请将 **合同状态** 设为 **待生成**，或留空（程序同样会尝试处理留空且未在 `state.json` 中的记录）。

### 「一个采购订单号 = 一份合同」的分组逻辑

- 程序会按 **采购订单号** 字段把表中所有记录分组；
- 同一订单号下 **任意一行** 出现「待生成」/ 空状态，即触发该订单号的合同生成；
- 渲染时，该订单号下 **全部行**（含已生成的行）会按 **采购行号** 升序汇总到合同的明细表里，**合同金额 = 全部行金额求和**；
- 处理成功后，该订单号下 **所有行** 都会被回写为「已生成」，且每个 `record_id` 都加入 `state.json` 幂等集；
- 想给某订单号补充新行？直接在表格里新增并把状态置「待生成」即可，**整张合同会用完整的明细重新生成并重新发送**。

---

## 配置步骤

1. 复制配置：`copy config.example.py config.py`（或在 Linux/macOS：`cp config.example.py config.py`）。  
2. 填写 `APP_ID`、`APP_SECRET`、`BITABLE_APP_TOKEN`、`BITABLE_TABLE_ID`。  
   - 表格 URL：`https://xxx.feishu.cn/base/<APP_TOKEN>?table=<TABLE_ID>`  
3. 复制 `.env.example` 为 `.env` 并填写密钥（可选；需配合 `python-dotenv`，已在 `main.py` / `webhook_app.py` 入口加载）。  
4. 生成 Word 模板：`python build_template.py`  
5. 用 Word 打开 `template/contract_template.docx`，按法务要求调整正文，**保留 `{{ }}` 占位符名称**（与 `field_mapping.py` 输出一致）。  
6. 配置接收人：在 `config.py` 或环境变量 `FEISHU_RECEIVER_OPEN_IDS` 中填写成员 **open_id**（多个英文逗号分隔）。获取方式：飞书管理后台通讯录或通讯录 API。

---

## 运行方式

### 1）轮询模式（无需公网 IP）

适合内网或本地定时任务：

```bash
cd /d d:\采购合同
pip install -r requirements.txt
python main.py --once        # 执行一轮扫描后退出
python main.py               # 按 POLL_INTERVAL_SEC 周期扫描
```

### 2）事件订阅 / Webhook（与 PRD 一致）

1. 部署 `webhook_app.py` 到公网可访问地址（HTTPS），路径示例：`https://your.domain/`（根路径）或 `https://your.domain/webhook/feishu`。  
2. **阿里云函数计算**：见 **[deploy/fc/README.md](./deploy/fc/README.md)**（自定义镜像 + LibreOffice，关机仍可用）。
2. 在开放平台 **事件订阅** 中填写上述 URL；首次校验需返回 `challenge`（代码已实现）。  
3. 订阅事件：**多维表格 · 记录新增**（`bitable.record.created`，名称以控制台为准）。  
4. 本地调试可用 ngrok 等内网穿透。

```bash
python webhook_app.py -v
```

默认监听 `http://0.0.0.0:8765/webhook/feishu`，可在 `config.py` 修改 `WEBHOOK_HOST` / `WEBHOOK_PORT`。容器 / FC 上通常使用 `gunicorn` 监听 `LISTEN_PORT`（默认 9000），见 `deploy/fc/Dockerfile`。

### 3）阿里云函数计算 FC（关机也跑）

将 Webhook 部署到 **FC 自定义镜像** 后，无需本机常开。镜像内已含 **LibreOffice** 用于 PDF。完整步骤（ACR 推送、函数配置、HTTPS、环境变量）见 **[deploy/fc/README.md](./deploy/fc/README.md)**。

---

## 日志与故障排查

- **401 / token 失效**：`feishu_client` 会在失败码 `99991663` 时自动刷新 `tenant_access_token`。  
- **权限不足**：检查应用是否已发布、表格是否已添加应用为协作者、权限是否与上文清单一致。  
- **单选字段无法写入**：部分租户需使用选项 ID 而非文案；可在开放平台「多维表格字段」API 查看选项 ID，并在 `bitable_patch_record` 处按需改为 ID（代码注释处可扩展）。  
- **合同链接列为 URL 类型**：若回写失败，可改为「文本」列存储 URL，或根据开放平台文档构造 URL 字段结构（需在 `main.py` 中自定义回写字段结构）。  
- **IM 发送失败**：确认机器人具备发送权限，且 `open_id` 正确；接收人与应用需在同一租户可用范围。

---

## 常见问题（FAQ）

**Q：PDF 是怎么生成的？**  
Windows 默认用 **Word COM**；Linux / 阿里云 FC 用 **LibreOffice**（版式可能与 Word 略有差异）。环境变量 `PDF_ENGINE=auto|word|libreoffice` 可强制指定。

**Q：能否不用 IM，只写云盘链接？**  
可以。设置 `SEND_IM_MESSAGE = False`，并配置 `UPLOAD_TO_DRIVE` / `DRIVE_FOLDER_TOKEN` / `FIELD_CONTRACT_LINK`。

**Q：Webhook 与轮询能同时开吗？**  
可以，但请注意幂等：表格「合同状态」与 `state.json` 应避免重复处理冲突。

---

## 需要你本地补充的配置清单（汇总）

以下内容无法从 PRD 自动确定，请你按实际环境填写：

1. **飞书应用** `APP_ID`、`APP_SECRET`。  
2. **多维表格** `BITABLE_APP_TOKEN`、`BITABLE_TABLE_ID`（及可选 `VIEW_ID`）。  
3. **表格列名** 若与默认不一致，修改 `config.py` 中全部 `FIELD_*`。  
4. **合同模板** `template/contract_template.docx`：法务定稿，占位符与 `field_mapping.py`  variables 对齐。  
5. **接收人 open_id**（启用 IM 时必填）。  
6. **云空间文件夹 token**（若需回写「合同链接」）：在飞书云空间中打开文件夹，从 URL 或开放平台接口获取 `folder_token`。  
7. **事件订阅公网 URL**（若使用 Webhook）：需 HTTPS 且能通过飞书校验。  
8. **（可选）Encrypt Key / 签名验证**：若启用事件加密，需在 `webhook_app.py` 中补充解密与签名校验逻辑（当前仅实现 challenge 与明文 JSON 路径）。  
9. **PDF 引擎**：云上部署请设 `PDF_ENGINE=libreoffice`；本地 Windows 可省略（`auto` 会用 Word）。详见 `contract_generator.py` 与 `deploy/fc/README.md`。

---

## 许可证

内部业务项目；版权归原作者所有。
