# -*- coding: utf-8 -*-
"""
飞书接入连通性自检脚本

用法：
    python diagnostic.py            # 全量检查
    python diagnostic.py --no-im    # 跳过 IM 发送测试

依次验证：
  1) 加载 .env / config.py，检查必填项
  2) 获取 tenant_access_token（验证 APP_ID / APP_SECRET）
  3) 读取多维表格记录（验证 BITABLE_APP_TOKEN / TABLE_ID + 权限 + 协作者）
  4) 向接收人发送一条文字消息（验证 RECEIVER_OPEN_IDS + IM 权限）
  5) 上传一个临时文件并发文件消息（验证 IM 文件上传权限）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# 让 .env 自动加载
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    print("[警告] 未安装 python-dotenv，将仅读取系统环境变量。建议先 pip install -r requirements.txt")


def step(idx: int, title: str) -> None:
    print(f"\n========== [步骤 {idx}] {title} ==========")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-im", action="store_true", help="跳过 IM 消息测试")
    parser.add_argument("--no-file", action="store_true", help="跳过 IM 文件测试")
    args = parser.parse_args()

    step(1, "加载配置")
    try:
        import config  # noqa: F401
    except ImportError:
        fail("未找到 config.py。请先执行：copy config.example.py config.py")
        return 2

    required = {
        "APP_ID": config.APP_ID,
        "APP_SECRET": config.APP_SECRET,
        "BITABLE_APP_TOKEN": config.BITABLE_APP_TOKEN,
        "BITABLE_TABLE_ID": config.BITABLE_TABLE_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        fail(f"以下配置缺失：{', '.join(missing)}（请检查 .env）")
        return 2
    ok("配置加载完成")
    info(f"APP_ID={config.APP_ID}")
    info(f"BITABLE_APP_TOKEN={config.BITABLE_APP_TOKEN}")
    info(f"BITABLE_TABLE_ID={config.BITABLE_TABLE_ID}")
    info(f"RECEIVER_OPEN_IDS={config.RECEIVER_OPEN_IDS}")

    from feishu_client import FeishuClient

    client = FeishuClient(config.APP_ID, config.APP_SECRET)

    step(2, "获取 tenant_access_token")
    try:
        token = client.tenant_access_token()
        ok(f"获取成功（前 12 位：{token[:12]}…，长度 {len(token)}）")
    except Exception as e:
        fail(f"获取失败：{e}")
        traceback.print_exc()
        print("\n排查思路：")
        print("  - APP_ID / APP_SECRET 是否拼写正确（控制台『凭证与基础信息』）")
        print("  - 应用是否已发布（创建版本 → 发布上线）")
        return 3

    step(3, "读取多维表格记录")
    try:
        page = client.bitable_list_records_page(
            config.BITABLE_APP_TOKEN,
            config.BITABLE_TABLE_ID,
            view_id=config.VIEW_ID,
            page_size=5,
        )
        items = page.get("items") or []
        total = page.get("total") if "total" in page else len(items)
        ok(f"读取成功，共拿到 {len(items)} 行（接口 total={total}）")
        if items:
            sample = items[0]
            info(f"首行 record_id：{sample.get('record_id')}")
            fields = sample.get("fields") or {}
            info(f"首行字段名：{list(fields.keys())}")

            expected_cols = [
                config.FIELD_PURCHASE_COMPANY,
                config.FIELD_SUPPLIER,
                config.FIELD_ORDER_NO,
                config.FIELD_ORDER_CREATED_AT,
                config.FIELD_LINE_NO,
                config.FIELD_MATERIAL_CODE,
                config.FIELD_MATERIAL_NAME,
                config.FIELD_DELIVERY_DATE,
                config.FIELD_QTY,
                config.FIELD_AMOUNT,
                config.FIELD_CONTRACT_STATUS,
            ]
            missing_cols = [c for c in expected_cols if c not in fields]
            if missing_cols:
                info(f"以下『期望列』在首行未出现（可能是该行未填值，或列名不一致）：{missing_cols}")
                info("如果列名不一致，请修改 config.py 中的 FIELD_* 配置。")
            else:
                ok("所有期望字段都在首行字段中找到")
        else:
            info("表格暂无数据；可在表格里先添加一行测试数据。")
    except Exception as e:
        fail(f"读取失败：{e}")
        traceback.print_exc()
        print("\n排查思路：")
        print("  - BITABLE_APP_TOKEN / TABLE_ID 是否复制完整、无多余字符")
        print("  - 应用是否申请并发布了多维表格『查看记录』权限")
        print("  - 是否在多维表格『…更多 → 添加文档应用』把自建应用加入协作者")
        return 4

    if args.no_im:
        print("\n[跳过] IM 消息测试")
        return 0

    if not config.RECEIVER_OPEN_IDS:
        info("未配置 RECEIVER_OPEN_IDS，跳过 IM 测试")
        return 0

    receive_id = config.RECEIVER_OPEN_IDS[0]

    step(4, f"向 {receive_id} 发送测试文字消息")
    try:
        # 直接用 client 内部 session 发文字消息（最小化依赖）
        import requests

        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {client.tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"receive_id_type": "open_id"}
        body = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps(
                {"text": "[采购合同自检] 飞书接入连通性测试 ✓ 收到此消息说明 IM 权限正常。"},
                ensure_ascii=False,
            ),
        }
        r = requests.post(url, headers=headers, params=params, json=body, timeout=30)
        data = r.json()
        if data.get("code") == 0:
            ok("文字消息已发送，请在飞书中查收")
        else:
            fail(f"发送失败：{data}")
            print("\n排查思路：")
            print("  - 应用权限是否包含 im:message:send_as_bot（发送消息）")
            print("  - 接收人 open_id 是否正确、是否与本应用同租户")
            print("  - 应用『可用范围』是否包含该接收人或全员")
            return 5
    except Exception as e:
        fail(f"发送异常：{e}")
        traceback.print_exc()
        return 5

    if args.no_file:
        print("\n[跳过] IM 文件上传测试")
        return 0

    step(5, "上传临时文件并发送文件消息")
    try:
        from docx import Document

        with tempfile.NamedTemporaryFile(
            suffix=".docx", delete=False, dir=str(Path(__file__).resolve().parent)
        ) as tmp:
            tmp_path = tmp.name
        d = Document()
        d.add_heading("飞书接入自检测试文件", 0)
        d.add_paragraph("如果你能在飞书中收到此文件，说明 IM 文件上传与发送权限正常。")
        d.save(tmp_path)
        info(f"已生成测试文件：{tmp_path}")

        file_key = client.im_upload_file(tmp_path, "采购合同自检测试.docx", file_type="docx")
        ok(f"文件上传成功，file_key={file_key}")

        client.im_send_file_message(receive_id, file_key, receive_id_type="open_id")
        ok("文件消息已发送，请在飞书查收。")
    except Exception as e:
        fail(f"文件流程失败：{e}")
        traceback.print_exc()
        print("\n排查思路：")
        print("  - 应用权限是否包含 im:resource（上传图片/文件）")
        print("  - 部分租户对 file_type 校验严格，可尝试改为 'stream'")
        return 6
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    print("\n=========================================")
    print("  全部检查通过，可以执行 `python main.py --once` 处理一行真实数据。")
    print("=========================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
