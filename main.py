# -*- coding: utf-8 -*-
"""
主入口：轮询「待生成」记录 → 生成 Word 合同 →（可选）上传云盘 → IM 发送 → 回写状态。

事件订阅模式请使用：python webhook_app.py（或 gunicorn 部署）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from contract_generator import convert_docx_to_pdf, render_contract, safe_filename
from feishu_client import FeishuClient, feishu_field_to_plain, load_config_module
from field_mapping import _format_amount_two_decimals, record_to_contract_context, validate_context

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_state(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("processed_record_ids") or []
        return set(str(x) for x in ids)
    except Exception as e:
        logger.warning("读取状态文件失败，将重新开始幂等集: %s", e)
        return set()


def _save_state(path: Path, ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"processed_record_ids": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_single_select(value) -> str | None:
    """从飞书单选/文本字段中取出展示文案。"""
    if value is None:
        return None
    plain = feishu_field_to_plain(value)
    if plain == "" or plain is None:
        return None
    return str(plain).strip()


def _should_process_record(record: dict, cfg) -> bool:
    """
    处理条件：合同状态为「待生成」或为空；且未被本地 state 标记为已处理。
    若你的表格用其它字段表示「待生成」，请修改此函数。
    """
    fields = record.get("fields") or {}
    st_col = cfg.FIELD_CONTRACT_STATUS
    status_val = _parse_single_select(fields.get(st_col))
    if status_val is None or status_val == "":
        return True
    if status_val == cfg.STATUS_PENDING:
        return True
    return False


def process_one_record(
    client: FeishuClient,
    cfg,
    record_id: str,
    *,
    skip_idempotent: bool = False,
    processed_ids: set[str] | None = None,
) -> bool:
    """
    处理单条记录。成功返回 True；跳过或失败返回 False（失败时已尽力回写「生成失败」）。
    """
    if processed_ids is not None and record_id in processed_ids and skip_idempotent:
        logger.info("跳过已处理 record_id=%s", record_id)
        return False

    try:
        rec = client.bitable_get_record(cfg.BITABLE_APP_TOKEN, cfg.BITABLE_TABLE_ID, record_id)
    except Exception as e:
        logger.error("读取记录失败 record_id=%s: %s", record_id, e)
        return False

    fields = rec.get("fields") or {}
    # 幂等：表格已标记「已生成」则跳过（Webhook 可能重复投递）
    existing_status = _parse_single_select(fields.get(cfg.FIELD_CONTRACT_STATUS))
    if existing_status == cfg.STATUS_DONE:
        logger.info("record_id=%s 已是「已生成」，跳过", record_id)
        return False

    ctx = record_to_contract_context(fields, field_config=cfg)
    missing = validate_context(ctx)
    if missing:
        logger.error(
            "记录 %s 缺少必填字段（列名）: %s — 请补全表格或调整 config.FIELD_* 映射",
            record_id,
            "、".join(missing),
        )
        try:
            client.bitable_patch_record(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                record_id,
                {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
            )
        except Exception as e:
            logger.warning("回写「生成失败」状态失败: %s", e)
        return False

    order_no = ctx.get("合同号码") or ctx.get("contract_no") or record_id
    out_name = f"合同_{safe_filename(order_no)}.docx"
    out_path = Path(cfg.OUTPUT_DIR) / out_name

    try:
        render_contract(Path(cfg.TEMPLATE_PATH), out_path, ctx)
    except Exception as e:
        logger.exception("生成合同失败 record_id=%s", record_id)
        try:
            client.bitable_patch_record(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                record_id,
                {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
            )
        except Exception as ex:
            logger.warning("回写失败状态异常: %s", ex)
        return False

    file_key = None
    try:
        file_key = client.im_upload_file(str(out_path), out_name, file_type="doc")
    except Exception as e:
        logger.error("IM 上传文件失败（请检查 im:resource 等权限）: %s", e)

    if cfg.SEND_IM_MESSAGE and cfg.RECEIVER_OPEN_IDS and not file_key:
        logger.error("已配置发送 IM 但文件未上传成功，将标记为生成失败")
        try:
            client.bitable_patch_record(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                record_id,
                {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
            )
        except Exception as e:
            logger.warning("回写失败状态异常: %s", e)
        return False

    if cfg.SEND_IM_MESSAGE and file_key:
        for oid in cfg.RECEIVER_OPEN_IDS:
            try:
                client.im_send_file_message(oid, file_key)
                logger.info("已向 open_id=%s 发送合同文件消息", oid)
            except Exception as e:
                logger.error("向 %s 发送消息失败: %s", oid, e)

    link_url = ""
    if getattr(cfg, "UPLOAD_TO_DRIVE", False) and cfg.DRIVE_FOLDER_TOKEN:
        try:
            up = client.drive_upload_all(str(out_path), out_name, cfg.DRIVE_FOLDER_TOKEN)
            token = up.get("file_token")
            if token:
                meta = client.drive_file_meta(token)
                link_url = str(meta.get("url") or meta.get("url_preview") or "")
        except Exception as e:
            logger.warning("云空间上传或取链接失败（可仅使用 IM 交付）: %s", e)

    # 回写表格：状态 + 可选链接
    patch: dict = {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_DONE}
    if link_url and getattr(cfg, "FIELD_CONTRACT_LINK", ""):
        patch[cfg.FIELD_CONTRACT_LINK] = link_url

    try:
        client.bitable_patch_record(
            cfg.BITABLE_APP_TOKEN,
            cfg.BITABLE_TABLE_ID,
            record_id,
            patch,
        )
    except Exception as e:
        logger.error("回写表格失败 record_id=%s: %s", record_id, e)
        return False

    if processed_ids is not None:
        processed_ids.add(record_id)
    logger.info("已完成 record_id=%s -> %s", record_id, out_path)
    return True


def _line_no_sort_key(record: dict, cfg) -> tuple:
    """按采购行号排序（数字优先，非数字回退到字符串）。"""
    fields = record.get("fields") or {}
    raw = feishu_field_to_plain(fields.get(cfg.FIELD_LINE_NO))
    try:
        return (0, int(str(raw)))
    except (TypeError, ValueError):
        try:
            return (1, float(str(raw)))
        except (TypeError, ValueError):
            return (2, str(raw or ""))


def _amount_to_float(value) -> float:
    """金额字段转 float（容错：剥掉逗号、空白）。"""
    if value is None:
        return 0.0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def process_one_order(
    client: FeishuClient,
    cfg,
    order_no: str,
    records: list[dict],
    *,
    processed_ids: set[str] | None = None,
) -> bool:
    """
    将同一采购订单号下的多条记录合并生成一份合同。

    - 表头（甲方 / 乙方 / 合同号码 / 签约日期）来自第一条记录；
    - 商品明细 items 为该订单号下所有行（按采购行号升序排序）；
    - 合同金额为该订单号下所有行金额之和；
    - 处理完成后：上传 IM → 把该订单号下「全部行」回写为「已生成」。
    """
    if not records:
        return False

    records = sorted(records, key=lambda r: _line_no_sort_key(r, cfg))
    head_fields = records[0].get("fields") or {}

    ctx = record_to_contract_context(head_fields, field_config=cfg)
    missing = validate_context(ctx)
    if missing:
        logger.error(
            "订单号 %s 缺少必填字段：%s — 请补全表格或检查 FIELD_* 映射",
            order_no,
            "、".join(missing),
        )
        for rec in records:
            rid = rec.get("record_id")
            if not rid:
                continue
            try:
                client.bitable_patch_record(
                    cfg.BITABLE_APP_TOKEN,
                    cfg.BITABLE_TABLE_ID,
                    rid,
                    {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
                )
            except Exception as e:
                logger.warning("回写「生成失败」状态失败 record_id=%s: %s", rid, e)
        return False

    items: list[dict] = []
    total = 0.0
    for rec in records:
        fields = rec.get("fields") or {}
        sub_ctx = record_to_contract_context(fields, field_config=cfg)
        item = (sub_ctx.get("items") or [{}])[0]
        item["金额"] = _format_amount_two_decimals(fields.get(cfg.FIELD_AMOUNT))
        items.append(item)
        total += _amount_to_float(item.get("金额"))

    ctx["items"] = items
    ctx["合同金额"] = f"{total:.2f}"
    ctx["total_amount"] = ctx["合同金额"]

    base_name = f"合同_{safe_filename(order_no)}"
    docx_path = Path(cfg.OUTPUT_DIR) / f"{base_name}.docx"

    try:
        render_contract(Path(cfg.TEMPLATE_PATH), docx_path, ctx)
    except Exception:
        logger.exception("生成合同失败 order_no=%s", order_no)
        for rec in records:
            rid = rec.get("record_id")
            if not rid:
                continue
            try:
                client.bitable_patch_record(
                    cfg.BITABLE_APP_TOKEN,
                    cfg.BITABLE_TABLE_ID,
                    rid,
                    {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
                )
            except Exception as e:
                logger.warning("回写失败状态异常 record_id=%s: %s", rid, e)
        return False

    # 根据交付格式决定上传哪个文件（默认 pdf）
    delivery_format = str(getattr(cfg, "DELIVERY_FORMAT", "pdf")).lower()
    upload_path = docx_path
    upload_name = f"{base_name}.docx"
    upload_type = "docx"

    if delivery_format == "pdf":
        try:
            pdf_path = Path(cfg.OUTPUT_DIR) / f"{base_name}.pdf"
            convert_docx_to_pdf(docx_path, pdf_path)
            upload_path = pdf_path
            upload_name = f"{base_name}.pdf"
            upload_type = "pdf"
        except Exception:
            logger.exception("PDF 转换失败 order_no=%s，标记该订单号下全部行为生成失败", order_no)
            for rec in records:
                rid = rec.get("record_id")
                if not rid:
                    continue
                try:
                    client.bitable_patch_record(
                        cfg.BITABLE_APP_TOKEN,
                        cfg.BITABLE_TABLE_ID,
                        rid,
                        {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
                    )
                except Exception as e:
                    logger.warning("回写失败状态异常 record_id=%s: %s", rid, e)
            return False

    file_key = None
    try:
        file_key = client.im_upload_file(str(upload_path), upload_name, file_type=upload_type)
    except Exception as e:
        logger.error("IM 上传文件失败（请检查 im:resource 权限）: %s", e)

    if cfg.SEND_IM_MESSAGE and cfg.RECEIVER_OPEN_IDS and not file_key:
        logger.error("已配置发送 IM 但文件未上传成功，将该订单号下全部行标记为生成失败")
        for rec in records:
            rid = rec.get("record_id")
            if not rid:
                continue
            try:
                client.bitable_patch_record(
                    cfg.BITABLE_APP_TOKEN,
                    cfg.BITABLE_TABLE_ID,
                    rid,
                    {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_FAILED},
                )
            except Exception as e:
                logger.warning("回写失败状态异常 record_id=%s: %s", rid, e)
        return False

    if cfg.SEND_IM_MESSAGE and file_key:
        for oid in cfg.RECEIVER_OPEN_IDS:
            try:
                client.im_send_file_message(oid, file_key)
                logger.info(
                    "已向 open_id=%s 发送合同文件 order_no=%s（共 %d 行）",
                    oid,
                    order_no,
                    len(items),
                )
            except Exception as e:
                logger.error("向 %s 发送消息失败: %s", oid, e)

    link_url = ""
    if getattr(cfg, "UPLOAD_TO_DRIVE", False) and cfg.DRIVE_FOLDER_TOKEN:
        try:
            up = client.drive_upload_all(str(upload_path), upload_name, cfg.DRIVE_FOLDER_TOKEN)
            token = up.get("file_token")
            if token:
                meta = client.drive_file_meta(token)
                link_url = str(meta.get("url") or meta.get("url_preview") or "")
        except Exception as e:
            logger.warning("云空间上传或取链接失败（可仅使用 IM 交付）: %s", e)

    patch: dict = {cfg.FIELD_CONTRACT_STATUS: cfg.STATUS_DONE}
    if link_url and getattr(cfg, "FIELD_CONTRACT_LINK", ""):
        patch[cfg.FIELD_CONTRACT_LINK] = link_url

    success = True
    for rec in records:
        rid = rec.get("record_id")
        if not rid:
            continue
        try:
            client.bitable_patch_record(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                rid,
                patch,
            )
            if processed_ids is not None:
                processed_ids.add(rid)
        except Exception as e:
            logger.error("回写表格失败 record_id=%s: %s", rid, e)
            success = False

    if success:
        logger.info(
            "已完成 order_no=%s（合并 %d 行，合同金额 %.2f） -> %s",
            order_no,
            len(items),
            total,
            upload_path,
        )
    return success


def _group_records_by_order(records: list[dict], cfg) -> dict[str, list[dict]]:
    """把记录按采购订单号分组（订单号为空的记录被忽略）。"""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        fields = rec.get("fields") or {}
        order_no_raw = feishu_field_to_plain(fields.get(cfg.FIELD_ORDER_NO))
        order_no = str(order_no_raw or "").strip()
        if not order_no:
            continue
        groups.setdefault(order_no, []).append(rec)
    return groups


def run_poll_loop(cfg, once: bool = False) -> None:
    """
    轮询模式：按采购订单号分组生成合同。

    - 同一订单号下任意一行出现「待生成」或空状态，即触发该订单号的合同生成；
    - 合同 items 始终汇总该订单号下的「全部行」（含已经标记「已生成」的行），
      以便补充行后整份合同保持完整；
    - 合同生成成功后，会把该订单号下所有行回写为「已生成」。
    """
    client = FeishuClient(cfg.APP_ID, cfg.APP_SECRET)
    state_path = Path(cfg.STATE_FILE)
    processed = _load_state(state_path)

    while True:
        try:
            records = client.bitable_iter_records(
                cfg.BITABLE_APP_TOKEN,
                cfg.BITABLE_TABLE_ID,
                view_id=getattr(cfg, "VIEW_ID", None),
            )
        except Exception as e:
            logger.error("拉取表格记录失败: %s", e)
            if once:
                break
            time.sleep(cfg.POLL_INTERVAL_SEC)
            continue

        groups = _group_records_by_order(records, cfg)
        n_orders = 0
        for order_no, recs in groups.items():
            need = any(_should_process_record(r, cfg) for r in recs)
            if not need:
                continue
            if process_one_order(client, cfg, order_no, recs, processed_ids=processed):
                n_orders += 1
                _save_state(state_path, processed)

        logger.info(
            "本轮扫描完成：共 %d 个订单，处理 %d 个待生成订单",
            len(groups),
            n_orders,
        )
        if once:
            break
        time.sleep(cfg.POLL_INTERVAL_SEC)


def main() -> None:
    parser = argparse.ArgumentParser(description="购销合同自动生成（飞书多维表格 + docxtpl）")
    parser.add_argument("--once", action="store_true", help="只执行一轮扫描后退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()
    _setup_logging(args.verbose)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    cfg = load_config_module()
    if not cfg.APP_ID or not cfg.APP_SECRET:
        logger.error("请在 config.py 或环境变量中配置 APP_ID / APP_SECRET")
        sys.exit(1)
    if not cfg.BITABLE_APP_TOKEN or not cfg.BITABLE_TABLE_ID:
        logger.error("请配置 BITABLE_APP_TOKEN / BITABLE_TABLE_ID")
        sys.exit(1)

    run_poll_loop(cfg, once=args.once)


if __name__ == "__main__":
    main()
