# -*- coding: utf-8 -*-
"""
飞书事件订阅回调服务（HTTP）。

PRD 要求订阅「多维表格 - 记录新增」bitable.record.created；
首次配置 URL 时飞书会发送 challenge，必须原样返回 {"challenge": "..."}。

部署：公网可访问的 HTTPS 地址（阿里云 FC / 任意容器 / ngrok 调试）。
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, request

from feishu_client import FeishuClient, feishu_field_to_plain, load_config_module
from main import (
    _group_records_by_order,
    _setup_logging,
    process_one_order,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


def _extract_record_from_event(body: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """
    从不同版本的事件负载中解析 app_token、table_id、record_id。
    若解析失败返回 (None, None, None)。
    """
    ev = body.get("event")
    if isinstance(ev, dict):
        rid = ev.get("record_id")
        at = ev.get("app_token")
        tid = ev.get("table_id")
        if rid:
            return at, tid, rid

    # 少数兼容路径：扁平结构
    rid = body.get("record_id")
    if rid:
        return body.get("app_token"), body.get("table_id"), rid

    return None, None, None


@app.route("/", methods=["POST"])
@app.route("/webhook/feishu", methods=["POST"])
@app.route("/feishu/events", methods=["POST"])
def feishu_webhook():
    body = request.get_json(force=True, silent=True) or {}

    # URL 验证 / challenge（开发指引常见字段名）
    ch = body.get("challenge")
    if ch is not None:
        logger.info("响应飞书 URL 校验 challenge")
        return jsonify({"challenge": ch})

    # 事件 2.0：可能是加密载荷；未配置解密时仅记录日志
    header = body.get("header") or {}
    event_type = header.get("event_type") or body.get("type") or ""

    app_token, table_id, record_id = _extract_record_from_event(body)

    cfg = load_config_module()
    if app_token and cfg.BITABLE_APP_TOKEN and app_token != cfg.BITABLE_APP_TOKEN:
        logger.warning("事件 app_token 与配置不一致，忽略: %s", app_token)
        return jsonify({"code": 0})
    if table_id and cfg.BITABLE_TABLE_ID and table_id != cfg.BITABLE_TABLE_ID:
        logger.warning("事件 table_id 与配置不一致，忽略: %s", table_id)
        return jsonify({"code": 0})

    if record_id and (
        "bitable.record.created" in event_type
        or "record.created" in event_type
        or event_type == ""
    ):
        logger.info("收到记录事件 record_id=%s type=%s", record_id, event_type)
        client = FeishuClient(cfg.APP_ID, cfg.APP_SECRET)
        try:
            # 1) 取出本条记录所属的采购订单号
            rec = client.bitable_get_record(
                cfg.BITABLE_APP_TOKEN, cfg.BITABLE_TABLE_ID, record_id
            )
            fields = rec.get("fields") or {}
            order_no_raw = feishu_field_to_plain(fields.get(cfg.FIELD_ORDER_NO))
            order_no = str(order_no_raw or "").strip()
            if not order_no:
                logger.warning("事件记录 %s 未填采购订单号，忽略", record_id)
                return jsonify({"code": 0})

            # 2) 拉取该订单号下的全部行（含已生成的行，便于完整合并明细）
            all_records = client.bitable_iter_records(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                view_id=getattr(cfg, "VIEW_ID", None),
            )
            groups = _group_records_by_order(all_records, cfg)
            same_order = groups.get(order_no) or [rec]

            # 3) 合并生成
            process_one_order(client, cfg, order_no, same_order)
        except Exception as e:
            logger.exception("处理事件失败: %s", e)
            return jsonify({"code": 1, "msg": str(e)}), 500

    return jsonify({"code": 0})


def main():
    parser = argparse.ArgumentParser(description="飞书 Webhook 回调服务")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _setup_logging(args.verbose)

    cfg = load_config_module()
    host = args.host or getattr(cfg, "WEBHOOK_HOST", "0.0.0.0")
    port = args.port or int(getattr(cfg, "WEBHOOK_PORT", 8765))
    logger.info("启动 Webhook 监听 http://%s:%s/webhook/feishu", host, port)
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
