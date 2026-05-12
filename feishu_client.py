# -*- coding: utf-8 -*-
"""飞书开放平台 API 封装：令牌缓存、自动刷新、网络重试、多维表格与 IM 文件消息。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

FEISHU_ORIGIN = "https://open.feishu.cn/open-apis"


def _build_session() -> requests.Session:
    """配置连接池与重试策略（网络抖动、短暂 429）。"""
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "PATCH", "PUT", "DELETE"),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def feishu_field_to_plain(value: Any) -> Any:
    """将多维表格字段值转为 Python 基本类型，便于填入 Word / 日志。"""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            if "text" in first or "type" in first:
                # 多行文本 / 文本片段
                parts: list[str] = []
                for seg in value:
                    if isinstance(seg, dict) and "text" in seg:
                        parts.append(str(seg.get("text", "")))
                return "".join(parts)
            if "name" in first:
                return ", ".join(str(x.get("name", "")) for x in value if isinstance(x, dict))
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        if "value" in value:
            return feishu_field_to_plain(value.get("value"))
        if "text" in value:
            return feishu_field_to_plain(value.get("text"))
        if "link" in value:
            return str(value.get("link") or "")
    return str(value)


class FeishuClient:
    """
    租户级调用（适用于自建应用后台机器人能力）。
    Token 默认有效期约 2 小时；在过期前 5 分钟自动刷新。
    """

    def __init__(self, app_id: str, app_secret: str, session: requests.Session | None = None):
        self._app_id = app_id
        self._app_secret = app_secret
        self._session = session or _build_session()
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    def _refresh_token(self) -> str:
        url = f"{FEISHU_ORIGIN}/auth/v3/tenant_access_token/internal"
        r = self._session.post(
            url,
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        # 官方返回 expire 秒数；若无则按 7200
        exp = int(data.get("expire") or 7200)
        self._token_expire_at = time.time() + max(60, exp - 300)
        logger.info("已刷新 tenant_access_token，将在 %s 前保持有效", int(exp))
        return self._token

    def tenant_access_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token  # type: ignore
        return self._refresh_token()

    def _headers_json(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """统一请求；若 token 失效（99991663）则强制刷新后重试一次。"""
        url = FEISHU_ORIGIN + path if path.startswith("/") else f"{FEISHU_ORIGIN}/{path}"
        for attempt in range(2):
            r = self._session.request(
                method,
                url,
                headers=self._headers_json(),
                params=params,
                json=json_body,
                timeout=90,
            )
            try:
                data = r.json()
            except Exception:
                r.raise_for_status()
                raise
            code = data.get("code")
            if code == 99991663 and attempt == 0:
                logger.warning("tenant_access_token 失效，正在刷新后重试…")
                self._token = None
                self._token_expire_at = 0
                continue
            if code != 0:
                raise RuntimeError(f"飞书 API 错误: {data}")
            inner = data.get("data")
            # 规范接口均返回 data；若无 data 键则回退空字典
            return inner if inner is not None else {}
        raise RuntimeError("重试后仍失败")

    # ----- 多维表格 -----

    def bitable_get_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> dict[str, Any]:
        path = (
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        )
        data = self.request_json("GET", path)
        return data.get("record") or {}

    def bitable_list_records_page(
        self,
        app_token: str,
        table_id: str,
        *,
        view_id: str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        params: dict[str, Any] = {"page_size": page_size}
        if view_id:
            params["view_id"] = view_id
        if page_token:
            params["page_token"] = page_token
        data = self.request_json("GET", path, params=params)
        return data or {}

    def bitable_iter_records(
        self,
        app_token: str,
        table_id: str,
        *,
        view_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """分页拉取全部记录。"""
        out: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            batch = self.bitable_list_records_page(
                app_token,
                table_id,
                view_id=view_id,
                page_token=page_token,
            )
            items = batch.get("items") or []
            out.extend(items)
            if not batch.get("has_more"):
                break
            page_token = batch.get("page_token")
            if not page_token:
                break
        return out

    def bitable_patch_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> None:
        """
        增量更新一条记录。飞书开放平台「更新记录」接口使用 HTTP PUT（非 PATCH），
        误用 PATCH 会返回 HTTP 404。
        文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update
        """
        path = (
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        )
        self.request_json("PUT", path, json_body={"fields": fields})

    # ----- IM：上传文件并发消息 -----

    def im_upload_file(self, file_path: str, file_name: str, file_type: str = "doc") -> str:
        """
        上传本地文件到 IM，返回 file_key。
        file_type 与开放平台文档一致：pdf/doc/xls/ppt/stream 等。
        Word 合同使用 doc。
        """
        url = f"{FEISHU_ORIGIN}/im/v1/files"
        headers = {"Authorization": f"Bearer {self.tenant_access_token()}"}
        with open(file_path, "rb") as fp:
            files = {"file": (file_name, fp, "application/octet-stream")}
            data = {"file_type": file_type, "file_name": file_name}
            r = self._session.post(url, headers=headers, data=data, files=files, timeout=120)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"im/v1/files 上传失败: {body}")
        fk = (body.get("data") or {}).get("file_key")
        if not fk:
            raise RuntimeError(f"im/v1/files 未返回 file_key: {body}")
        return str(fk)

    def im_send_file_message(self, receive_id: str, file_key: str, receive_id_type: str = "open_id") -> None:
        """向指定成员发送文件消息。"""
        path = "/im/v1/messages"
        params = {"receive_id_type": receive_id_type}
        payload = {
            "receive_id": receive_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        url = f"{FEISHU_ORIGIN}{path}"
        r = self._session.post(
            url,
            headers=self._headers_json(),
            params=params,
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"发送 IM 文件消息失败: {data}")

    # ----- 云空间上传（可选） -----

    def drive_upload_all(self, file_path: str, file_name: str, parent_node: str) -> dict[str, Any]:
        """
        上传文件到云空间指定文件夹；parent_node 为文件夹 token。
        返回 data 字段（含 file_token 等，视权限而定）。
        """
        url = f"{FEISHU_ORIGIN}/drive/v1/files/upload_all"
        size = __import__("os").path.getsize(file_path)
        headers = {"Authorization": f"Bearer {self.tenant_access_token()}"}
        with open(file_path, "rb") as fp:
            files = {"file": (file_name, fp, "application/octet-stream")}
            data = {
                "file_name": file_name,
                "parent_type": "explorer",
                "parent_node": parent_node,
                "size": str(size),
            }
            r = self._session.post(url, headers=headers, data=data, files=files, timeout=180)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"云空间上传失败: {body}")
        return body.get("data") or {}

    def drive_file_meta(self, file_token: str) -> dict[str, Any]:
        """获取文件元数据（含可分享的 URL 字段时可用于回写表格）。"""
        path = f"/drive/v1/files/{file_token}/meta"
        data = self.request_json("GET", path)
        return data.get("file") or data.get("data") or {}


def load_config_module():
    """加载用户配置 config.py。"""
    try:
        import config as user_config  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "未找到 config.py：请复制 config.example.py 为 config.py，或设置环境变量。"
        ) from e
    return user_config
