# -*- coding: utf-8 -*-
"""
将多维表格「一行记录」映射为合同模板上下文（Jinja2/docxtpl 变量）。

PRD 约定字段：
  甲方 ← 采购公司，乙方 ← 供应商，合同号码 ← 采购订单号，
  签约日期 ← 订单创建日期（YYYY年MM月DD日），
  明细含：采购行号、物料编码、物料名称、交期、数量、金额，
  合同金额 = 当前行金额。

若你的表格列名不同，请在 config.py 中修改 FIELD_* 常量，
必要时调整本文件中的 template_keys 或增加计算逻辑。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from feishu_client import feishu_field_to_plain


def _format_sign_date(raw: Any) -> str:
    """将飞书日期字段转为「YYYY年MM月DD日」。"""
    if raw is None or raw == "":
        return ""
    text = str(feishu_field_to_plain(raw))
    # 飞书日期可能是时间戳毫秒
    if text.isdigit():
        ms = int(text)
        if ms > 1_000_000_000_000:
            ms //= 1000
        try:
            dt = datetime.fromtimestamp(ms)
            return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"
        except (ValueError, OSError):
            pass
    # 已是中文或 ISO 日期则尽力规范化
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = m.groups()
        return f"{y}年{mo}月{d}日"
    return text


def record_to_contract_context(
    fields: dict[str, Any],
    *,
    field_config: Any,
) -> dict[str, Any]:
    """
    根据 config 中的 FIELD_* 名称，从 record.fields 抽取模板变量。

    模板中建议使用与下列 ctx 键一致的占位符（docxtpl：{{ 甲方 }}）。
    用户亦可直接在 Word 模板里使用英文键：{{ buyer }} 等——只需与本函数返回键一致。
    """
    fc = field_config

    def g(name_attr: str) -> Any:
        col = getattr(fc, name_attr, None)
        if not col:
            return ""
        return feishu_field_to_plain(fields.get(col))

    buyer = g("FIELD_PURCHASE_COMPANY")
    seller = g("FIELD_SUPPLIER")
    contract_no = g("FIELD_ORDER_NO")
    created = g("FIELD_ORDER_CREATED_AT")
    sign_date = _format_sign_date(fields.get(fc.FIELD_ORDER_CREATED_AT))

    line_no = g("FIELD_LINE_NO")
    material_code = g("FIELD_MATERIAL_CODE")
    material_name = g("FIELD_MATERIAL_NAME")
    delivery = g("FIELD_DELIVERY_DATE")
    qty = g("FIELD_QTY")
    amount = g("FIELD_AMOUNT")

    # PRD：合同金额 = 当前行金额
    total_amount = amount

    # 商品行（用于模板中的 {%tr for item in items %} 行循环）
    item_row = {
        "采购订单号": contract_no,
        "采购行号": line_no,
        "物料编码": material_code,
        "物料名称": material_name,
        "交期": delivery,
        "数量": qty,
        "金额": amount,
    }

    # 双语键方便模板迁移
    ctx = {
        # 中文键（推荐与 PRD 表述一致）
        "甲方": buyer,
        "乙方": seller,
        "合同号码": contract_no,
        "签约日期": sign_date,
        "采购行号": line_no,
        "物料编码": material_code,
        "物料名称": material_name,
        "交期": delivery,
        "数量": qty,
        "金额": amount,
        "合同金额": total_amount,
        # 商品明细列表：单条记录默认仅含 1 行；
        # 若后续需要按订单号合并多条记录为同一份合同，请在 main.py 中
        # 把同一订单号下的若干条 item_row 合并到 items 即可。
        "items": [item_row],
        # 英文别名
        "buyer": buyer,
        "seller": seller,
        "contract_no": contract_no,
        "sign_date": sign_date,
        "line_no": line_no,
        "material_code": material_code,
        "material_name": material_name,
        "delivery_date": delivery,
        "qty": qty,
        "amount": amount,
        "total_amount": total_amount,
        # 原始创建日期字符串（备用）
        "order_created_raw": created,
    }

    missing: list[str] = []
    if not buyer:
        missing.append(fc.FIELD_PURCHASE_COMPANY)
    if not seller:
        missing.append(fc.FIELD_SUPPLIER)
    if not contract_no:
        missing.append(fc.FIELD_ORDER_NO)

    ctx["_missing_required_fields"] = missing
    return ctx


def validate_context(ctx: dict[str, Any]) -> list[str]:
    """返回缺失的必填业务字段列名列表。"""
    return list(ctx.get("_missing_required_fields") or [])
