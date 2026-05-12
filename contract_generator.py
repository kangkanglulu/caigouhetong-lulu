# -*- coding: utf-8 -*-
"""基于 docxtpl 将上下文填入 Word 模板并保存；并支持 docx → pdf 转换。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docxtpl import DocxTemplate

logger = logging.getLogger(__name__)

# PDF_ENGINE：auto | word | libreoffice
# - auto：Windows 且已安装 pywin32 时用 Word COM；否则用 LibreOffice（适合阿里云 FC / Linux）
# - word：强制 Word（仅 Windows）
# - libreoffice：强制 LibreOffice（Linux / 容器）


def clean_template_context(ctx: dict) -> dict:
    """移除仅用于校验的内部键，避免写入模板。"""
    return {k: v for k, v in ctx.items() if not str(k).startswith("_")}


def render_contract(
    template_path: Path,
    output_path: Path,
    context: dict,
) -> None:
    """
    使用 Jinja2 语法渲染 Word 模板。
    占位符示例：{{ 甲方 }}、{{ contract_no }}、{% for row in rows %}…{% endfor %}
    """
    template_path = Path(template_path)
    output_path = Path(output_path)
    if not template_path.is_file():
        raise FileNotFoundError(f"模板不存在: {template_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    safe = clean_template_context(context)

    logger.debug("渲染模板键: %s", list(safe.keys()))
    doc = DocxTemplate(str(template_path))
    try:
        doc.render(safe)
    except Exception as e:
        logger.exception("docxtpl 渲染失败，请检查模板占位符是否与字段映射一致")
        raise RuntimeError(
            "模板渲染失败。请确认 Word 模板中的占位符与程序输出的变量名一致（含中英文）。"
        ) from e
    doc.save(str(output_path))
    logger.info("已生成合同文件: %s", output_path)


def safe_filename(order_no: str) -> str:
    """Windows 文件名合法化。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", str(order_no).strip())
    return s or "未命名订单"


def _find_libreoffice_binary() -> str | None:
    """在 PATH 中查找 soffice / libreoffice。"""
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _convert_docx_to_pdf_libreoffice(docx_path: Path, pdf_path: Path | None = None) -> Path:
    """
    使用 LibreOffice headless 将 .docx 转为 .pdf（Linux / 阿里云 FC 推荐）。

    与 Microsoft Word 导出相比，版式、字体、分页可能略有差异（通常约 95% 观感一致）。
    """
    docx_path = Path(docx_path).resolve()
    if not docx_path.is_file():
        raise FileNotFoundError(f"待转换的 docx 不存在: {docx_path}")

    if pdf_path is None:
        pdf_path = docx_path.with_suffix(".pdf")
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError:
            pass

    binary = _find_libreoffice_binary()
    if not binary:
        raise RuntimeError(
            "未找到 LibreOffice（命令 soffice / libreoffice）。"
            "请在系统中安装 LibreOffice，或在 Docker 镜像中安装 libreoffice-writer；"
            "Windows 本机可设置环境变量 PDF_ENGINE=word 使用 Word COM。"
        )

    out_dir = pdf_path.parent
    # LibreOffice 默认输出：out_dir / {docx 主文件名}.pdf
    expected = out_dir / f"{docx_path.stem}.pdf"
    if expected.exists():
        try:
            expected.unlink()
        except OSError:
            pass

    cmd = [
        binary,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]
    logger.debug("执行: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("LIBREOFFICE_TIMEOUT_SEC", "180")),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("LibreOffice 转换超时，请增大 LIBREOFFICE_TIMEOUT_SEC 或检查文档大小。") from e

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"LibreOffice 退出码 {proc.returncode}：{err or '无输出'}")

    if not expected.is_file():
        raise RuntimeError(f"LibreOffice 未生成预期文件：{expected}")

    # 若调用方指定的 pdf 路径与默认名不同，则移动
    if expected.resolve() != pdf_path.resolve():
        shutil.move(str(expected), str(pdf_path))

    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise RuntimeError(f"PDF 输出无效：{pdf_path}")

    logger.info("已生成 PDF（LibreOffice）：%s", pdf_path)
    return pdf_path


# Word SaveAs2 文件格式常量：wdFormatPDF
_WD_FORMAT_PDF = 17


def _convert_docx_to_pdf_word_win32(docx_path: Path, pdf_path: Path | None = None) -> Path:
    """
    将 .docx 转为 .pdf：Windows 通过 win32com 直接驱动 Microsoft Word。

    实现要点：
      - 不依赖 docx2pdf 包；
      - Word.Quit 失败不视为整体失败，以「PDF 文件存在且非空」作为成功判据。
    """
    docx_path = Path(docx_path).resolve()
    if not docx_path.is_file():
        raise FileNotFoundError(f"待转换的 docx 不存在: {docx_path}")
    if pdf_path is None:
        pdf_path = docx_path.with_suffix(".pdf")
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError:
            pass

    try:
        import pythoncom
        from win32com.client import DispatchEx
    except ImportError as e:
        raise RuntimeError(
            "缺少 pywin32 依赖。请执行 `pip install pywin32` 后重试，"
            "或设置环境变量 PDF_ENGINE=libreoffice 使用 LibreOffice。"
        ) from e

    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = DispatchEx("Word.Application")
        try:
            word.Visible = False
        except Exception:
            pass
        try:
            word.DisplayAlerts = 0
        except Exception:
            pass

        doc = word.Documents.Open(str(docx_path), ReadOnly=True)
        try:
            doc.ExportAsFixedFormat(
                OutputFileName=str(pdf_path),
                ExportFormat=_WD_FORMAT_PDF,
                OpenAfterExport=False,
                OptimizeFor=0,
                CreateBookmarks=0,
            )
        except Exception:
            doc.SaveAs2(str(pdf_path), FileFormat=_WD_FORMAT_PDF)
    except Exception as e:
        logger.exception("docx → pdf（Word）转换失败：%s", e)
        raise RuntimeError(
            "docx → pdf 转换失败。请确认本机已安装 Microsoft Word，"
            "或设置 PDF_ENGINE=libreoffice 使用 LibreOffice。"
        ) from e
    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=0)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if not pdf_path.is_file():
        raise RuntimeError(f"PDF 转换完成但未找到输出文件：{pdf_path}")
    if pdf_path.stat().st_size <= 0:
        raise RuntimeError(f"PDF 输出为空：{pdf_path}")

    logger.info("已生成 PDF（Word）：%s", pdf_path)
    return pdf_path


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path | None = None) -> Path:
    """
    将 .docx 转为 .pdf。

    引擎由环境变量 ``PDF_ENGINE`` 控制：

    - ``auto``（默认）：Windows 且能导入 pywin32 时用 Word；否则用 LibreOffice。
    - ``word``：强制 Word（仅 Windows）。
    - ``libreoffice``：强制 LibreOffice（阿里云 FC / Linux 容器请用此项或 auto）。
    """
    engine = (os.environ.get("PDF_ENGINE") or "auto").strip().lower()
    if engine == "libreoffice":
        return _convert_docx_to_pdf_libreoffice(docx_path, pdf_path)
    if engine == "word":
        if sys.platform != "win32":
            raise RuntimeError("PDF_ENGINE=word 仅支持 Windows；Linux 请使用 libreoffice。")
        return _convert_docx_to_pdf_word_win32(docx_path, pdf_path)

    # auto
    if sys.platform == "win32":
        try:
            import pythoncom  # noqa: F401
            from win32com.client import DispatchEx  # noqa: F401
        except ImportError:
            logger.warning("Windows 下未安装 pywin32，自动改用 LibreOffice")
            return _convert_docx_to_pdf_libreoffice(docx_path, pdf_path)
        return _convert_docx_to_pdf_word_win32(docx_path, pdf_path)

    return _convert_docx_to_pdf_libreoffice(docx_path, pdf_path)
