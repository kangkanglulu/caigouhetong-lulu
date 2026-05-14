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


def _parse_feishu_date_to_dt(raw: Any) -> datetime | None:
    """从飞书日期/时间戳字段解析出 datetime（本地时区）。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        if "date" in raw:
            raw = raw.get("date")
        elif "value" in raw:
            return _parse_feishu_date_to_dt(raw.get("value"))
        else:
            return None
    if isinstance(raw, list) and raw:
        return _parse_feishu_date_to_dt(raw[0])
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        n = int(raw)
        if n > 1_000_000_000_000:
            n //= 1000
        try:
            return datetime.fromtimestamp(n)
        except (ValueError, OSError):
            return None

    text = str(feishu_field_to_plain(raw)).strip()
    if not text:
        return None
    # 毫秒或秒级时间戳
    if text.isdigit():
        n = int(text)
        if n > 1_000_000_000_000:
            n //= 1000
        try:
            return datetime.fromtimestamp(n)
        except (ValueError, OSError):
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    m2 = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if m2:
        y, mo, d = map(int, m2.groups())
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    return None


def _format_date_cn(raw: Any) -> str:
    """飞书日期 / 时间戳 →「YYYY年MM月DD日」（签约日期、交期等）。"""
    dt = _parse_feishu_date_to_dt(raw)
    if dt is not None:
        return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"
    if raw is None or raw == "":
        return ""
    return str(feishu_field_to_plain(raw)).strip()


def _format_amount_two_decimals(val: Any) -> str:
    """金额：保留小数点后两位（与 Excel 展示习惯一致）。"""
    if val is None or val == "":
        return ""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{float(val):.2f}"
    s = str(feishu_field_to_plain(val)).strip().replace(",", "").replace("，", "")
    if not s:
        return ""
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return str(feishu_field_to_plain(val)).strip()


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
    sign_date = _format_date_cn(fields.get(fc.FIELD_ORDER_CREATED_AT))

    line_no = g("FIELD_LINE_NO")
    material_code = g("FIELD_MATERIAL_CODE")
    material_name = g("FIELD_MATERIAL_NAME")
    delivery = _format_date_cn(fields.get(fc.FIELD_DELIVERY_DATE))
    qty = g("FIELD_QTY")
    amount_raw = g("FIELD_AMOUNT")
    amount = _format_amount_two_decimals(amount_raw)

    # PRD：合同金额 = 当前行金额（两位小数）
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
