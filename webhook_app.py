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
    _should_process_record,
    process_one_order,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


def _extract_records_from_event(
    body: dict[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    """
    从不同版本的事件负载中解析 app_token、table_id、record_id 列表。

    支持事件：
      - bitable.record.created（老版本，event.record_id）
      - drive.file.bitable_record_changed_v1（v2.0，event.action_list[].record_id）
      - drive.file.bitable_record_changed_v1 中字段为 record_id_list 的变种
      - 极少数扁平结构兼容

    只关心「新增/修改」动作（record_added / record_edited），删除动作忽略。
    """
    ev = body.get("event")
    if not isinstance(ev, dict):
        # 扁平兼容
        rid = body.get("record_id")
        if rid:
            return body.get("app_token"), body.get("table_id"), [rid]
        return None, None, []

    app_token = ev.get("app_token") or ev.get("file_token")
    table_id = ev.get("table_id")

    record_ids: list[str] = []

    # 老版本：单条 record_id
    rid_single = ev.get("record_id")
    if rid_single:
        record_ids.append(rid_single)

    # v2.0：action_list
    action_list = ev.get("action_list")
    if isinstance(action_list, list):
        for act in action_list:
            if not isinstance(act, dict):
                continue
            action_name = (act.get("action") or "").lower()
            # 忽略删除动作；新增/修改/未知都尝试处理
            if "delet" in action_name:
                continue
            rid = act.get("record_id")
            if rid:
                record_ids.append(rid)

    # 另一种变种：record_id_list
    rid_list = ev.get("record_id_list")
    if isinstance(rid_list, list):
        for rid in rid_list:
            if isinstance(rid, str) and rid:
                record_ids.append(rid)

    # 去重保序
    seen: set[str] = set()
    uniq_ids: list[str] = []
    for rid in record_ids:
        if rid not in seen:
            seen.add(rid)
            uniq_ids.append(rid)

    return app_token, table_id, uniq_ids


@app.route("/", methods=["POST"])
@app.route("/webhook/feishu", methods=["POST"])
@app.route("/feishu/events", methods=["POST"])
def feishu_webhook():
    import json as _json

    body = request.get_json(force=True, silent=True) or {}

    # URL 验证 / challenge（开发指引常见字段名）
    ch = body.get("challenge")
    if ch is not None:
        logger.info("响应飞书 URL 校验 challenge")
        return jsonify({"challenge": ch})

    # 调试用：把飞书发来的事件结构完整打到日志里，便于排查
    # （生产稳定后可删除此日志）
    try:
        logger.info(
            "DEBUG raw body keys=%s body=%s",
            list(body.keys()),
            _json.dumps(body, ensure_ascii=False)[:4000],
        )
    except Exception:
        logger.info("DEBUG raw body keys=%s (json dump 失败)", list(body.keys()))

    header = body.get("header") or {}
    event_type = header.get("event_type") or body.get("type") or ""

    app_token, table_id, record_ids = _extract_records_from_event(body)

    cfg = load_config_module()
    if app_token and cfg.BITABLE_APP_TOKEN and app_token != cfg.BITABLE_APP_TOKEN:
        logger.warning("事件 app_token 与配置不一致，忽略: %s", app_token)
        return jsonify({"code": 0})
    if table_id and cfg.BITABLE_TABLE_ID and table_id != cfg.BITABLE_TABLE_ID:
        logger.warning("事件 table_id 与配置不一致，忽略: %s", table_id)
        return jsonify({"code": 0})

    # 识别支持的事件类型：
    #   - 老版本 bitable.record.created
    #   - v2.0 drive.file.bitable_record_changed_v1
    #   - 兜底：event_type 为空但能解析出 record_id 时也处理
    is_supported_event = (
        "bitable.record.created" in event_type
        or "bitable_record_changed" in event_type
        or "record.created" in event_type
        or event_type == ""
    )

    if not record_ids or not is_supported_event:
        logger.info(
            "收到事件但未触发处理 event_type=%s record_ids=%s", event_type, record_ids
        )
        return jsonify({"code": 0})

    logger.info(
        "收到记录事件 event_type=%s record_ids=%s", event_type, record_ids
    )

    client = FeishuClient(cfg.APP_ID, cfg.APP_SECRET)
    try:
        # 先把整张表拉一次（同一订单号可能有多行），后面按订单号分组
        all_records = client.bitable_iter_records(
            cfg.BITABLE_APP_TOKEN,
            cfg.BITABLE_TABLE_ID,
            view_id=getattr(cfg, "VIEW_ID", None),
        )
        groups = _group_records_by_order(all_records, cfg)

        # 由 record_ids 反查涉及到的所有订单号，去重处理
        processed_orders: set[str] = set()
        for rid in record_ids:
            try:
                rec = client.bitable_get_record(
                    cfg.BITABLE_APP_TOKEN, cfg.BITABLE_TABLE_ID, rid
                )
            except Exception as e:
                logger.warning("读取记录 %s 失败: %s", rid, e)
                continue
            fields = rec.get("fields") or {}
            order_no_raw = feishu_field_to_plain(fields.get(cfg.FIELD_ORDER_NO))
            order_no = str(order_no_raw or "").strip()
            if not order_no:
                logger.warning("记录 %s 未填采购订单号，忽略", rid)
                continue
            if order_no in processed_orders:
                continue
            processed_orders.add(order_no)

            same_order = groups.get(order_no) or [rec]

            # 幂等防御：如果该订单号下没有任何一行处于「待生成/空」状态，
            # 说明上次已经处理完，这次事件多半是我们自己写状态触发的，跳过。
            if not any(_should_process_record(r, cfg) for r in same_order):
                logger.info(
                    "订单号 %s 已全部为「已生成」，跳过（避免回写自触发）",
                    order_no,
                )
                continue

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
