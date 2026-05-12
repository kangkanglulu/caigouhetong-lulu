# -*- coding: utf-8 -*-
"""
复制本文件为 config.py 并修改；或改用环境变量（见 main 加载逻辑）。
敏感信息建议放 .env，勿提交仓库。
"""

import os
from pathlib import Path

# ----- 飞书应用凭证 -----
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# ----- 多维表格 -----
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "")
# 可选：仅查询某视图
VIEW_ID = os.environ.get("BITABLE_VIEW_ID", "") or None

# ----- 路径 -----
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template" / "contract_template.docx"
OUTPUT_DIR = BASE_DIR / "output"

# ----- 业务字段（请与多维表格列名完全一致）-----
# PRD 默认列名；若你的表格不同，请修改此处
FIELD_PURCHASE_COMPANY = "采购公司"
FIELD_SUPPLIER = "供应商"
FIELD_ORDER_NO = "采购订单号"
FIELD_ORDER_CREATED_AT = "订单创建日期"
FIELD_LINE_NO = "采购行号"
FIELD_MATERIAL_CODE = "物料编码"
FIELD_MATERIAL_NAME = "物料名称"
FIELD_DELIVERY_DATE = "交期"
FIELD_QTY = "数量"
FIELD_AMOUNT = "金额"
FIELD_CONTRACT_STATUS = "合同状态"

# 单选项（与表格中单选选项文案一致）
STATUS_PENDING = "待生成"
STATUS_DONE = "已生成"
STATUS_FAILED = "生成失败"

# ----- 交付方式 -----
# 输出 / 发送的文件格式："pdf"（默认）或 "docx"
# pdf 模式：
#   - Windows：默认用 Microsoft Word COM（与 Word 版式一致）；
#   - Linux / 阿里云 FC：用 LibreOffice headless（与 Word 略有差异，见 deploy/fc/README.md）。
# 可通过环境变量 PDF_ENGINE=auto|word|libreoffice 覆盖（FC 建议 libreoffice 或 auto）。
DELIVERY_FORMAT = os.environ.get("DELIVERY_FORMAT", "pdf").lower()

# 是否在生成后将文件通过飞书 IM 发给成员（需要配置 RECEIVER_OPEN_IDS）
SEND_IM_MESSAGE = True
RECEIVER_OPEN_IDS = [
    x.strip()
    for x in os.environ.get("FEISHU_RECEIVER_OPEN_IDS", "").split(",")
    if x.strip()
]

# 是否上传到云空间并在表格回写「合同链接」（列名见下）
UPLOAD_TO_DRIVE = bool(os.environ.get("FEISHU_DRIVE_FOLDER_TOKEN", ""))
DRIVE_FOLDER_TOKEN = os.environ.get("FEISHU_DRIVE_FOLDER_TOKEN", "")
FIELD_CONTRACT_LINK = "合同链接"

# ----- 运行参数 -----
# 轮询间隔（秒）；Webhook 模式下可忽略
POLL_INTERVAL_SEC = 60
# 本地状态文件：记录已处理的 record_id，用于幂等（PRD 要求）
STATE_FILE = BASE_DIR / "state.json"

# ----- Webhook -----
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8765
# 事件订阅校验 Token（飞书后台「事件订阅」里配置，可选）
VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
# 加密密钥（启用 Encrypt Key 时验证签名用；未启用可留空）
ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")
