#!/usr/bin/env python3
"""PySide6 UI for Codex Switcher (UI-only refactor)."""

from __future__ import annotations

import sys
import json
import base64
import os
import subprocess
import re
import shutil
import threading
import ctypes
import time
import html
from ctypes import wintypes
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib import request as urllib_request
from urllib import error as urllib_error
from urllib.parse import quote as urlquote, urlparse

from PySide6 import QtCore, QtGui, QtWidgets


def resolve_asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


try:
    from qt_material import apply_stylesheet  # type: ignore
except Exception:
    apply_stylesheet = None

from codex_switcher import (
    build_accounts,
    check_codex_available,
    delete_account,
    extract_host,
    find_codex_exe,
    get_active_account,
    load_store,
    log_exception,
    ping_average,
    apply_account_config,
    apply_env_for_account,
    http_head_average,
    LOG_PATH,
    post_json,
    save_store,
    set_active_account,
    upsert_account,
)


APP_TITLE = "Codex Switcher"
APP_VERSION = "2.0.7"
APP_REPO = "nkosi-fang/CodexSwitcher"

CODING_COMPONENTS = [
    "Codex",
    "Responses",
    "Chat Completions",
    "Embeddings",
    "Files",
    "File uploads",
    "Batch",
    "Fine-tuning",
    "Moderations",
    "Realtime",
    "Search",
    "Agent",
]

CODING_COMPONENT_HINTS = {
    "Codex": "Codex 服务本身，异常时 CLI 可能整体不可用",
    "Responses": "主流生成/推理接口，影响代码生成请求",
    "Chat Completions": "旧版聊天接口，部分配置仍依赖",
    "Embeddings": "向量检索/代码库搜索能力",
    "Files": "文件管理与引用",
    "File uploads": "文件上传通道",
    "Batch": "批处理异步任务",
    "Fine-tuning": "微调训练",
    "Moderations": "安全审核，异常可能导致请求阻塞",
    "Realtime": "实时流式/低延迟交互",
    "Search": "内置搜索/检索工具",
    "Agent": "代理式编排（多步工具/任务）",
}

STATUS_TEXT = {
    "operational": "正常",
    "degraded_performance": "性能下降",
    "partial_outage": "部分中断",
    "major_outage": "严重故障",
    "under_maintenance": "维护中",
    "unknown": "未知",
}


def run_in_ui(fn) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        fn()
        return
    QtCore.QTimer.singleShot(0, app, fn)


def message_info(parent: QtWidgets.QWidget, title: str, text: str) -> None:
    QtWidgets.QMessageBox.information(parent, title, text)


def message_warn(parent: QtWidgets.QWidget, title: str, text: str) -> None:
    QtWidgets.QMessageBox.warning(parent, title, text)


def apply_white_shadow(widget: QtWidgets.QWidget) -> None:
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(12)
    effect.setColor(QtGui.QColor(255, 255, 255, 180))
    effect.setOffset(0, 0)
    widget.setGraphicsEffect(effect)


def message_error(parent: QtWidgets.QWidget, title: str, text: str) -> None:
    QtWidgets.QMessageBox.critical(parent, title, text)


class NavBadgeButton(QtWidgets.QPushButton):
    def __init__(self, label: str) -> None:
        super().__init__(label)
        self._badge = QtWidgets.QLabel("", self)
        self._badge.setAlignment(QtCore.Qt.AlignCenter)
        self._badge.setStyleSheet(
            "background:#ff4d4f;color:white;border-radius:8px;font-size:10px;font-weight:700;"
        )
        self._badge.setFixedSize(16, 16)
        self._badge.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_badge()

    def _position_badge(self) -> None:
        w = self._badge.width()
        self._badge.move(max(0, self.width() - w - 4), 2)

    def set_badge_count(self, count: int) -> None:
        if count <= 0:
            self._badge.hide()
            return
        if count > 99:
            text = "99+"
        else:
            text = str(count)
        width = 16 if len(text) == 1 else 22
        self._badge.setFixedSize(width, 16)
        self._badge.setText(text)
        self._badge.show()
        self._position_badge()


def _popen_hidden_cmd_on_windows(args: List[str]):
    popen_kwargs: Dict[str, object] = {}
    if os.name == "nt" and args:
        executable = str(args[0]).lower()
        if executable.endswith(".cmd") or executable.endswith(".bat"):
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                popen_kwargs["startupinfo"] = startupinfo
            except Exception:
                pass
    return subprocess.Popen(args, **popen_kwargs)


def log_diagnosis(title: str, detail: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] {title}\n")
            fh.write(detail)
            fh.write("\n\n")
    except Exception:
        return


def probe_endpoints(
    base: str,
    api_key: str,
    org_id: str,
    model: str,
    timeout: int = 60,
) -> Dict[str, object]:
    base = base.strip().rstrip("/")
    base_host = extract_host(base)
    if not base_host:
        raise ValueError("Base URL 无效，无法解析主机")

    def fmt_ms(value: object) -> str:
        if isinstance(value, (int, float)):
            return f"{value:.0f}ms"
        return "不可用"

    ping_avg, _loss = ping_average(base_host, 1)
    http_avg = None
    try:
        http_avg = http_head_average(f"{base}/models", api_key, 1)
    except Exception:
        http_avg = None

    port_ms = None
    port_ok: Optional[bool] = None
    try:
        import socket

        start = time.perf_counter()
        with socket.create_connection((base_host, 443), timeout=3):
            port_ms = (time.perf_counter() - start) * 1000
            port_ok = True
    except Exception:
        port_ok = False

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }
    if org_id:
        headers["OpenAI-Organization"] = org_id

    def get_json(url: str) -> tuple[bool, str]:
        req = urllib_request.Request(url, headers=headers, method="GET")
        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return True, body
        except urllib_error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            return False, f"HTTP {exc.code}: {body or exc.reason}"
        except Exception as exc:
            return False, str(exc)

    embedding_model = "text-embedding-3-small"
    moderation_model = "omni-moderation-latest"
    skip_endpoints = {
        "/realtime": "实时语音/文本会话（WebSocket 连接）",
        "/assistants": "Assistants 工作流（需线程/工具配置）",
        "/batch": "批处理任务（需上传文件）",
        "/fine-tuning": "模型微调（需训练配置/文件）",
        "/images/generations": "图像生成（需图像参数）",
        "/images/edits": "图像编辑（需图像文件）",
        "/videos": "视频生成（需视频参数）",
        "/audio/speech": "语音合成（需音频参数）",
        "/audio/transcriptions": "语音转写（需音频文件）",
        "/audio/translations": "语音翻译（需音频文件）",
    }

    def request_endpoint(endpoint: str, url: str) -> tuple[bool, str]:
        if endpoint == "/models":
            return get_json(url)
        if endpoint == "/moderations":
            payload = {"model": moderation_model, "input": "hello"}
            return post_json(url, headers, payload, timeout=timeout)
        if endpoint == "/embeddings":
            payload = {"model": embedding_model, "input": "hello"}
            return post_json(url, headers, payload, timeout=timeout)
        if endpoint == "/chat/completions":
            payload = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
            return post_json(url, headers, payload, timeout=timeout)
        if endpoint == "/completions":
            payload = {"model": model, "prompt": "hello"}
            return post_json(url, headers, payload, timeout=timeout)
        payload = {"model": model, "input": "hello"}
        return post_json(url, headers, payload, timeout=timeout)

    def parse_json_payload(body: str):
        text = body.strip() if isinstance(body, str) else ""
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        parsed_line_payload = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if isinstance(data, dict):
                parsed_line_payload = data
        if parsed_line_payload is not None:
            return parsed_line_payload
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            snippet = text[start : end + 1]
            try:
                data = json.loads(snippet)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def validate_success_body(endpoint: str, body: str) -> tuple[bool, str]:
        text = body.strip() if isinstance(body, str) else ""
        if not text:
            return False, "响应体为空"

        data = parse_json_payload(text)
        if data is None:
            return False, "响应体不是有效 JSON"

        if isinstance(data, dict) and "error" in data:
            error_obj = data.get("error")
            if error_obj not in (None, "", {}, []):
                return False, "响应中包含 error 字段"

        if endpoint == "/models":
            if not isinstance(data, dict):
                return False, "响应结构不是 JSON 对象"
            items = data.get("data")
            if isinstance(items, list):
                return True, ""
            return False, "缺少 data 列表"

        if endpoint == "/chat/completions" or endpoint == "/completions":
            if not isinstance(data, dict):
                return False, "响应结构不是 JSON 对象"
            choices = data.get("choices")
            if isinstance(choices, list):
                return True, ""
            if isinstance(data.get("id"), str) and isinstance(data.get("model"), str):
                return True, ""
            return False, "缺少 choices 或 id/model"

        if endpoint == "/responses":
            if not isinstance(data, dict):
                return False, "响应结构不是 JSON 对象"
            output = data.get("output")
            if isinstance(output, list):
                return True, ""
            output_text = data.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return True, ""
            keys = ("id", "object", "model", "status", "response")
            if any(k in data for k in keys):
                return True, ""
            return False, "缺少 output/output_text 或关键字段"

        if endpoint == "/embeddings":
            if not isinstance(data, dict):
                return False, "响应结构不是 JSON 对象"
            items = data.get("data")
            if isinstance(items, list):
                return True, ""
            return False, "缺少 data 列表"

        if endpoint == "/moderations":
            if not isinstance(data, dict):
                return False, "响应结构不是 JSON 对象"
            items = data.get("results")
            if isinstance(items, list):
                return True, ""
            return False, "缺少 results 列表"

        return True, ""

    model_supported: Optional[bool] = None
    model_source = ""
    model_in_list: Optional[bool] = None
    response_model = ""
    response_model_source = ""

    def parse_models(body: str) -> set[str]:
        data = parse_json_payload(body)
        if data is None:
            return set()
        if isinstance(data, dict):
            items = data.get("data")
            if isinstance(items, list):
                result: set[str] = set()
                for item in items:
                    if isinstance(item, dict):
                        mid = item.get("id")
                        if isinstance(mid, str):
                            result.add(mid)
                return result
        return set()

    def extract_response_model(body: str) -> str:
        data = parse_json_payload(body)
        if data is None:
            return ""
        if isinstance(data, dict):
            model_value = data.get("model")
            if isinstance(model_value, str):
                return model_value
            response_value = data.get("response")
            if isinstance(response_value, dict):
                nested_model = response_value.get("model")
                if isinstance(nested_model, str):
                    return nested_model
        return ""

    def is_model_error(body: str) -> bool:
        msg = str(body).lower()
        if "model" not in msg:
            return False
        keywords = ("not found", "not allowed", "not supported", "does not exist", "invalid")
        return any(k in msg for k in keywords)

    def set_model_support(value: bool, source: str) -> None:
        nonlocal model_supported, model_source
        if value is True:
            model_supported = True
            model_source = source
        elif model_supported is None:
            model_supported = False
            model_source = source

    def build_candidates() -> list[tuple[str, str, str]]:
        bases: list[str] = []
        base_clean = base.rstrip("/")
        bases.append(base_clean)
        parsed = urlparse(base_clean)
        base_path = parsed.path.rstrip("/")
        if base_path.endswith("/v1"):
            if base_path != "/v1":
                root_v1 = f"{parsed.scheme}://{parsed.netloc}/v1"
                bases.append(root_v1)
        else:
            bases.append(base_clean + "/v1")
        # de-dup while preserving order
        seen = set()
        uniq_bases: list[str] = []
        for item in bases:
            if item in seen:
                continue
            seen.add(item)
            uniq_bases.append(item)

        candidates: list[tuple[str, str, str]] = []
        for b in uniq_bases:
            prefix = urlparse(b).path.rstrip("/")
            for ep in (
                "/responses",
                "/chat/completions",
                "/completions",
                "/models",
                "/embeddings",
                "/moderations",
                "/realtime",
                "/assistants",
                "/batch",
                "/fine-tuning",
                "/images/generations",
                "/images/edits",
                "/videos",
                "/audio/speech",
                "/audio/transcriptions",
                "/audio/translations",
            ):
                url = b.rstrip("/") + ep
                label = f"{prefix}{ep}" if prefix else ep
                candidates.append((label, ep, url))
        # de-dup by url
        seen_url = set()
        result: list[tuple[str, str, str]] = []
        for label, ep, url in candidates:
            if url in seen_url:
                continue
            seen_url.add(url)
            result.append((label, ep, url))
        return result

    endpoints = build_candidates()
    results = []
    success_endpoint = ""
    for label, ep, url in endpoints:
        if ep in skip_endpoints:
            results.append((label, ep, url, None, f"SKIP: {skip_endpoints[ep]}"))
            continue
        ok, body = request_endpoint(ep, url)
        if ok:
            content_ok, reason = validate_success_body(ep, body)
            if not content_ok:
                ok = False
                body = f"HTTP 200 但响应内容无效：{reason}"
        results.append((label, ep, url, ok, body))
        if ok and ep in ("/responses", "/chat/completions", "/completions") and not success_endpoint:
            success_endpoint = label

    for _label, ep, _url, ok, body in results:
        if ok and ep in ("/responses", "/chat/completions", "/completions"):
            set_model_support(True, ep)
        if ep == "/models" and ok and model_in_list is None:
            models = parse_models(body)
            if models:
                model_in_list = model in models
                set_model_support(model_in_list, "/models")
    if model_supported is None:
        for _label, ep, _url, ok, body in results:
            if (ok is False) and ep in ("/responses", "/chat/completions", "/completions") and is_model_error(body):
                set_model_support(False, ep)

    for _label, ep, _url, ok, body in results:
        if ok and ep in ("/responses", "/chat/completions", "/completions"):
            response_model = extract_response_model(body)
            if response_model:
                response_model_source = ep
                break

    in_list_text = "未知"
    if model_in_list is True:
        in_list_text = "是"
    elif model_in_list is False:
        in_list_text = "否"

    model_text = "可用" if model_supported is True else "不可用" if model_supported is False else "未知"
    model_hint = f"（来源: {model_source}）" if model_source else ""

    errors_text = " ".join(str(body).lower() for _label, _ep, _url, _ok, body in results)
    supported = [label for label, _ep, _url, ok, _body in results if ok]
    supported_urls = []
    for _label, _ep, url, ok, _body in results:
        if ok and url not in supported_urls:
            supported_urls.append(url)
    supported_text = ", ".join(supported) if supported else "无"

    if success_endpoint:
        conclusion = f"结论：链路正常（API 请求成功，接口: {success_endpoint}）"
    elif any(label.endswith("/models") for label in supported):
        conclusion = "结论：仅 /models 可用，API 接口可能受限"
    else:
        if "401" in errors_text or "403" in errors_text or "auth" in errors_text:
            conclusion = "结论：账号/密钥可能有误"
        elif "404" in errors_text or "not found" in errors_text:
            conclusion = "结论：接口可能不支持（请更换诊断接口）"
        else:
            conclusion = "结论：疑似中转服务异常"

    summary_lines = []
    summary_lines.append(f"Base URL: {base}")
    summary_lines.append(f"Base Host: {base_host}")
    summary_lines.append(
        "Base 连通："
        f"Ping={fmt_ms(ping_avg)} / "
        f"HTTP={fmt_ms(http_avg)} / "
        f"Port={'OK' if port_ok else 'FAIL' if port_ok is not None else '不可用'}"
    )
    summary_lines.append(f"\n可用接口：{supported_text}")
    if supported_urls:
        summary_lines.append("可用接口(URL)：")
        for url in supported_urls:
            summary_lines.append(f"- {url}")
    summary_lines.append(f"模型列表包含（{model}）：{in_list_text}")
    if response_model:
        src_label = response_model_source or "未知"
        summary_lines.append(f"实际返回 model：{response_model}（来源: {src_label}）")
    summary_detail = "\n".join(summary_lines)

    lines = list(summary_lines)
    lines.append(f"模型可用性（{model}）：{model_text}{model_hint}")
    lines.append(f"模型列表包含（{model}）：{in_list_text}")
    if response_model:
        src_label = response_model_source or "未知"
        lines.append(f"实际返回 model：{response_model}（来源: {src_label}）")
    lines.append(f"Embedding 测试模型：{embedding_model}")
    lines.append(f"Moderation 测试模型：{moderation_model}")
    lines.append("\n接口探测结果：")
    for label, _ep, _url, ok, body in results:
        if ok is True:
            lines.append(f"- {label}: OK")
        elif ok is False:
            brief = str(body).splitlines()[0][:200] if body else "-"
            lines.append(f"- {label}: FAIL ({brief})")
        else:
            lines.append(f"- {label}: {body}")
    lines.append("\nAPI 请求结果：" + ("成功" if success_endpoint else "失败"))

    detail = "\n".join(lines)
    return {
        "conclusion": conclusion,
        "detail": detail,
        "summary_detail": summary_detail,
        "supported_labels": supported,
        "supported_urls": supported_urls,
        "model_supported": model_supported,
        "model_source": model_source,
        "model_in_list": model_in_list,
        "response_model": response_model,
        "response_model_source": response_model_source,
        "success_endpoint": success_endpoint,
        "results": results,
        "base_host": base_host,
        "port_ms": port_ms,
    }

class AppState:
    def __init__(self) -> None:
        self.store = load_store()
        self.active_account = get_active_account(self.store)
        self.codex_path: Optional[str] = None
        self.codex_version: Optional[str] = None
        self.vscode_install_dir: Optional[str] = None
        saved_dir = self.store.get("vscode_install_dir")
        if isinstance(saved_dir, str) and saved_dir:
            self.vscode_install_dir = saved_dir

class AccountPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, refresh_pages=None) -> None:
        super().__init__()
        self.state = state
        self.refresh_pages = refresh_pages or (lambda: None)
        self.account_items: List[Dict[str, str]] = []

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("多账号切换")
        header.setFont(self._header_font())
        layout.addWidget(header)

        list_width = 280
        form_width = 320
        card_gap = 12
        account_group_width = list_width + form_width + card_gap + 10

        current_group = QtWidgets.QGroupBox("当前账号")
        apply_white_shadow(current_group)
        current_group.setMinimumWidth(account_group_width)
        current_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        current_layout = QtWidgets.QHBoxLayout(current_group)
        self.current_label = QtWidgets.QLabel("未选择")
        self.current_label.setWordWrap(False)
        self.current_label.setToolTip("未选择")
        current_layout.addWidget(self.current_label)
        current_layout.addStretch(1)
        layout.addWidget(current_group)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(card_gap)
        layout.addLayout(body)

        account_group = QtWidgets.QGroupBox("多账号管理")
        apply_white_shadow(account_group)
        account_group.setMinimumWidth(account_group_width)
        account_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        account_layout = QtWidgets.QHBoxLayout(account_group)
        account_layout.setSpacing(card_gap)

        # 左侧列表
        list_panel = QtWidgets.QWidget()
        list_panel.setMinimumWidth(list_width)
        list_panel.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        list_layout = QtWidgets.QVBoxLayout(list_panel)
        list_title = QtWidgets.QLabel("账号列表")
        list_layout.addWidget(list_title)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setMinimumWidth(0)
        self.list_widget.setMinimumHeight(200)
        self.list_widget.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        self.list_widget.setTextElideMode(QtCore.Qt.ElideRight)
        self.list_widget.currentRowChanged.connect(self.on_select)
        list_layout.addWidget(self.list_widget)
        list_layout.addStretch(1)
        btn_row = QtWidgets.QHBoxLayout()
        self.apply_btn = QtWidgets.QPushButton("应用账号")
        self.delete_btn = QtWidgets.QPushButton("删除账号")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.apply_btn.clicked.connect(self.apply_selected)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch(1)
        list_layout.addLayout(btn_row)

        # 右侧表单
        form_panel = QtWidgets.QWidget()
        form_panel.setMinimumWidth(form_width)
        form_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        form_panel_layout = QtWidgets.QVBoxLayout(form_panel)
        form_title = QtWidgets.QLabel("新增/更新账号")
        form_panel_layout.addWidget(form_title)
        form_layout = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.base_edit = QtWidgets.QLineEdit()
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.org_edit = QtWidgets.QLineEdit()
        self.test_model_edit = QtWidgets.QLineEdit()
        self.test_model_edit.setPlaceholderText("默认使用gpt-5.2-codex，使用其它模型请手动填入。")
        self.type_group = QtWidgets.QButtonGroup(self)
        self.type_team = QtWidgets.QRadioButton("Team 账号")
        self.type_official = QtWidgets.QRadioButton("ChatGPT 官方账号")
        self.type_proxy = QtWidgets.QRadioButton("中转账号")
        self.type_group.addButton(self.type_team)
        self.type_group.addButton(self.type_official)
        self.type_group.addButton(self.type_proxy)
        self.type_proxy.setChecked(True)
        self.type_official.toggled.connect(self._handle_account_type_change)
        type_row = QtWidgets.QHBoxLayout()
        type_row.addWidget(QtWidgets.QLabel("账号类型："))
        type_row.addWidget(self.type_team)
        type_row.addWidget(self.type_official)
        type_row.addWidget(self.type_proxy)
        type_row.addStretch(1)
        form_panel_layout.addLayout(type_row)
        form_layout.addRow("名称", self.name_edit)
        form_layout.addRow("Base URL", self.base_edit)
        form_layout.addRow("API Key", self.key_edit)
        form_layout.addRow("Org ID", self.org_edit)
        form_layout.addRow("测试模型", self.test_model_edit)
        form_panel_layout.addLayout(form_layout)
        form_panel_layout.addStretch(1)

        form_btn_row = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("保存/更新")
        self.clear_btn = QtWidgets.QPushButton("清空")
        self.test_btn = QtWidgets.QPushButton("账户测试")
        self.copy_btn = QtWidgets.QPushButton("复制账号信息")
        self.save_btn.clicked.connect(self.save_account)
        self.clear_btn.clicked.connect(self.clear_form)
        self.test_btn.clicked.connect(self.test_account)
        self.copy_btn.clicked.connect(self.copy_account_info)
        form_btn_row.addWidget(self.save_btn)
        form_btn_row.addWidget(self.clear_btn)
        form_btn_row.addWidget(self.test_btn)
        form_btn_row.addWidget(self.copy_btn)
        form_btn_row.addStretch(1)
        form_panel_layout.addLayout(form_btn_row)

        form_panel_layout.addStretch(0)

        account_layout.addWidget(list_panel)
        account_layout.addWidget(form_panel)

        body.addWidget(account_group)

        hint_group = QtWidgets.QGroupBox("提示")
        apply_white_shadow(hint_group)
        hint_group.setMinimumWidth(account_group_width)
        hint_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        hint_layout = QtWidgets.QVBoxLayout(hint_group)
        hint_label = QtWidgets.QLabel()
        hint_label.setTextFormat(QtCore.Qt.RichText)
        hint_label.setText(
            '<ul style="margin:0 0 0 14px; padding:0; line-height:1.6;">'
            '<li>官方账号请填写 Base URL=https://api.openai.com/v1，API Key 使用官方 Key。</li>'
            '<li>账号类型：Team=需要 Org ID；官方/中转=可不填 Org ID。</li>'
            '<li>“账户测试”和“测试模型”用于确保输入的账户信息正确。</li>'
            '<li>“测试模型”可自由填入实际可用模型。</li>'
            '</ul>'
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666;")
        hint_layout.addWidget(hint_label)
        layout.addWidget(hint_group)

        layout.addStretch(1)

    def copy_account_info(self) -> None:
        name = self.name_edit.text().strip()
        base = self.base_edit.text().strip()
        api_key = self.key_edit.text().strip()
        org_id = self.org_edit.text().strip()
        if not name or not base or not api_key:
            message_warn(self, "提示", "名称、Base URL、API Key 不能为空")
            return
        kind = "Team" if self._get_selected_account_type() == "team" else ("官方" if self._get_selected_account_type() == "official" else "中转")
        lines = [
            f"名称：{name}",
            f"账号类型：{kind}",
            f"Base URL：{base}",
            f"API Key：{api_key}",
            f"Org ID：{org_id}",
        ]
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        message_info(self, "提示", "账号信息已复制")

    def _handle_account_type_change(self, checked: bool) -> None:
        if checked:
            self.base_edit.setText("https://api.openai.com/v1")

    def _account_kind(self, account: Dict[str, str]) -> str:
        if account.get("is_team") == "1" or account.get("account_type") == "team":
            return "Team"
        if account.get("account_type") == "official":
            return "官方"
        return "中转"

    def _get_selected_account_type(self) -> str:
        if self.type_team.isChecked():
            return "team"
        if self.type_official.isChecked():
            return "official"
        return "proxy"

    def _set_account_type_from_account(self, account: Dict[str, str]) -> None:
        if account.get("is_team") == "1" or account.get("account_type") == "team":
            self.type_team.setChecked(True)
            return
        if account.get("account_type") == "official":
            self.type_official.setChecked(True)
            return
        self.type_proxy.setChecked(True)
    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font


    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        self.account_items = build_accounts(self.state.store)
        for item in self.account_items:
            kind = "Team" if item.get("is_team") == "1" else "中转"
            label = f"[{kind}] {item.get('name', '')} -> {item.get('base_url', '')}"
            self.list_widget.addItem(label)
        self.state.active_account = get_active_account(self.state.store)
        current = self.state.active_account
        if current:
            kind = self._account_kind(current)
            label = f"[{kind}] {current.get('name', '')} | {current.get('base_url', '')}"
            label = label.replace("\n", " ").replace("\r", " ")
            self.current_label.setText(label)
            self.current_label.setToolTip(label)
        else:
            self.current_label.setText("未选择")
            self.current_label.setToolTip("未选择")

    def on_select(self, row: int) -> None:
        if row < 0 or row >= len(self.account_items):
            return
        account = self.account_items[row]
        self.name_edit.setText(account.get("name", ""))
        self.base_edit.setText(account.get("base_url", ""))
        self.key_edit.setText(account.get("api_key", ""))
        self.org_edit.setText(account.get("org_id", ""))
        self._set_account_type_from_account(account)

    def _find_account_row(self, name: str, is_team: bool) -> int:
        team_flag = "1" if is_team else "0"
        for idx, item in enumerate(self.account_items):
            if item.get("name", "") == name and item.get("is_team", "0") == team_flag:
                return idx
        return -1

    def _apply_selected(self, show_message: bool = True) -> bool:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.account_items):
            message_warn(self, "提示", "请选择账号")
            return False
        account = self.account_items[row]
        apply_account_config(self.state.store, account)
        apply_env_for_account(account)
        set_active_account(self.state.store, account)
        self.state.active_account = account
        self.refresh()
        selected_row = self._find_account_row(account.get("name", ""), account.get("is_team") == "1")
        if selected_row >= 0:
            self.list_widget.setCurrentRow(selected_row)
        self.refresh_pages()
        if show_message:
            message_info(self, "完成", "账号已应用")
        return True

    def apply_selected(self) -> None:
        self._apply_selected(show_message=True)

    def save_account(self) -> None:
        name = self.name_edit.text().strip()
        base_url = self.base_edit.text().strip()
        api_key = self.key_edit.text().strip()
        org_id = self.org_edit.text().strip()
        account_type = self._get_selected_account_type()
        is_team = account_type == "team"
        if not name or not base_url or not api_key:
            message_warn(self, "提示", "名称、Base URL、API Key 不能为空")
            return
        if is_team and not org_id:
            message_warn(self, "提示", "Team 账号需要填写 Org ID")
            return
        upsert_account(self.state.store, name, base_url, api_key, org_id, is_team, account_type)
        self.refresh()
        row = self._find_account_row(name, is_team)
        if row < 0:
            message_info(self, "完成", "账号已保存")
            return
        self.list_widget.setCurrentRow(row)
        self._apply_selected(show_message=True)

    def delete_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.account_items):
            message_warn(self, "提示", "请选择账号")
            return
        account = self.account_items[row]
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认",
            f"确认删除账号 {account.get('name', '')}？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        delete_account(self.state.store, account)
        self.refresh()
        self.refresh_pages()

    def clear_form(self) -> None:
        self.name_edit.clear()
        self.base_edit.clear()
        self.key_edit.clear()
        self.org_edit.clear()
        self.type_proxy.setChecked(True)

    def test_account(self) -> None:
        base = self.base_edit.text().strip().rstrip("/")
        api_key = self.key_edit.text().strip()
        org_id = self.org_edit.text().strip()
        if not base or not api_key:
            message_warn(self, "提示", "Base URL 或 API Key 不能为空")
            return
        account_type = self._get_selected_account_type()
        if account_type == "team" and not org_id:
            message_warn(self, "提示", "Team 账号需要填写 Org ID")
            return

        exe = find_codex_exe()
        if not exe:
            message_warn(self, "提示", "未检测到 codex 命令，请先安装")
            return

        model = self.test_model_edit.text().strip() or "gpt-5.2-codex"
        account = {
            "name": self.name_edit.text().strip() or "temp",
            "base_url": base,
            "api_key": api_key,
            "org_id": org_id,
            "is_team": "1" if account_type == "team" else "0",
        }
        apply_env_for_account(account)

        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")
        env = os.environ.copy()
        try:
            exe_lower = exe.lower()
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
                if exe_lower.endswith(".ps1"):
                    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, "chat", "-m", model]
                elif exe_lower.endswith(".cmd") or exe_lower.endswith(".bat"):
                    cmd = ["cmd.exe", "/k", exe, "chat", "-m", model]
                else:
                    cmd = [exe, "chat", "-m", model]
                subprocess.Popen(cmd, env=env, creationflags=creationflags)
            else:
                subprocess.Popen([exe, "chat", "-m", model], env=env)
            self.test_btn.setEnabled(True)
            self.test_btn.setText("账户测试")
            message_info(self, "提示", "已进入 chat 模式，请输入任意内容并等待模型回复，以确保“账号/密钥/Base URL”等信息正确")
        except Exception as exc:
            self.test_btn.setEnabled(True)
            self.test_btn.setText("账户测试")
            message_error(self, "失败", str(exc))

class NetworkDiagnosticsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("中转站接口")
        header.setFont(self._header_font())
        layout.addWidget(header)

        self.diag_group = QtWidgets.QGroupBox("关键诊断")
        apply_white_shadow(self.diag_group)
        diag_layout = QtWidgets.QVBoxLayout(self.diag_group)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("当前 Base URL"))
        self.base_label = QtWidgets.QLabel("-")
        self.base_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row1.addWidget(self.base_label, 1)
        row1.addStretch(1)
        diag_layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("测试模型"))
        self.model_edit = QtWidgets.QLineEdit()
        self.model_edit.setPlaceholderText("gpt-5.2-codex")
        row2.addWidget(self.model_edit)
        self.run_btn = QtWidgets.QPushButton("接口检测")
        self.run_btn.clicked.connect(self.start_diagnosis)
        row2.addWidget(self.run_btn)
        row2.addStretch(1)
        diag_layout.addLayout(row2)

        self.conclusion_label = QtWidgets.QLabel("结论：-")
        self.conclusion_label.setWordWrap(True)
        diag_layout.addWidget(self.conclusion_label)

        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        size_hint = self.detail_text.sizeHint().height()
        if size_hint:
            self.detail_text.setFixedHeight(max(60, size_hint // 2))
        else:
            self.detail_text.setMinimumHeight(60)
        diag_layout.addWidget(self.detail_text)

        copy_row = QtWidgets.QHBoxLayout()
        self.copy_urls_btn = QtWidgets.QPushButton("复制可用接口(URL)")
        self.copy_urls_btn.clicked.connect(self.copy_supported_urls)
        copy_row.addWidget(self.copy_urls_btn)
        copy_row.addStretch(1)
        diag_layout.addLayout(copy_row)

        layout.addWidget(self.diag_group, alignment=QtCore.Qt.AlignLeft)
        self.probe_group = QtWidgets.QGroupBox("账号池可用模型探测")
        apply_white_shadow(self.probe_group)
        probe_layout = QtWidgets.QVBoxLayout(self.probe_group)
        cfg_group = QtWidgets.QGroupBox("配置")
        apply_white_shadow(cfg_group)
        cfg_layout = QtWidgets.QHBoxLayout(cfg_group)
        self.base_edit = QtWidgets.QLineEdit()
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.retries_spin = QtWidgets.QSpinBox()
        self.retries_spin.setRange(1, 20)
        self.retries_spin.setValue(3)
        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(1, 999)
        self.timeout_spin.setValue(90)
        self.start_probe_btn = QtWidgets.QPushButton("开始探测")
        self.start_probe_btn.clicked.connect(self.start_probe)
        cfg_layout.addWidget(QtWidgets.QLabel("Base URL"))
        cfg_layout.addWidget(self.base_edit, 1)
        cfg_layout.addWidget(QtWidgets.QLabel("API Key"))
        cfg_layout.addWidget(self.key_edit, 1)
        cfg_layout.addWidget(QtWidgets.QLabel("重试次数"))
        cfg_layout.addWidget(self.retries_spin)
        cfg_layout.addWidget(QtWidgets.QLabel("超时(s)"))
        cfg_layout.addWidget(self.timeout_spin)
        cfg_layout.addWidget(self.start_probe_btn)
        probe_layout.addWidget(cfg_group)

        body = QtWidgets.QHBoxLayout()
        probe_layout.addLayout(body)

        input_group = QtWidgets.QGroupBox("模型名称")
        apply_white_shadow(input_group)
        input_layout = QtWidgets.QVBoxLayout(input_group)
        self.model_text = QtWidgets.QLineEdit()
        self.model_text.setPlaceholderText("例如：gpt-5.2-codex")
        input_layout.addWidget(self.model_text)
        body.addWidget(input_group)

        result_group = QtWidgets.QGroupBox("结果")
        apply_white_shadow(result_group)
        result_layout = QtWidgets.QVBoxLayout(result_group)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["模型", "状态", "返回结果"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header_h = self.table.horizontalHeader().height()
        row_h = self.table.verticalHeader().defaultSectionSize()
        self.table.setFixedHeight(header_h + row_h + 8)
        result_layout.addWidget(self.table)
        body.addWidget(result_group)
        body.setStretch(0, 1)
        body.setStretch(1, 3)

        hint_group = QtWidgets.QGroupBox("提示")
        apply_white_shadow(hint_group)
        hint_layout = QtWidgets.QVBoxLayout(hint_group)
        hint_label = QtWidgets.QLabel()
        hint_label.setTextFormat(QtCore.Qt.RichText)
        hint_label.setText(
            '<ul style="margin:0 0 0 14px; padding:0; line-height:1.6;">'
            '<li>本工具使用UA请求方式探测，但也有被中转站/WAF风控拦截的可能性，请检查日志文件 .codex\\codex_switcher.log。</li>'
            '<li>可用模型主要是在oai推出新模型时，查看中转站账号池中能不能使用的目的。</li>'
            '<li>中转站账号池无号源时，理论上不影响中转站接口和模型探测。</li>'
            '</ul>'
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666;")
        hint_layout.addWidget(hint_label)
        probe_layout.addWidget(hint_group)

        self.probe_status_label = QtWidgets.QLabel("就绪")
        probe_layout.addWidget(self.probe_status_label)

        layout.addWidget(self.probe_group, alignment=QtCore.Qt.AlignLeft)
        layout.addStretch(1)

    def _sync_card_widths(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        margins = layout.contentsMargins()
        width = max(0, self.width() - margins.left() - margins.right())
        if width <= 0:
            return
        self.diag_group.setFixedWidth(width)
        self.probe_group.setFixedWidth(width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_card_widths()

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def _start_marquee(self, label: QtWidgets.QLabel, base_text: str, key: str) -> None:
        self._stop_marquee(key)
        trail = ">>>>>>"
        pad = 6
        frames = []
        for i in range(pad):
            frames.append(f"{' ' * i}{trail}")
        for i in range(pad - 2, -1, -1):
            frames.append(f"{' ' * i}{trail}")
        state = {"index": 0, "frames": frames, "base": base_text, "label": label, "style": label.styleSheet()}
        timer = QtCore.QTimer(self)

        def tick() -> None:
            idx = state["index"]
            state["index"] = idx + 1
            frame = state["frames"][idx % len(state["frames"])]
            label.setText(f"{base_text} {frame}")

        timer.timeout.connect(tick)
        timer.start(120)
        label.setStyleSheet("color: #e53935; font-weight: 700; background-color: #fff3e0; padding: 2px 6px; border-radius: 4px;")
        pool = getattr(self, "_marquee", None)
        if pool is None:
            pool = {}
            self._marquee = pool
        pool[key] = (timer, state)
        tick()

    def _stop_marquee(self, key: str) -> None:
        pool = getattr(self, "_marquee", None)
        if not pool or key not in pool:
            return
        timer, state = pool.pop(key)
        timer.stop()
        label = state.get("label")
        prev_style = state.get("style")
        if label is not None and prev_style is not None:
            label.setStyleSheet(prev_style)

    def copy_supported_urls(self) -> None:
        urls = getattr(self, "_supported_urls", [])
        if not urls:
            message_warn(self, "提示", "暂无可用接口列表")
            return
        QtWidgets.QApplication.clipboard().setText("\n".join(urls))
        message_info(self, "提示", "可用接口(URL)已复制")

    def on_show(self) -> None:
        account = self.state.active_account
        base = account.get("base_url", "") if account else ""
        self.base_label.setText(base or "-")
        self._sync_card_widths()
        if hasattr(self, 'base_edit') and hasattr(self, 'key_edit'):
            if account:
                self.base_edit.setText(account.get('base_url', ''))
                self.key_edit.setText(account.get('api_key', ''))

    
    def start_probe(self) -> None:
        base = self.base_edit.text().strip().rstrip("/")
        api_key = self.key_edit.text().strip()
        if not base or not api_key:
            message_warn(self, "提示", "base_url 或 api_key 不能为空")
            return
        model = self.model_text.text().strip()
        if not model:
            message_warn(self, "提示", "请输入模型名称")
            return
        retries = int(self.retries_spin.value())
        timeout = int(self.timeout_spin.value())
        self._start_marquee(self.probe_status_label, "探测中", "_probe_marquee")
        self.table.setRowCount(0)

        org_id = ""
        account = self.state.active_account
        if account:
            account_base = (account.get("base_url", "") or "").strip().rstrip("/")
            account_key = (account.get("api_key", "") or "").strip()
            if base == account_base and api_key == account_key:
                org_id = (account.get("org_id", "") or "").strip()

        def apply_result(result: Dict[str, object], conclusion: str) -> None:
            self.append_result(result)
            self._stop_marquee("_probe_marquee")
            if conclusion:
                self.probe_status_label.setText(conclusion)

        def runner() -> None:
            last_result = None
            for attempt in range(1, retries + 1):
                try:
                    diag = probe_endpoints(base, api_key, org_id, model, timeout=timeout)
                except Exception as exc:
                    result = {"model": model, "ok": False, "endpoint": "", "error": str(exc)}
                    last_result = (result, "探测失败")
                    break
                ok_value = diag.get("model_supported")
                endpoint = diag.get("model_source") or diag.get("success_endpoint") or ""
                error = "" if ok_value is True else diag.get("conclusion", "")
                result = {
                    "model": model,
                    "ok": ok_value,
                    "endpoint": endpoint,
                    "error": error,
                    "response_model": diag.get("response_model", ""),
                    "model_in_list": diag.get("model_in_list"),
                }
                last_result = (result, diag.get("conclusion", "完成"))
                if ok_value is True or diag.get("success_endpoint"):
                    break
                if attempt < retries:
                    time.sleep(2)
            if last_result:
                result, conclusion = last_result
                run_in_ui(lambda r=result, c=conclusion: apply_result(r, c))

        threading.Thread(target=runner, daemon=True).start()

    def _append_row(self, values: list[object]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, value in enumerate(values):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))

    def append_result(self, result: Dict[str, object]) -> None:
        ok_value = result.get("ok")
        if ok_value is True:
            ok_text = "该模型可用"
        elif ok_value is False:
            ok_text = "该模型不可用"
        else:
            ok_text = "未知"
        if ok_value is True:
            return_value = result.get("endpoint")
            response_model = result.get("response_model") or ""
            model_in_list = result.get("model_in_list")
            extras = []
            if response_model:
                extras.append(f"实际模型={response_model}")
            if model_in_list is True:
                extras.append("模型列表=是")
            elif model_in_list is False:
                extras.append("模型列表=否")
            if extras:
                extra_text = "，".join(extras)
                if return_value:
                    return_value = f"{return_value}（{extra_text}）"
                else:
                    return_value = f"（{extra_text}）"
        else:
            return_value = result.get("error") or ""
        values = [result.get("model"), ok_text, return_value]
        self._append_row(values)

    def start_diagnosis(self) -> None:
        account = self.state.active_account
        if not account:
            message_warn(self, "提示", "请先选择账号")
            return
        base = (account.get("base_url", "") or "").strip().rstrip("/")
        api_key = (account.get("api_key", "") or "").strip()
        org_id = (account.get("org_id", "") or "").strip()
        if not base or not api_key:
            message_warn(self, "提示", "Base URL 或 API Key 不能为空")
            return

        model = self.model_edit.text().strip() or "gpt-5.2-codex"
        base_host = extract_host(base)
        if not base_host:
            message_warn(self, "提示", "Base URL 无效，无法解析主机")
            return

        self.run_btn.setEnabled(False)
        self._start_marquee(self.conclusion_label, "结论：诊断中", "_diag_marquee")
        self.detail_text.setPlainText("")

        def runner() -> None:
            try:
                diagnosis = probe_endpoints(base, api_key, org_id, model, timeout=60)
                conclusion = diagnosis.get("conclusion", "结论：诊断失败")
                detail = diagnosis.get("detail", "")
                summary_detail = diagnosis.get("summary_detail", detail)
                supported = diagnosis.get("supported_labels", [])
                supported_urls = diagnosis.get("supported_urls", [])
                if not diagnosis.get("success_endpoint"):
                    log_diagnosis("诊断失败", f"{conclusion}\n{detail}")
                def done() -> None:
                    self.run_btn.setEnabled(True)
                    model_in_list = diagnosis.get("model_in_list")
                    in_list_text = "未知"
                    if model_in_list is True:
                        in_list_text = "是"
                    elif model_in_list is False:
                        in_list_text = "否"
                    conclusion_text = f"{conclusion} | 模型列表包含（{model}）：{in_list_text}"
                    self._stop_marquee("_diag_marquee")
                    self.conclusion_label.setText(conclusion_text)
                    self.detail_text.setPlainText(summary_detail)
                    self._supported_labels = supported
                    self._supported_urls = supported_urls

                run_in_ui(done)
            except Exception as exc:
                log_exception(exc)

                def done() -> None:
                    self.run_btn.setEnabled(True)
                    self._stop_marquee("_diag_marquee")
                    self.conclusion_label.setText("结论：诊断失败")
                    message_error(self, "失败", str(exc))

                run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

class CodexStatusPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._local_version: Optional[str] = None
        self._latest_version: Optional[str] = None
        self._workspace_dir: Optional[Path] = None

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("Codex 状态")
        header.setFont(self._header_font())
        layout.addWidget(header)

        action_row = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("刷新检测")
        self.refresh_btn.setToolTip("刷新检测 (F5 / Ctrl+R)")
        self.refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._handle_refresh_click)
        action_row.addWidget(self.refresh_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.refresh_shortcut = QtGui.QShortcut(QtGui.QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self._handle_refresh_click)
        self.refresh_shortcut2 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+R"), self)
        self.refresh_shortcut2.activated.connect(self._handle_refresh_click)

        local_group = QtWidgets.QGroupBox("本机 Codex CLI")
        local_layout = QtWidgets.QVBoxLayout(local_group)
        self.local_status = QtWidgets.QLabel("检测中...")
        self.local_version = QtWidgets.QLabel("路径：-")
        self.local_hint = QtWidgets.QLabel("安装命令：npm i -g @openai/codex")
        local_layout.addWidget(self.local_status)
        local_layout.addWidget(self.local_version)
        local_layout.addWidget(self.local_hint)
        layout.addWidget(local_group)

        latest_group = QtWidgets.QGroupBox("官方最新版本")
        latest_layout = QtWidgets.QVBoxLayout(latest_group)
        self.latest_status = QtWidgets.QLabel("检测中...")
        self.latest_version = QtWidgets.QLabel("版本：-")
        self.latest_hint = QtWidgets.QLabel("更新命令：npm i -g @openai/codex@latest")
        self.update_btn = QtWidgets.QPushButton("一键更新")
        self.update_btn.clicked.connect(self.handle_update_click)
        update_row = QtWidgets.QHBoxLayout()
        update_row.addWidget(self.latest_hint)
        update_row.addWidget(self.update_btn)
        update_row.addStretch(1)
        latest_layout.addWidget(self.latest_status)
        latest_layout.addWidget(self.latest_version)
        latest_layout.addLayout(update_row)
        self.latest_group = latest_group
        layout.addWidget(self.latest_group)

        launch_group = QtWidgets.QGroupBox("Codex CLI 一键启动")
        apply_white_shadow(launch_group)
        launch_layout = QtWidgets.QVBoxLayout(launch_group)
        path_row = QtWidgets.QHBoxLayout()
        workspace_caption = QtWidgets.QLabel("\u5de5\u4f5c\u533a")
        self.workspace_path_edit = QtWidgets.QLineEdit()
        self.workspace_path_edit.setReadOnly(True)
        self.workspace_path_edit.setPlaceholderText("\u672a\u9009\u62e9\u5de5\u4f5c\u533a")
        self.workspace_path_edit.setText("\u672a\u9009\u62e9\u5de5\u4f5c\u533a")
        self.workspace_path_edit.setClearButtonEnabled(False)
        self.workspace_path_edit.setMinimumHeight(32)
        self.workspace_path_edit.setStyleSheet(
            "QLineEdit {"
            "border: 1px solid #8ea6ff;"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            "background: #ffffff;"
            "}"
            "QLineEdit:read-only {"
            "background: #f7f9ff;"
            "}"
        )
        self.workspace_path_edit.setToolTip("\u672a\u9009\u62e9\u5de5\u4f5c\u533a")
        self.pick_workspace_btn = QtWidgets.QPushButton("\u9009\u62e9\u5de5\u4f5c\u533a")
        self.pick_workspace_btn.clicked.connect(self.pick_workspace)
        path_row.addWidget(workspace_caption)
        path_row.addWidget(self.workspace_path_edit, 1)
        path_row.addWidget(self.pick_workspace_btn)
        launch_layout.addLayout(path_row)
        launch_btn_row = QtWidgets.QHBoxLayout()
        self.launch_codex_btn = QtWidgets.QPushButton("一键启动 CODEX CLI")
        self.launch_codex_btn.clicked.connect(self.launch_codex_cli)
        launch_btn_row.addWidget(self.launch_codex_btn)
        launch_btn_row.addStretch(1)
        launch_layout.addLayout(launch_btn_row)
        layout.addWidget(launch_group)
        
        self.compare_status = QtWidgets.QLabel("")
        self.compare_status.setVisible(False)
        self.progress_label = QtWidgets.QLabel("")
        layout.addWidget(self.progress_label)
        layout.addWidget(self.compare_status)
        layout.addStretch(1)


    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        self.refresh_status()
        self._update_debug()

    def _handle_refresh_click(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        self._refresh_token = getattr(self, "_refresh_token", 0) + 1
        token = self._refresh_token
        self.local_status.setText("检测中...")
        self.local_version.setText("路径：-")
        self.local_hint.setText("安装命令：npm i -g @openai/codex")
        self.latest_status.setText("检测中...")
        self.latest_version.setText("版本：-")
        self.latest_hint.setText("更新命令：npm i -g @openai/codex@latest")
        self.latest_group.setVisible(True)
        self.compare_status.setVisible(False)
        self.progress_label.setText("步骤：准备检测")
        self._local_version = None
        self._latest_version = None

        def runner() -> None:
            run_in_ui(lambda: self.progress_label.setText("步骤：检查本地 codex"))
            try:
                local_ok, local_ver, local_path, local_msg = self._get_local_version()
            except Exception as exc:
                local_ok, local_ver, local_path, local_msg = False, "-", "-", f"{exc}"

            def apply_local() -> None:
                if getattr(self, "_refresh_token", 0) != token:
                    return
                if local_ok:
                    self.local_status.setText(f"已安装 | {local_ver}")
                    self.local_version.setText(f"路径：{local_path}")
                    if local_msg and not self._extract_semver(local_ver):
                        self.local_hint.setText(f"版本获取失败：{local_msg}")
                    else:
                        self.local_hint.setText("")
                else:
                    self.local_status.setText("未安装")
                    self.local_version.setText(f"原因：{local_msg}")
                    self.local_hint.setText("安装命令：npm i -g @openai/codex")
                self.state.codex_path = local_path if local_ok else None
                self.state.codex_version = local_ver if local_ok else None
                self._local_version = local_ver if local_ok else None
                self._update_debug()

            run_in_ui(apply_local)

            run_in_ui(lambda: self.progress_label.setText("步骤：检查最新版本"))
            try:
                latest_ok, latest_ver, latest_msg = self._get_latest_version()
            except Exception as exc:
                latest_ok, latest_ver, latest_msg = False, "-", f"{exc}"

            def apply_latest() -> None:
                if getattr(self, "_refresh_token", 0) != token:
                    return
                if latest_ok:
                    self.latest_status.setText("可获取")
                    self.latest_version.setText(f"版本：{latest_ver}")
                    self.latest_hint.setText("更新命令：npm i -g @openai/codex@latest")
                else:
                    self.latest_status.setText("获取失败")
                    self.latest_version.setText(f"原因：{latest_msg}")
                    self.latest_hint.setText("")
                self._latest_version = latest_ver if latest_ok else None
                compare_text = ""
                if local_ok and latest_ok:
                    compare_text = self._compare_versions(local_ver, latest_ver)
                    if not compare_text:
                        compare_text = "本地版本未知，无法比较。"
                self.compare_status.setText(compare_text)
                self.compare_status.setVisible(bool(compare_text))
                self.progress_label.setText("步骤：完成")
                self._update_debug()

            run_in_ui(apply_latest)

        threading.Thread(target=runner, daemon=True).start()

    def handle_update_click(self) -> None:
        latest = self._latest_version
        if not latest:
            message_warn(self, "提示", "未获取到官方最新版本，请先刷新检测")
            return
        local = self._local_version
        latest_sem = self._extract_semver(latest or "")
        local_sem = self._extract_semver(local or "")
        if latest_sem and local_sem and latest_sem == local_sem:
            message_info(self, "提示", "当前已是最新版本")
            return
        if not self._open_terminal_command("npm i -g @openai/codex@latest"):
            message_error(self, "失败", "无法启动终端，请手动运行更新命令")
            return
        message_info(self, "提示", "已启动更新，请更新完成后重新打开窗口")

    def pick_workspace(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择工作区")
        if not folder:
            return
        path = Path(folder)
        if not path.exists() or not path.is_dir():
            message_warn(self, "提示", "选择的目录无效")
            return
        self._workspace_dir = path
        self.workspace_path_edit.setText(str(path))
        self.workspace_path_edit.setToolTip(str(path))

    def _ensure_workspace(self) -> Optional[Path]:
        if not self._workspace_dir:
            message_warn(self, "提示", "请先选择工作区")
            return None
        if not self._workspace_dir.exists():
            message_warn(self, "提示", "工作区不存在")
            return None
        return self._workspace_dir

    def launch_codex_cli(self) -> None:
        workspace = self._ensure_workspace()
        if not workspace:
            return
        exe = self._find_codex_exe()
        if exe and not Path(exe).is_file():
            exe = None
        if not exe:
            message_warn(self, "提示", "未找到 codex 命令，请先安装（可用 npm prefix -g 查看全局目录）")
            return
        suffix = Path(exe).suffix.lower()
        if suffix in (".cmd", ".bat"):
            cmd = self._cmd_quote(str(exe))
            ok = self._open_terminal_command(cmd, cwd=workspace, shell="cmd")
        else:
            cmd = f"& {self._ps_quote(str(exe))}"
            ok = self._open_terminal_command(cmd, cwd=workspace)
        if not ok:
            message_error(self, "失败", "无法启动终端，请手动运行 codex")

    def _cmd_quote(self, value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def _ps_quote(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _build_ps_command(self, command: str, cwd: Optional[Path]) -> str:
        if cwd:
            return f"Set-Location -LiteralPath {self._ps_quote(str(cwd))}; {command}"
        return command

    def _find_windows_terminal(self) -> Optional[str]:
        return shutil.which("wt") or shutil.which("wt.exe")

    def _open_terminal_command(self, command: str, cwd: Optional[Path] = None, shell: str = "powershell") -> bool:
        if shell == "cmd":
            return self._open_cmd_terminal(command, cwd)
        ps_command = self._build_ps_command(command, cwd)
        wt_exe = self._find_windows_terminal()
        if wt_exe:
            args = [wt_exe]
            if cwd:
                args += ["-d", str(cwd)]
            args += ["powershell", "-NoExit", "-Command", ps_command]
            try:
                subprocess.Popen(args)
                return True
            except Exception:
                return False
        ps_exe = shutil.which("powershell") or shutil.which("powershell.exe")
        if not ps_exe:
            return False
        args = [ps_exe, "-NoExit", "-Command", ps_command]
        try:
            creationflags = 0x00000010 if os.name == "nt" else 0
            subprocess.Popen(args, creationflags=creationflags)
            return True
        except Exception:
            return False

    def _open_cmd_terminal(self, command: str, cwd: Optional[Path] = None) -> bool:
        wt_exe = self._find_windows_terminal()
        if wt_exe:
            args = [wt_exe]
            if cwd:
                args += ["-d", str(cwd)]
            args += ["cmd", "/k", command]
            try:
                subprocess.Popen(args)
                return True
            except Exception:
                return False
        cmd_exe = shutil.which("cmd") or shutil.which("cmd.exe")
        if not cmd_exe:
            return False
        args = [cmd_exe, "/k", command]
        try:
            creationflags = 0x00000010 if os.name == "nt" else 0
            subprocess.Popen(args, creationflags=creationflags)
            return True
        except Exception:
            return False

    def _get_npm_prefix_global(self) -> Optional[Path]:
        npm_exe = shutil.which("npm") or shutil.which("npm.cmd") or shutil.which("npm.exe")
        if not npm_exe:
            return None
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run([npm_exe, "prefix", "-g"], capture_output=True, text=True, timeout=5, creationflags=creationflags)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        prefix = (proc.stdout or "").strip()
        if not prefix:
            return None
        return Path(prefix)

    def _find_codex_in_npm_prefix(self) -> Optional[str]:
        prefix = self._get_npm_prefix_global()
        if not prefix:
            return None
        candidates = [
            prefix / "node_modules" / ".bin" / "codex.cmd",
            prefix / "node_modules" / ".bin" / "codex.exe",
            prefix / "node_modules" / ".bin" / "codex",
            prefix / "bin" / "codex",
            prefix / "bin" / "codex.cmd",
            prefix / "codex.cmd",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def _build_search_paths(self) -> List[str]:
        paths = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm_bin = Path(appdata) / "npm"
            if npm_bin.is_dir():
                paths.insert(0, str(npm_bin))
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            npm_global = Path(userprofile) / ".npm-global" / "bin"
            if npm_global.is_dir():
                paths.insert(0, str(npm_global))
        return paths

    def _which_in_paths(self, cmd: str, paths: List[str]) -> Optional[str]:
        exts = [".exe", ".cmd", ".bat", ".ps1", ""]
        for base in paths:
            for ext in exts:
                name = cmd if cmd.lower().endswith(ext) else f"{cmd}{ext}"
                candidate = Path(base) / name
                if candidate.is_file():
                    return str(candidate)
        return None

    def _pick_best_match(self, lines: List[str]) -> Optional[str]:
        items = [line.strip() for line in lines if line.strip()]
        if not items:
            return None
        priority = [".exe", ".cmd", ".bat", ".ps1", ""]
        for ext in priority:
            for item in items:
                if ext:
                    if item.lower().endswith(ext):
                        return item
                else:
                    if Path(item).suffix == "":
                        return item
        return items[0]

    def _get_where_exe(self) -> Optional[str]:
        exe = shutil.which("where") or shutil.which("where.exe")
        if exe:
            return exe
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            candidate = Path(system_root) / "System32" / "where.exe"
            if candidate.is_file():
                return str(candidate)
        return None

    def _find_codex_exe(self) -> Optional[str]:
        exe = shutil.which("codex")
        if exe:
            return exe
        exe = self._which_in_paths("codex", self._build_search_paths())
        if exe:
            return exe
        exe = self._find_codex_in_npm_prefix()
        if exe:
            return exe
        where_exe = self._get_where_exe()
        if where_exe:
            try:
                creationflags = 0x08000000 if os.name == "nt" else 0
                proc = subprocess.run([where_exe, "codex"], capture_output=True, text=True, timeout=2, creationflags=creationflags)
                if proc.returncode == 0:
                    lines = (proc.stdout or "").splitlines()
                    best = self._pick_best_match(lines)
                    if best:
                        return best
            except Exception:
                return None
        return None

    def _run_where(self) -> str:
        where_exe = self._get_where_exe()
        if not where_exe:
            return "where.exe not found"
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run([where_exe, "codex"], capture_output=True, text=True, timeout=3, creationflags=creationflags)
            out = (proc.stdout or "").strip() or "-"
            err = (proc.stderr or "").strip() or "-"
            return f"exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"
        except Exception as exc:
            return f"error: {exc}"

    def _build_debug_report(self) -> str:
        lines = []
        lines.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Executable: {sys.executable}")
        lines.append(f"Frozen: {getattr(sys, 'frozen', False)}")
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            lines.append(f"_MEIPASS: {meipass}")
        lines.append(f"CWD: {os.getcwd()}")
        lines.append(f"OS: {os.name} / {sys.platform}")
        lines.append("")
        env_keys = ["APPDATA", "LOCALAPPDATA", "USERPROFILE", "SystemRoot", "WINDIR", "PATHEXT"]
        for key in env_keys:
            lines.append(f"{key}={os.environ.get(key, '')}")
        lines.append("")
        lines.append("PATH entries:")
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if p:
                lines.append(f"  {p}")
        lines.append("")
        lines.append("Search paths:")
        for p in self._build_search_paths():
            lines.append(f"  {p}")
        lines.append("")
        lines.append(f"shutil.which('codex'): {shutil.which('codex') or '-'}")
        lines.append(f"find_codex_exe(): {self._find_codex_exe() or '-'}")
        lines.append(f"where.exe: {self._get_where_exe() or '-'}")
        lines.append("where codex:")
        lines.append(self._run_where())
        return "\n".join(lines)

    def _update_debug(self) -> None:
        if hasattr(self, "debug_text"):
            self.debug_text.setPlainText(self._build_debug_report())

    def copy_debug(self) -> None:
        if hasattr(self, "debug_text"):
            QtWidgets.QApplication.clipboard().setText(self.debug_text.toPlainText())
            message_info(self, "提示", "诊断信息已复制")


    def _get_local_version(self):
        exe = self._find_codex_exe()
        if not exe:
            return False, "-", "-", "未找到 codex 命令"
        cmd = [exe, "--version"]
        if exe.lower().endswith(".ps1"):
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, "--version"]
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=creationflags)
        except Exception as exc:
            return True, "未知", exe, f"{exc}"
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        text = out or err
        version = self._extract_semver(text) or (text if text else "未知")
        if proc.returncode != 0 and not text:
            return True, "未知", exe, f"exit={proc.returncode}"
        if proc.returncode != 0 and text:
            return True, version, exe, f"exit={proc.returncode}"
        return True, version, exe, ""

    def _get_latest_version(self):
        try:
            req = urllib_request.Request(
                "https://api.github.com/repos/openai/codex/releases/latest",
                headers={"User-Agent": "CodexSwitcher"},
            )
            with urllib_request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name") or data.get("name") or "未知"
            ver = self._extract_semver(tag) or tag
            return True, ver, ""
        except urllib_error.URLError:
            return False, "-", "网络不可用或无法访问 GitHub，请检查网络/代理后重试"
        except Exception as exc:
            return False, "-", str(exc)

    def _extract_semver(self, text: str) -> Optional[str]:
        match = re.search(r"\d+\.\d+\.\d+", text)
        return match.group(0) if match else None

    def _compare_versions(self, local: Optional[str], latest: Optional[str]) -> str:
        local_sem = self._extract_semver(local or "")
        latest_sem = self._extract_semver(latest or "")
        if not local_sem or not latest_sem:
            return ""
        if local_sem == latest_sem:
            return "已是最新版本，无需更新。"
        try:
            local_parts = tuple(int(p) for p in local_sem.split("."))
            latest_parts = tuple(int(p) for p in latest_sem.split("."))
            if local_parts > latest_parts:
                return f"本地版本 {local_sem} 高于最新 {latest_sem}。"
        except Exception:
            pass
        return f"检测到新版本：{latest_sem}，可更新。"

class ConfigTomlPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.current_path: Optional[Path] = None
        self._raw_json: Optional[Dict[str, object]] = None
        self._raw_text: str = ""

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("config.toml")
        header.setFont(self._header_font())
        layout.addWidget(header)

        info_group = QtWidgets.QGroupBox("文件信息")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QVBoxLayout(info_group)
        self.codex_path_label = QtWidgets.QLabel("Codex 路径：-")
        self.config_path_label = QtWidgets.QLabel("config.toml 路径：-")
        self.path_hint_label = QtWidgets.QLabel("")
        self.path_hint_label.hide()
        info_layout.addWidget(self.codex_path_label)
        info_layout.addWidget(self.config_path_label)
        info_layout.addWidget(self.path_hint_label)
        layout.addWidget(info_group)

        action_row = QtWidgets.QHBoxLayout()
        self.reload_btn = QtWidgets.QPushButton("重新读取")
        self.reload_btn.clicked.connect(self.refresh_content)
        self.open_folder_btn = QtWidgets.QPushButton("打开所在文件夹")
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.clicked.connect(self.save_content)
        action_row.addWidget(self.reload_btn)
        action_row.addWidget(self.open_folder_btn)
        action_row.addWidget(self.save_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        content_group = QtWidgets.QGroupBox("内容（可手动修改）")
        apply_white_shadow(content_group)
        content_layout = QtWidgets.QVBoxLayout(content_group)
        self.editor = QtWidgets.QPlainTextEdit()
        content_layout.addWidget(self.editor)
        layout.addWidget(content_group, 1)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        self.refresh_content()

    def _infer_userprofile_from_exe(self, exe_path: str) -> Optional[Path]:
        try:
            parts = Path(exe_path).parts
        except Exception:
            return None
        for i, part in enumerate(parts):
            if part.lower() in ("users", "home") and i + 1 < len(parts):
                return Path(*parts[: i + 2])
        return None

    def _compute_config_path(self) -> tuple[Optional[Path], str, Optional[str]]:
        exe_path = self.state.codex_path
        if not exe_path:
            return None, "未检测到本机 codex 路径，请先在“Codex 状态”页刷新检测", None
        userprofile = self._infer_userprofile_from_exe(exe_path)
        if userprofile:
            hint = f"根据 codex 路径推断用户目录：{userprofile}"
        else:
            env_profile = os.environ.get("USERPROFILE")
            if env_profile:
                userprofile = Path(env_profile)
                hint = f"未能从 codex 路径推断用户目录，使用 USERPROFILE：{userprofile}"
            else:
                userprofile = Path.home()
                hint = f"未能从 codex 路径推断用户目录，使用 Path.home()：{userprofile}"
        config_path = userprofile / ".codex" / "config.toml"
        return config_path, hint, exe_path

    def _resolve_config_path(self) -> tuple[Optional[Path], str]:
        config_path, hint, exe_path = self._compute_config_path()
        if exe_path:
            self.codex_path_label.setText(f"Codex 路径：{exe_path}")
        return config_path, hint

    def refresh_content(self) -> None:
        self._config_refresh_token = getattr(self, "_config_refresh_token", 0) + 1
        token = self._config_refresh_token
        self.config_path_label.setText("config.toml 路径：加载中...")
        self.path_hint_label.setText("")
        self.editor.setPlainText("")
        self.save_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self.status_label.setText("加载中...")

        def worker() -> None:
            config_path, hint, exe_path = self._compute_config_path()
            content = None
            read_error = None
            exists = False
            if config_path:
                exists = config_path.exists()
                if exists:
                    try:
                        content = config_path.read_text(encoding="utf-8")
                    except Exception as exc:
                        read_error = str(exc)

            def apply() -> None:
                if getattr(self, "_config_refresh_token", 0) != token:
                    return
                if exe_path:
                    self.codex_path_label.setText(f"Codex 路径：{exe_path}")
                if not config_path:
                    self.current_path = None
                    self.config_path_label.setText("config.toml 路径：-")
                    self.path_hint_label.setText(hint)
                    self.editor.setPlainText("")
                    self.save_btn.setEnabled(False)
                    self.open_folder_btn.setEnabled(False)
                    self.status_label.setText(hint)
                    return
                self.current_path = config_path
                self.config_path_label.setText(f"config.toml 路径：{config_path}")
                self.path_hint_label.setText(hint)
                self.save_btn.setEnabled(True)
                self.open_folder_btn.setEnabled(True)
                if exists:
                    if read_error:
                        self.editor.setPlainText("")
                        self.status_label.setText(f"读取失败：{read_error}")
                    else:
                        self.editor.setPlainText(content or "")
                        self.status_label.setText("读取完成")
                else:
                    self.editor.setPlainText("")
                    self.status_label.setText("文件不存在，将在保存时创建")

            run_in_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def open_folder(self) -> None:
        config_path, hint = self._resolve_config_path()
        if not config_path:
            message_warn(self, "提示", hint)
            return
        folder = config_path.parent
        if not folder.exists():
            message_warn(self, "提示", f"目录不存在：{folder}")
            return
        try:
            os.startfile(str(folder))
        except Exception as exc:
            message_error(self, "失败", str(exc))


    def save_content(self) -> None:
        config_path, hint = self._resolve_config_path()
        if not config_path:
            message_warn(self, "提示", hint)
            return
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = self.editor.toPlainText()
            config_path.write_text(content, encoding="utf-8")
            self.status_label.setText(f"已保存：{config_path}")
        except Exception as exc:
            message_error(self, "失败", str(exc))
            self.status_label.setText(f"保存失败：{exc}")


class OpencodeConfigPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.account_items: List[Dict[str, str]] = []
        self.account_map: List[Dict[str, str]] = []
        self.current_path: Optional[Path] = None

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("opencode 配置")
        header.setFont(self._header_font())
        layout.addWidget(header)

        info_group = QtWidgets.QGroupBox("文件信息")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QVBoxLayout(info_group)
        self.opencode_status_label = QtWidgets.QLabel("opencode 状态：检测中...")
        self.opencode_path_label = QtWidgets.QLabel("opencode 路径：-")
        self.opencode_version_label = QtWidgets.QLabel("当前本地版本：-")
        self.opencode_latest_label = QtWidgets.QLabel("npm 最新版本：-")
        self.opencode_hint_label = QtWidgets.QLabel("安装方法：npm i -g opencode-ai")
        self.config_path_label = QtWidgets.QLabel("opencode.json 路径：-")
        self.path_hint_label = QtWidgets.QLabel("")
        self.path_hint_label.hide()
        info_layout.addWidget(self.opencode_status_label)
        info_layout.addWidget(self.opencode_path_label)
        info_layout.addWidget(self.opencode_version_label)
        info_layout.addWidget(self.opencode_latest_label)
        info_layout.addWidget(self.opencode_hint_label)
        info_layout.addWidget(self.config_path_label)
        info_layout.addWidget(self.path_hint_label)
        layout.addWidget(info_group)

        account_group = QtWidgets.QGroupBox("账号来源")
        apply_white_shadow(account_group)
        account_layout = QtWidgets.QHBoxLayout(account_group)
        self.account_combo = QtWidgets.QComboBox()
        self.account_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.account_combo.setMinimumWidth(520)
        self.account_combo.view().setMinimumWidth(640)
        self.refresh_accounts_btn = QtWidgets.QPushButton("刷新账号")
        self.refresh_accounts_btn.clicked.connect(self.refresh_accounts)
        account_layout.addWidget(QtWidgets.QLabel("选择账号"))
        account_layout.addWidget(self.account_combo)
        account_layout.addWidget(self.refresh_accounts_btn)
        account_layout.addStretch(1)
        layout.addWidget(account_group)

        action_row = QtWidgets.QHBoxLayout()
        self.apply_account_btn = QtWidgets.QPushButton("应用账号到 opencode.json")
        self.apply_account_btn.clicked.connect(self.apply_account_to_editor)
        self.reload_btn = QtWidgets.QPushButton("重新读取")
        self.reload_btn.clicked.connect(self.refresh_content)
        self.open_folder_btn = QtWidgets.QPushButton("打开所在文件夹")
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.save_btn = QtWidgets.QPushButton("保存")
        self.save_btn.clicked.connect(self.save_content)
        action_row.addWidget(self.apply_account_btn)
        action_row.addWidget(self.reload_btn)
        action_row.addWidget(self.open_folder_btn)
        action_row.addWidget(self.save_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        content_group = QtWidgets.QGroupBox("opencode.json内容（可手动修改）")
        apply_white_shadow(content_group)
        content_layout = QtWidgets.QVBoxLayout(content_group)
        self.editor = QtWidgets.QPlainTextEdit()
        content_layout.addWidget(self.editor)
        layout.addWidget(content_group, 1)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        self.refresh_accounts()
        self.refresh_content()

    def _get_config_path(self) -> Path:
        return Path.home() / ".config" / "opencode" / "opencode.json"

    def _account_kind(self, account: Dict[str, str]) -> str:
        if account.get("is_team") == "1" or account.get("account_type") == "team":
            return "Team"
        if account.get("account_type") == "official":
            return "官方"
        return "中转"

    def refresh_accounts(self) -> None:
        self.account_combo.clear()
        self.account_items = build_accounts(self.state.store)
        self.account_map = []
        for item in self.account_items:
            kind = self._account_kind(item)
            label = f"[{kind}] {item.get('name', '')} | {item.get('base_url', '')}"
            self.account_combo.addItem(label)
            self.account_map.append(item)
        if not self.account_items:
            self.account_combo.addItem("暂无账号")

    def refresh_content(self) -> None:
        config_path = self._get_config_path()
        self.current_path = config_path
        self._refresh_opencode_status_async()
        self.config_path_label.setText(f"opencode.json 路径：{config_path}")
        self.save_btn.setEnabled(True)
        self.open_folder_btn.setEnabled(True)
        if config_path.exists():
            try:
                content = config_path.read_text(encoding="utf-8")
            except Exception as exc:
                self.editor.setPlainText("")
                self.status_label.setText(f"读取失败：{exc}")
                return
            self._raw_text = content
            if not content.strip():
                self._raw_json = None
                self.editor.setPlainText("")
                self.status_label.setText("opencode.json 为空，可选择账号并点击“应用账号到 opencode.json”生成模板，然后点击保存。")
                return
            raw_json = self._safe_json_load(content)
            self._raw_json = raw_json
            if raw_json is not None:
                masked = self._mask_api_keys(raw_json)
                self.editor.setPlainText(json.dumps(masked, ensure_ascii=False, indent=2))
                self.status_label.setText("读取完成")
            else:
                self.editor.setPlainText(content)
                self.status_label.setText("opencode.json 不是有效 JSON，可选择账号并点击“应用账号到 opencode.json”生成模板，然后点击保存。")
        else:
            self._raw_text = ""
            self._raw_json = None
            self.editor.setPlainText("")
            self.status_label.setText("未检测到 opencode.json，可先选择账号并点击“应用账号到 opencode.json”生成模板，然后点击保存。")
    def _find_opencode_exe(self) -> Optional[str]:
        paths = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        ext_order = [".cmd", ".ps1", ".bat", ".exe", ""]
        for ext in ext_order:
            name = "opencode" if ext == "" else f"opencode{ext}"
            for base in paths:
                candidate = Path(base) / name
                if candidate.is_file():
                    return str(candidate)
        return shutil.which("opencode")
    def _extract_semver(self, text: str) -> Optional[str]:
        match = re.search(r"\d+\.\d+\.\d+", text)
        return match.group(0) if match else None

    def _get_opencode_local_version(self, exe: str) -> str:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            exe_lower = exe.lower()
            if exe_lower.endswith(".ps1"):
                cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, "--version"]
            elif exe_lower.endswith(".cmd") or exe_lower.endswith(".bat"):
                cmd = ["cmd", "/c", exe, "--version"]
            else:
                cmd = [exe, "--version"]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
                startupinfo=startupinfo,
                stdin=subprocess.DEVNULL,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            text = out or err
            return self._extract_semver(text) or (text if text else "未知")
        except Exception:
            return "未知"
    def _get_latest_opencode_version(self) -> tuple[bool, str]:
        try:
            req = urllib_request.Request(
                "https://registry.npmjs.org/opencode-ai/latest",
                headers={"User-Agent": "CodexSwitcher"},
            )
            with urllib_request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ver = data.get("version") or "未知"
            return True, ver
        except urllib_error.URLError:
            return False, "网络不可用或无法访问 npm"
        except Exception as exc:
            return False, str(exc)

    def _refresh_opencode_status_async(self) -> None:
        self._opencode_refresh_token = getattr(self, "_opencode_refresh_token", 0) + 1
        token = self._opencode_refresh_token
        self.opencode_status_label.setText("opencode 状态：检测中...")
        self.opencode_path_label.setText("opencode 路径：-")
        self.opencode_version_label.setText("当前本地版本：-")
        self.opencode_latest_label.setText("npm 最新版本：-")
        self.opencode_hint_label.setText("安装方法：npm i -g opencode-ai")

        def worker() -> None:
            try:
                exe = self._find_opencode_exe()
                local_ver = self._get_opencode_local_version(exe) if exe else "-"
            except Exception:
                exe = None
                local_ver = "未知"

            def apply_local() -> None:
                if getattr(self, "_opencode_refresh_token", 0) != token:
                    return
                if exe:
                    self.opencode_status_label.setText("opencode 状态：已安装")
                    self.opencode_path_label.setText(f"opencode 路径：{exe}")
                    self.opencode_version_label.setText(f"当前本地版本：{local_ver}")
                else:
                    self.opencode_status_label.setText("opencode 状态：未安装")
                    self.opencode_path_label.setText("opencode 路径：-")
                    self.opencode_version_label.setText("当前本地版本：-")

            run_in_ui(apply_local)

            try:
                ok, latest = self._get_latest_opencode_version()
            except Exception as exc:
                ok, latest = False, str(exc)

            def apply_latest() -> None:
                if getattr(self, "_opencode_refresh_token", 0) != token:
                    return
                if ok:
                    self.opencode_latest_label.setText(f"npm 最新版本：{latest}")
                else:
                    self.opencode_latest_label.setText(f"npm 最新版本：{latest}")

            run_in_ui(apply_latest)

        threading.Thread(target=worker, daemon=True).start()
    def _mask_api_keys(self, obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "apiKey" and isinstance(v, str) and v:
                    out[k] = "****"
                else:
                    out[k] = self._mask_api_keys(v)
            return out
        if isinstance(obj, list):
            return [self._mask_api_keys(i) for i in obj]
        return obj

    def _restore_api_keys(self, obj, raw):
        if raw is None:
            return obj
        if isinstance(obj, dict) and isinstance(raw, dict):
            out = {}
            for k, v in obj.items():
                rv = raw.get(k) if isinstance(raw, dict) else None
                if k == "apiKey" and isinstance(v, str) and set(v) == {"*"}:
                    out[k] = rv if isinstance(rv, str) else v
                else:
                    out[k] = self._restore_api_keys(v, rv)
            return out
        if isinstance(obj, list) and isinstance(raw, list):
            return [self._restore_api_keys(v, raw[i] if i < len(raw) else None) for i, v in enumerate(obj)]
        return obj

    def _safe_json_load(self, text: str) -> Optional[Dict[str, object]]:
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _build_opencode_config(self, account: Dict[str, str]) -> Dict[str, object]:
        name = account.get("name", "xyai") or "xyai"
        base_url = account.get("base_url", "")
        api_key = account.get("api_key", "")
        config = {
            "provider": {
                name: {
                    "name": name,
                    "npm": "@ai-sdk/openai",
                    "models": {
                        "gpt-5.2": {"name": "gpt-5.2"},
                        "gpt-5.2-codex": {"name": "gpt-5.2-codex"},
                    },
                    "options": {
                        "apiKey": api_key,
                        "baseURL": base_url,
                        "options": {
                            "reasoningEffort": "high",
                            "textVerbosity": "low",
                            "reasoningSummary": "auto",
                        },
                        "setCacheKey": True,
                    },
                }
            },
            "$schema": "https://opencode.ai/config.json",
        }
        return config
    def _update_config_with_account(self, raw: Optional[Dict[str, object]], account: Dict[str, str]) -> Dict[str, object]:
        name = account.get("name", "xyai") or "xyai"
        base_url = account.get("base_url", "")
        api_key = account.get("api_key", "")
        if not isinstance(raw, dict):
            return self._build_opencode_config(account)
        provider = raw.get("provider")
        if not isinstance(provider, dict) or not provider:
            raw["provider"] = self._build_opencode_config(account).get("provider", {})
            raw.setdefault("$schema", "https://opencode.ai/config.json")
            return raw
        key = name if name in provider else next(iter(provider.keys()))
        entry = provider.get(key)
        if not isinstance(entry, dict):
            entry = {}
        entry = dict(entry)
        entry["name"] = name
        entry.setdefault("npm", "@ai-sdk/openai")
        options = entry.get("options")
        if not isinstance(options, dict):
            options = {}
        options["apiKey"] = api_key
        options["baseURL"] = base_url
        entry["options"] = options
        provider[key] = entry
        raw["provider"] = provider
        raw.setdefault("$schema", "https://opencode.ai/config.json")
        return raw


    def apply_account_to_editor(self) -> None:
        if not self.account_map:
            message_warn(self, "提示", "当前没有可用账号")
            return
        idx = self.account_combo.currentIndex()
        if idx < 0 or idx >= len(self.account_map):
            message_warn(self, "提示", "请选择账号")
            return
        current_text = self.editor.toPlainText()
        raw_current = self._safe_json_load(current_text)
        if raw_current is None:
            raw_current = self._raw_json
        raw_updated = self._update_config_with_account(raw_current, self.account_map[idx])
        self._raw_json = raw_updated
        masked = self._mask_api_keys(raw_updated)
        self.editor.setPlainText(json.dumps(masked, ensure_ascii=False, indent=2))
        self.status_label.setText("已应用账号，点击保存写入文件")

    def open_folder(self) -> None:
        config_path = self._get_config_path()
        folder = config_path.parent
        if not folder.exists():
            message_warn(self, "提示", f"目录不存在：{folder}")
            return
        try:
            os.startfile(str(folder))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def save_content(self) -> None:
        config_path = self._get_config_path()
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = self.editor.toPlainText()
            data = self._safe_json_load(content)
            if data is None:
                message_error(self, "失败", "JSON 解析失败，请检查格式")
                return
            data = self._restore_api_keys(data, self._raw_json)
            config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText(f"已保存：{config_path}")
        except Exception as exc:
            message_error(self, "失败", str(exc))
            self.status_label.setText(f"保存失败：{exc}")




class SkillsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.skill_items: List[Dict[str, object]] = []

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("Skill 管理")
        header.setFont(self._header_font())
        layout.addWidget(header)

        action_row = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("刷新列表")
        self.refresh_btn.clicked.connect(self.refresh_list)
        self.import_btn = QtWidgets.QPushButton("导入 Skill")
        self.import_btn.clicked.connect(self.import_skill)
        self.backup_btn = QtWidgets.QPushButton("备份技能")
        self.backup_btn.clicked.connect(self.backup_skills)
        self.open_backup_btn = QtWidgets.QPushButton("打开备份目录")
        self.open_backup_btn.clicked.connect(self.open_backup_root)
        self.open_root_btn = QtWidgets.QPushButton("打开技能目录")
        self.open_root_btn.clicked.connect(self.open_skills_root)
        action_row.addWidget(self.refresh_btn)
        action_row.addWidget(self.import_btn)
        action_row.addWidget(self.open_root_btn)
        action_row.addWidget(self.backup_btn)
        action_row.addWidget(self.open_backup_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        body = QtWidgets.QHBoxLayout()
        layout.addLayout(body, 1)

        list_group = QtWidgets.QGroupBox("Skill 列表")
        apply_white_shadow(list_group)
        list_layout = QtWidgets.QVBoxLayout(list_group)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setMinimumWidth(260)
        self.list_widget.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        self.list_widget.setTextElideMode(QtCore.Qt.ElideRight)
        self.list_widget.currentRowChanged.connect(self.on_select)
        list_layout.addWidget(self.list_widget)
        body.addWidget(list_group, 1)

        detail_group = QtWidgets.QGroupBox("Skill 详情")
        apply_white_shadow(detail_group)
        detail_layout = QtWidgets.QVBoxLayout(detail_group)

        info_group = QtWidgets.QGroupBox("基本信息")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QFormLayout(info_group)
        self.name_label = QtWidgets.QLabel("-")
        self.desc_label = QtWidgets.QLabel("-")
        self.desc_label.setWordWrap(True)
        self.source_label = QtWidgets.QLabel("-")
        self.path_label = QtWidgets.QLabel("-")
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        info_layout.addRow("名称", self.name_label)
        info_layout.addRow("描述", self.desc_label)
        info_layout.addRow("来源", self.source_label)
        info_layout.addRow("路径", self.path_label)
        detail_layout.addWidget(info_group)

        readme_group = QtWidgets.QGroupBox("使用说明")
        apply_white_shadow(readme_group)
        readme_layout = QtWidgets.QVBoxLayout(readme_group)
        self.readme_text = QtWidgets.QPlainTextEdit()
        self.readme_text.setReadOnly(True)
        self.readme_text.setMinimumHeight(180)
        readme_layout.addWidget(self.readme_text)
        detail_layout.addWidget(readme_group, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.open_btn = QtWidgets.QPushButton("打开所在目录")
        self.open_btn.clicked.connect(self.open_selected_folder)
        self.remove_btn = QtWidgets.QPushButton("删除 Skill")
        self.remove_btn.clicked.connect(self.remove_skill)
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch(1)
        detail_layout.addLayout(btn_row)

        body.addWidget(detail_group, 2)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        self.refresh_list()

    def _skills_root(self) -> Path:
        return Path.home() / ".codex" / "skills"


    def _extract_title_desc(self, text: str, fallback: str) -> tuple[str, str]:
        name = ""
        desc = ""
        lines = text.splitlines()

        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                stripped = line.strip()
                if stripped == "---":
                    break
                if ":" not in stripped:
                    continue
                key, value = stripped.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "name" and value:
                    name = value
                elif key == "description" and value:
                    desc = value

        if not name or not desc:
            for line in lines[:30]:
                stripped = line.strip()
                if not stripped:
                    continue
                lower = stripped.lower()
                if not name and lower.startswith("name:"):
                    name = stripped.split(":", 1)[1].strip()
                    continue
                if not desc and lower.startswith("description:"):
                    desc = stripped.split(":", 1)[1].strip()
                    continue
                if name and desc:
                    break

        if not name:
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#"):
                    name = stripped.lstrip("#").strip()
                    break

        if not desc:
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped == "---":
                    continue
                lower = stripped.lower()
                if lower.startswith("name:") or lower.startswith("description:"):
                    continue
                if stripped.startswith("#"):
                    continue
                desc = stripped
                break

        if not name:
            name = fallback
        if not desc:
            desc = "无描述"
        return name, desc

    def _build_skill_item(self, path: Path, source: str) -> Dict[str, object]:
        skill_md = path / "SKILL.md"
        title = path.name
        desc = "无描述"
        has_doc = False
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8", errors="ignore")
                title, desc = self._extract_title_desc(content, path.name)
                has_doc = True
            except Exception:
                has_doc = False
        return {
            "name": title,
            "desc": desc,
            "path": path,
            "source": source,
            "has_doc": has_doc,
        }

    def _find_skill_dirs(self, base: Path) -> List[Path]:
        results: List[Path] = []
        try:
            for root, _dirs, files in os.walk(base):
                if "SKILL.md" in files:
                    results.append(Path(root))
        except Exception:
            return results
        return results

    def refresh_list(self) -> None:
        self.list_widget.clear()
        self.skill_items = []
        root = self._skills_root()
        if not root.exists():
            self.status_label.setText(f"技能目录不存在：{root}")
            self._reset_detail()
            return

        system_dir = root / ".system"
        if system_dir.exists():
            for entry in sorted(system_dir.iterdir()):
                if entry.is_dir():
                    self.skill_items.append(self._build_skill_item(entry, "系统"))

        user_dir = root / "user"
        if user_dir.exists():
            for entry in self._find_skill_dirs(user_dir):
                self.skill_items.append(self._build_skill_item(entry, "用户"))

        for entry in sorted(root.iterdir()):
            if entry.is_dir() and entry.name not in (".system", "user"):
                self.skill_items.append(self._build_skill_item(entry, "本地"))

        for item in self.skill_items:
            label = f"[{item['source']}] {item['name']}"
            if item.get("desc"):
                label = f"{label} - {item['desc']}"
            list_item = QtWidgets.QListWidgetItem(label)
            list_item.setData(QtCore.Qt.UserRole, item)
            self.list_widget.addItem(list_item)

        if not self.skill_items:
            self.status_label.setText("未发现任何技能")
            self._reset_detail()
        else:
            self.status_label.setText(f"共 {len(self.skill_items)} 个技能")
            self.list_widget.setCurrentRow(0)

    def _reset_detail(self) -> None:
        self.name_label.setText("-")
        self.desc_label.setText("-")
        self.source_label.setText("-")
        self.path_label.setText("-")
        self.readme_text.setPlainText("")
        self.remove_btn.setEnabled(False)
        self.open_btn.setEnabled(False)

    def on_select(self, row: int) -> None:
        if row < 0 or row >= self.list_widget.count():
            self._reset_detail()
            return
        item = self.list_widget.item(row)
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, dict):
            self._reset_detail()
            return
        name = str(data.get("name", "-"))
        desc = str(data.get("desc", "-"))
        source = str(data.get("source", "-"))
        path = data.get("path")
        self.name_label.setText(name)
        self.desc_label.setText(desc)
        self.source_label.setText(source)
        self.path_label.setText(str(path) if path else "-")

        self.open_btn.setEnabled(bool(path))
        self.remove_btn.setEnabled(source != "系统")

        readme = ""
        if isinstance(path, Path):
            skill_md = path / "SKILL.md"
            if skill_md.exists():
                try:
                    readme = skill_md.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    readme = "读取 SKILL.md 失败"
            else:
                readme = "未找到 SKILL.md"
        self.readme_text.setPlainText(readme)


    def _backup_base_dir(self) -> Path:
        return self._skills_root().parent

    def _generate_backup_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._backup_base_dir() / f"skills_backup_{stamp}"

    def _prune_backups(self, keep: int = 5) -> None:
        base = self._backup_base_dir()
        if not base.exists():
            return
        backups = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("skills_backup_")]
        backups.sort(key=lambda p: p.name)
        if len(backups) <= keep:
            return
        for old in backups[:-keep]:
            try:
                shutil.rmtree(old)
            except Exception:
                continue

    def backup_skills(self) -> None:
        root = self._skills_root()
        if not root.exists():
            message_warn(self, "提示", f"技能目录不存在：{root}")
            return
        backup_dir = self._generate_backup_dir()
        try:
            shutil.copytree(root, backup_dir)
            self._prune_backups()
            self.status_label.setText(f"已备份：{backup_dir}")
        except Exception as exc:
            message_error(self, "失败", str(exc))


    def open_backup_root(self) -> None:
        base = self._backup_base_dir()
        if not base.exists():
            message_warn(self, "提示", f"备份目录不存在：{base}")
            return
        try:
            os.startfile(str(base))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def open_skills_root(self) -> None:
        root = self._skills_root()
        if not root.exists():
            message_warn(self, "提示", f"技能目录不存在：{root}")
            return
        try:
            os.startfile(str(root))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def open_selected_folder(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        item = self.list_widget.item(row)
        data = item.data(QtCore.Qt.UserRole)
        path = data.get("path") if isinstance(data, dict) else None
        if not isinstance(path, Path):
            message_warn(self, "提示", "未找到技能目录")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def import_skill(self) -> None:
        root = self._skills_root()
        root.mkdir(parents=True, exist_ok=True)
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择技能目录")
        if not folder:
            return
        src = Path(folder)
        if not src.exists() or not src.is_dir():
            message_warn(self, "提示", "选择的目录无效")
            return
        dest = root / src.name
        if dest.exists():
            message_warn(self, "提示", f"目标已存在：{dest.name}")
            return
        try:
            shutil.copytree(src, dest)
            self.status_label.setText(f"已导入技能：{dest.name}")
            self.refresh_list()
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def remove_skill(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        item = self.list_widget.item(row)
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, dict):
            return
        source = data.get("source")
        path = data.get("path")
        name = data.get("name", "")
        if source == "系统":
            message_warn(self, "提示", "系统技能不允许删除")
            return
        if not isinstance(path, Path):
            message_warn(self, "提示", "未找到技能目录")
            return
        ok = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定删除技能 “{name}” 吗？该操作不可恢复。",
        )
        if ok != QtWidgets.QMessageBox.Yes:
            return
        try:
            shutil.rmtree(path)
            self.status_label.setText(f"已删除技能：{name}")
            self.refresh_list()
        except Exception as exc:
            message_error(self, "失败", str(exc))



class VSCodePluginPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.extension_items: List[Dict[str, object]] = []
        self._marketplace_meta: Optional[Dict[str, object]] = None
        self._index_path: Optional[Path] = None
        self._backup_dir: Optional[Path] = None
        self._workspace_dir: Optional[Path] = None
        self._vscode_install_dir: Optional[Path] = None
        if isinstance(self.state.vscode_install_dir, str) and self.state.vscode_install_dir:
            self._vscode_install_dir = Path(self.state.vscode_install_dir)

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("VS Code 插件")
        header.setFont(self._header_font())
        layout.addWidget(header)

        launch_group = QtWidgets.QGroupBox("VS Code Codex 启动")
        apply_white_shadow(launch_group)
        launch_layout = QtWidgets.QVBoxLayout(launch_group)

        vscode_row = QtWidgets.QHBoxLayout()
        vscode_caption = QtWidgets.QLabel("VS Code 安装目录")
        self.vscode_path_edit = QtWidgets.QLineEdit()
        self.vscode_path_edit.setReadOnly(True)
        self.vscode_path_edit.setClearButtonEnabled(False)
        self.vscode_path_edit.setMinimumHeight(32)
        self.vscode_path_edit.setStyleSheet(
            "QLineEdit {"
            "border: 1px solid #8ea6ff;"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            "background: #ffffff;"
            "}"
            "QLineEdit:read-only {"
            "background: #f7f9ff;"
            "}"
        )
        self.pick_vscode_btn = QtWidgets.QPushButton("选择目录")
        self.pick_vscode_btn.clicked.connect(self.pick_vscode_install_dir)
        vscode_row.addWidget(vscode_caption)
        vscode_row.addWidget(self.vscode_path_edit, 1)
        vscode_row.addWidget(self.pick_vscode_btn)
        launch_layout.addLayout(vscode_row)

        workspace_row = QtWidgets.QHBoxLayout()
        workspace_caption = QtWidgets.QLabel("工作区")
        self.workspace_path_edit = QtWidgets.QLineEdit()
        self.workspace_path_edit.setReadOnly(True)
        self.workspace_path_edit.setClearButtonEnabled(False)
        self.workspace_path_edit.setMinimumHeight(32)
        self.workspace_path_edit.setStyleSheet(
            "QLineEdit {"
            "border: 1px solid #8ea6ff;"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            "background: #ffffff;"
            "}"
            "QLineEdit:read-only {"
            "background: #f7f9ff;"
            "}"
        )
        self.pick_workspace_btn = QtWidgets.QPushButton("选择工作区")
        self.pick_workspace_btn.clicked.connect(self.pick_workspace)
        workspace_row.addWidget(workspace_caption)
        workspace_row.addWidget(self.workspace_path_edit, 1)
        workspace_row.addWidget(self.pick_workspace_btn)
        launch_layout.addLayout(workspace_row)

        launch_btn_row = QtWidgets.QHBoxLayout()
        self.launch_vscode_btn = QtWidgets.QPushButton("一键启动 VS Code")
        self.launch_vscode_btn.clicked.connect(self.launch_vscode)
        self.fix_webview_btn = QtWidgets.QPushButton("WebView错误修改")
        self.fix_webview_btn.clicked.connect(self.fix_webview_issue)
        launch_btn_row.addWidget(self.launch_vscode_btn)
        launch_btn_row.addWidget(self.fix_webview_btn)
        launch_btn_row.addStretch(1)
        launch_layout.addLayout(launch_btn_row)

        layout.addWidget(launch_group)

        action_row = QtWidgets.QHBoxLayout()
        self.scan_btn = QtWidgets.QPushButton("扫描插件")
        self.scan_btn.clicked.connect(self.refresh_extensions)
        self.pick_index_btn = QtWidgets.QPushButton("选择 index 文件")
        self.pick_index_btn.clicked.connect(self.pick_index_file)
        self.open_ext_btn = QtWidgets.QPushButton("打开插件目录")
        self.open_ext_btn.clicked.connect(self.open_extension_folder)
        self.disable_update_btn = QtWidgets.QPushButton("关闭自动更新")
        self.disable_update_btn.clicked.connect(self.disable_auto_update)
        action_row.addWidget(self.scan_btn)
        action_row.addWidget(self.pick_index_btn)
        action_row.addWidget(self.open_ext_btn)
        action_row.addWidget(self.disable_update_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        info_group = QtWidgets.QGroupBox("插件信息")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QFormLayout(info_group)
        self.ext_combo = QtWidgets.QComboBox()
        self.ext_combo.currentIndexChanged.connect(self.on_extension_changed)
        self.ext_path_label = QtWidgets.QLabel("-")
        self.ext_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.index_path_label = QtWidgets.QLabel("-")
        self.index_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.index_path_label.setWordWrap(True)
        self.index_path_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.ext_version_label = QtWidgets.QLabel("-")
        self.ext_latest_label = QtWidgets.QLabel("-")
        info_layout.addRow("插件目录", self.ext_combo)
        info_layout.addRow("路径", self.ext_path_label)
        info_layout.addRow("插件版本", self.ext_version_label)
        info_layout.addRow("最新版本", self.ext_latest_label)
        info_layout.addRow("index 文件", self.index_path_label)
        layout.addWidget(info_group)

        model_group = QtWidgets.QGroupBox("增加codex vscode中可用模型")
        apply_white_shadow(model_group)
        model_layout = QtWidgets.QFormLayout(model_group)
        self.model_edit = QtWidgets.QLineEdit()
        self.model_edit.setPlaceholderText("gpt-5.3-codex, gpt-5.2-codex, gpt-5.2")
        self.model_edit.setText("gpt-5.3-codex, gpt-5.2-codex, gpt-5.2")
        self.apply_btn = QtWidgets.QPushButton("备份并增加模型")
        self.apply_btn.clicked.connect(self.apply_patch)
        self.open_backup_btn = QtWidgets.QPushButton("打开备份目录")
        self.open_backup_btn.clicked.connect(self.open_backup_dir)
        self.restore_btn = QtWidgets.QPushButton("恢复默认设置")
        self.restore_btn.clicked.connect(self.restore_backup)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.restore_btn)
        btn_row.addWidget(self.open_backup_btn)
        btn_row.addStretch(1)
        model_layout.addRow("模型名称（可多个，逗号分隔）", self.model_edit)
        model_layout.addRow("操作", btn_row)
        layout.addWidget(model_group)

        hint = QtWidgets.QLabel(
            '<span style="color:#000;font-weight:700;">修改后请重启 VS Code 或插件。</span><br>'
            '<span style="color:#666;">原理：工具会把你输入的模型加入可用模型列表，并放宽仅 ChatGPT 登录的限制，让 API Key 也能选到这些模型。</span>'
            '<ul style="margin:6px 0 0 18px; padding:0; color:#666;">'
            '<li>\u201c\u6700\u65b0\u7248\u672c\u201d\u6765\u81ea Marketplace\uff0c\u4f1a\u540c\u65f6\u663e\u793a\u7a33\u5b9a\u7248/\u9884\u89c8\u7248\uff1b\u672c\u5730\u7248\u672c\u4f1a\u7ed3\u5408\u8fdc\u7a0b\u7ed3\u679c\u6807\u6ce8\u6e20\u9053\u3002</li>'
            '<li>“恢复默认设置”会恢复最近一次备份（保留原逻辑）。</li>'
            '</ul>'
        )
        hint.setTextFormat(QtCore.Qt.RichText)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        self._refresh_vscode_install_label()
        self._refresh_workspace_label()
        self.refresh_extensions()

    def _refresh_vscode_install_label(self) -> None:
        if self._vscode_install_dir and self._vscode_install_dir.exists():
            value = str(self._vscode_install_dir)
        else:
            value = "未选择将使用vscode在win中的默认安装路径"
        self.vscode_path_edit.setText(value)
        self.vscode_path_edit.setToolTip(value)

    def _refresh_workspace_label(self) -> None:
        if self._workspace_dir and self._workspace_dir.exists():
            value = str(self._workspace_dir)
        else:
            value = "未选择工作区"
        self.workspace_path_edit.setText(value)
        self.workspace_path_edit.setToolTip(value)

    def pick_vscode_install_dir(self) -> None:
        start_dir = str(self._vscode_install_dir) if self._vscode_install_dir else ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择 VS Code 安装目录", start_dir)
        if not folder:
            return
        path = Path(folder)
        exe = self._find_vscode_exe_in_dir(path)
        if not exe:
            message_warn(self, "提示", "未在所选目录找到 Code.exe 或 Code - Insiders.exe，请选择包含 Code.exe 的安装目录")
            return
        self._vscode_install_dir = path
        self.state.vscode_install_dir = str(path)
        self.state.store["vscode_install_dir"] = str(path)
        save_store(self.state.store)
        self._refresh_vscode_install_label()

    def _find_vscode_exe_in_dir(self, root: Path) -> Optional[str]:
        candidates = [
            root / "Code.exe",
            root / "Code - Insiders.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def pick_workspace(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择工作区")
        if not folder:
            return
        path = Path(folder)
        if not path.exists() or not path.is_dir():
            message_warn(self, "提示", "选择的目录无效")
            return
        self._workspace_dir = path
        self._refresh_workspace_label()

    def _ensure_workspace(self) -> Optional[Path]:
        if not self._workspace_dir:
            message_warn(self, "提示", "请先选择工作区")
            return None
        if not self._workspace_dir.exists():
            message_warn(self, "提示", "工作区不存在")
            return None
        return self._workspace_dir

    def launch_vscode(self) -> None:
        workspace = self._ensure_workspace()
        if not workspace:
            return
        code_cli = self._find_vscode_cli()
        args = None
        if code_cli and self._vscode_supports_command(code_cli):
            args = [code_cli, "-r", str(workspace), "--command", "chatgpt.openSidebar"]
        else:
            if code_cli:
                args = [code_cli, "-r", str(workspace)]
            else:
                code_exe = self._find_vscode_exe()
                if code_exe:
                    args = [code_exe, str(workspace)]
            self._ensure_open_on_startup(workspace)
        if not args:
            message_warn(self, "提示", "未找到 VS Code，可先安装或在 PATH 中启用 code 命令")
            return
        try:
            _popen_hidden_cmd_on_windows(args)
        except Exception as exc:
            message_error(self, "失败", str(exc))
            return
        message_info(self, "提示", "已打开VS Code并自动启动codex插件，如果遇到VS Code提示“WebView视图相关错误提示”，请点击“WebView错误修改”按钮。")

    def fix_webview_issue(self) -> None:
        workspace = self._ensure_workspace()
        if not workspace:
            return

        def worker() -> None:
            self._kill_vscode_processes()
            self._clear_vscode_cache(self._vscode_install_dir)
            run_in_ui(self.launch_vscode)

        threading.Thread(target=worker, daemon=True).start()

    def _kill_vscode_processes(self) -> None:
        targets = [
            "Code.exe",
            "Code - Insiders.exe",
            "msedgewebview2.exe",
            "ServiceHub.RoslynCodeAnalysisService.exe",
            "ServiceHub.Host.Node.x64.exe",
            "ServiceHub.TestWindowStoreHost.exe",
        ]
        for name in targets:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True, text=True)
            except Exception:
                continue

    def _clear_vscode_cache(self, install_dir: Optional[Path] = None) -> None:
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        paths = []
        channel = None
        portable_user_data = None
        if install_dir:
            if (install_dir / "Code - Insiders.exe").is_file():
                channel = "insiders"
            elif (install_dir / "Code.exe").is_file():
                channel = "stable"
            portable_root = install_dir / "data" / "user-data"
            if portable_root.is_dir():
                portable_user_data = portable_root
        if portable_user_data:
            base = portable_user_data
            paths += [
                base / "WebView",
                base / "CachedData",
                base / "Cache",
                base / "GPUCache",
                base / "Local Storage",
                base / "Service Worker" / "CacheStorage",
                base / "Service Worker" / "ScriptCache",
                base / "User" / "workspaceStorage",
                base / "User" / "globalStorage",
            ]
        else:
            if channel == "stable":
                names = ["Code"]
            elif channel == "insiders":
                names = ["Code - Insiders"]
            else:
                names = ["Code", "Code - Insiders"]
            if appdata:
                base = Path(appdata)
                for name in names:
                    root = base / name
                    paths += [
                        root / "WebView",
                        root / "CachedData",
                        root / "Cache",
                        root / "GPUCache",
                        root / "Local Storage",
                        root / "Service Worker" / "CacheStorage",
                        root / "Service Worker" / "ScriptCache",
                    ]
            if local:
                base = Path(local) / "Microsoft"
                if channel == "stable":
                    local_names = ["Code"]
                elif channel == "insiders":
                    local_names = ["Code - Insiders"]
                else:
                    local_names = ["Code", "Code - Insiders"]
                for name in local_names:
                    root = base / name
                    paths += [
                        root / "User" / "workspaceStorage",
                        root / "User" / "globalStorage",
                    ]
                paths.append(Path(local) / "Temp" / "Code")
        for p in paths:
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink(missing_ok=True)
            except Exception:
                continue

    def _ensure_open_on_startup(self, workspace: Path) -> bool:
        settings_path = workspace / ".vscode" / "settings.json"
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            raw = settings_path.read_text(encoding="utf-8", errors="ignore") if settings_path.exists() else ""
            data = self._load_jsonc(raw)
            data["chatgpt.openOnStartup"] = True
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def _find_vscode_cli(self) -> Optional[str]:
        for name in ("code", "code.cmd", "code.exe", "code-insiders", "code-insiders.cmd"):
            path = shutil.which(name)
            if path:
                return path
        return None

    def _find_vscode_exe(self) -> Optional[str]:
        if self._vscode_install_dir and self._vscode_install_dir.exists():
            exe = self._find_vscode_exe_in_dir(self._vscode_install_dir)
            if exe:
                return exe
        candidates = []
        local = os.environ.get("LOCALAPPDATA")
        program = os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES")
        program_x86 = os.environ.get("ProgramFiles(x86)") or os.environ.get("PROGRAMFILES(X86)")
        if local:
            candidates.append(Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(local) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        if program:
            candidates.append(Path(program) / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(program) / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        if program_x86:
            candidates.append(Path(program_x86) / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(program_x86) / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def _vscode_supports_command(self, code_cli: str) -> bool:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run([code_cli, "--help"], capture_output=True, text=True, timeout=3, creationflags=creationflags)
        except Exception:
            return False
        output = (proc.stdout or "") + (proc.stderr or "")
        return "--command" in output

    def _extension_roots(self) -> List[Path]:
        homes: List[Path] = []
        home = Path.home()
        homes.append(home)
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            homes.append(Path(userprofile))
        homedrive = os.environ.get("HOMEDRIVE")
        homepath = os.environ.get("HOMEPATH")
        if homedrive and homepath:
            homes.append(Path(f"{homedrive}{homepath}"))

        roots: List[Path] = []
        for base in homes:
            roots.extend(
                [
                    base / ".vscode" / "extensions",
                    base / ".vscode-insiders" / "extensions",
                    base / ".vscode-oss" / "extensions",
                    base / ".cursor" / "extensions",
                ]
            )

        custom_ext = os.environ.get("VSCODE_EXTENSIONS")
        if custom_ext:
            roots.append(Path(custom_ext))

        if isinstance(self.state.vscode_install_dir, str) and self.state.vscode_install_dir:
            install_dir = Path(self.state.vscode_install_dir)
            roots.append(install_dir / "resources" / "app" / "extensions")

        uniq: List[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            if root.exists() and root.is_dir():
                uniq.append(root)
        return uniq

    def _find_extensions(self) -> List[Path]:
        results: List[Path] = []
        for root in self._extension_roots():
            try:
                entries = list(root.iterdir())
            except Exception:
                continue
            for entry in entries:
                if not entry.is_dir():
                    continue
                name = entry.name.lower()
                if name.startswith("openai.chatgpt"):
                    results.append(entry)
        uniq: List[Path] = []
        seen: set[str] = set()
        for item in results:
            key = str(item).lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        return uniq

    def _find_index_file(self, ext_path: Path) -> Optional[Path]:
        assets = ext_path / "webview" / "assets"
        if not assets.exists():
            return None
        candidates = list(assets.glob("index-*.js"))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        candidates.sort(key=lambda p: (p.stat().st_mtime, p.stat().st_size), reverse=True)
        return candidates[0]


    def _parse_extension_version(self, ext_path: Path) -> str:
        name = ext_path.name
        if "openai.chatgpt-" in name:
            return name.split("openai.chatgpt-", 1)[1] or "未知"
        if "-" in name:
            return name.rsplit("-", 1)[-1]
        return "未知"

    def _split_version_and_platform(self, raw_version: str) -> tuple[str, str]:
        text = str(raw_version or "").strip()
        if not text:
            return "", ""
        match = re.search(r"\d+\.\d+\.\d+", text)
        if not match:
            return "", ""
        semver = match.group(0)
        platform = ""
        tail = text[match.end():]
        if tail.startswith("-"):
            platform = tail[1:].strip().lower()
        return semver, platform

    def _is_prerelease_version(self, item: object) -> bool:
        if not isinstance(item, dict):
            return False
        flags_text = str(item.get("flags", "")).lower()
        if "prerelease" in flags_text:
            return True
        for prop in (item.get("properties") or []):
            if not isinstance(prop, dict):
                continue
            if prop.get("key") == "Microsoft.VisualStudio.Code.PreRelease":
                return str(prop.get("value", "")).strip().lower() == "true"
        return False

    def _marketplace_target_platform(self, item: object) -> str:
        if not isinstance(item, dict):
            return ""
        value = item.get("targetPlatform")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        for prop in (item.get("properties") or []):
            if not isinstance(prop, dict):
                continue
            if prop.get("key") == "Microsoft.VisualStudio.Code.TargetPlatform":
                prop_val = str(prop.get("value", "")).strip().lower()
                if prop_val:
                    return prop_val
        return ""

    def _fetch_marketplace_release_meta(self) -> Optional[Dict[str, object]]:
        url = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
        payload = {
            "filters": [
                {
                    "criteria": [
                        {"filterType": 7, "value": "openai.chatgpt"},
                        {"filterType": 8, "value": "Microsoft.VisualStudio.Code"},
                    ]
                }
            ],
            "flags": 0x1 | 0x2 | 0x10 | 0x80 | 0x10000,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json;api-version=7.1-preview.1")
        req.add_header("User-Agent", "CodexSwitcher")
        try:
            with urllib_request.urlopen(req, timeout=6) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            obj = json.loads(body)
            ext = obj["results"][0]["extensions"][0]
            versions = ext.get("versions", [])
            if not isinstance(versions, list) or not versions:
                return None

            latest_stable: Optional[str] = None
            latest_prerelease: Optional[str] = None
            channel_map: Dict[str, str] = {}

            for item in versions:
                if not isinstance(item, dict):
                    continue
                version = str(item.get("version", "")).strip()
                if not version:
                    continue
                is_prerelease = self._is_prerelease_version(item)
                platform = self._marketplace_target_platform(item)
                channel = "preview" if is_prerelease else "stable"
                if is_prerelease and latest_prerelease is None:
                    latest_prerelease = version
                if (not is_prerelease) and latest_stable is None:
                    latest_stable = version
                keys = [f"{version}|{platform}", f"{version}|"]
                for key in keys:
                    existing = channel_map.get(key)
                    if existing is None:
                        channel_map[key] = channel
                    elif existing != channel:
                        channel_map[key] = "both"

            if not latest_stable and not latest_prerelease:
                return None
            return {
                "latest_stable": latest_stable,
                "latest_prerelease": latest_prerelease,
                "channel_map": channel_map,
            }
        except Exception:
            return None

    def _format_marketplace_latest_text(self, meta: Optional[Dict[str, object]]) -> str:
        if not meta:
            return "\u83b7\u53d6\u5931\u8d25"
        stable = str(meta.get("latest_stable") or "").strip()
        preview = str(meta.get("latest_prerelease") or "").strip()
        parts: List[str] = []
        if stable:
            parts.append(f"\u7a33\u5b9a\u7248\uff1a{stable}")
        if preview:
            parts.append(f"\u9884\u89c8\u7248\uff1a{preview}")
        return " | ".join(parts) if parts else "\u83b7\u53d6\u5931\u8d25"

    def _channel_label_from_meta(self, raw_version: str) -> str:
        meta = self._marketplace_meta
        if not meta:
            return ""
        semver, platform = self._split_version_and_platform(raw_version)
        if not semver:
            return ""
        channel_map = meta.get("channel_map")
        channel = None
        if isinstance(channel_map, dict):
            channel = channel_map.get(f"{semver}|{platform}") or channel_map.get(f"{semver}|")
        if channel is None:
            if semver == str(meta.get("latest_stable") or ""):
                channel = "stable"
            elif semver == str(meta.get("latest_prerelease") or ""):
                channel = "preview"
        if channel == "stable":
            return "\u7a33\u5b9a\u7248"
        if channel == "preview":
            return "\u9884\u89c8\u7248"
        if channel == "both":
            return "\u7a33\u5b9a/\u9884\u89c8\u5747\u6709"
        return ""

    def refresh_extensions(self) -> None:
        self.ext_combo.clear()
        self.extension_items = []
        self._marketplace_meta = self._fetch_marketplace_release_meta()
        for path in self._find_extensions():
            self.extension_items.append({"path": path, "version": self._parse_extension_version(path)})
        if self._marketplace_meta:
            latest_text = self._format_marketplace_latest_text(self._marketplace_meta)
            self.ext_latest_label.setText(latest_text)
            self.ext_latest_label.setToolTip(latest_text)
        elif self.extension_items:
            self.ext_latest_label.setText("\u83b7\u53d6\u5931\u8d25\uff08\u5df2\u663e\u793a\u672c\u5730\u626b\u63cf\u7ed3\u679c\uff09")
            self.ext_latest_label.setToolTip("")
        else:
            self.ext_latest_label.setText("\u83b7\u53d6\u5931\u8d25")
            self.ext_latest_label.setToolTip("")

        self.extension_items.sort(
            key=lambda item: (
                tuple(int(x) for x in re.findall(r"\d+", str(item.get("version", "")))[:3]),
                (item.get("path").stat().st_mtime if isinstance(item.get("path"), Path) and item.get("path").exists() else 0),
            ),
            reverse=True,
        )
        if not self.extension_items:
            self.ext_combo.addItem("未发现 openai.chatgpt 扩展")
            self.ext_path_label.setText("-")
            self.ext_version_label.setText("-")
            self.index_path_label.setText("-")
            self._index_path = None
            self.status_label.setText("未发现 VS Code 扩展，请确认已安装 Codex 插件")
            return
        for item in self.extension_items:
            self.ext_combo.addItem(item["path"].name, item)
        self.ext_combo.setCurrentIndex(0)
        self.on_extension_changed(0)
        self.status_label.setText(f"扫描完成：发现 {len(self.extension_items)} 个 openai.chatgpt 扩展")

    def on_extension_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.extension_items):
            return
        item = self.extension_items[index]
        ext_path = item["path"]
        self.ext_path_label.setText(str(ext_path))
        raw_version = str(item.get("version", "-"))
        channel_label = self._channel_label_from_meta(raw_version)
        if channel_label:
            display_version = f"{raw_version}（{channel_label}）"
        elif self._marketplace_meta is None:
            display_version = f"{raw_version}（渠道未知）"
        else:
            display_version = raw_version
        self.ext_version_label.setText(display_version)
        self.ext_version_label.setToolTip(display_version)
        self._index_path = self._find_index_file(ext_path)
        self.index_path_label.setText(str(self._index_path) if self._index_path else "未找到")

    def pick_index_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 index-*.js",
            "",
            "JavaScript (*.js)"
        )
        if not file_path:
            return
        self._index_path = Path(file_path)
        self.index_path_label.setText(str(self._index_path))
        self.status_label.setText("已选择 index 文件")

    def open_extension_folder(self) -> None:
        idx = self.ext_combo.currentIndex()
        if idx < 0 or idx >= len(self.extension_items):
            return
        ext_path = self.extension_items[idx]["path"]
        try:
            os.startfile(str(ext_path))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def _backup_dir_for_index(self, index_path: Path) -> Path:
        backup_dir = index_path.parent / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def _backup_index(self, index_path: Path) -> Path:
        backup_dir = self._backup_dir_for_index(index_path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{index_path.name}.{stamp}.bak"
        shutil.copy2(index_path, backup_path)
        self._backup_dir = backup_dir
        return backup_path


    def restore_backup(self) -> None:
        if not self._index_path or not self._index_path.exists():
            message_warn(self, "提示", "未找到 index 文件")
            return
        backup_dir = self._backup_dir_for_index(self._index_path)
        if not backup_dir.exists():
            message_warn(self, "提示", "未发现备份目录")
            return
        pattern = f"{self._index_path.name}."
        backups = [p for p in backup_dir.iterdir() if p.is_file() and p.name.startswith(pattern) and p.suffix == ".bak"]
        if not backups:
            message_warn(self, "提示", "未找到备份文件")
            return
        backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest_backup = backups[0]
        try:
            shutil.copy2(latest_backup, self._index_path)
            self.status_label.setText(f"已恢复：{latest_backup}")
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def open_backup_dir(self) -> None:
        if not self._backup_dir and self._index_path:
            candidate = self._backup_dir_for_index(self._index_path)
            if candidate.exists():
                self._backup_dir = candidate
        if not self._backup_dir:
            message_warn(self, "提示", "尚未生成备份")
            return
        try:
            os.startfile(str(self._backup_dir))
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def _split_model_input(self, raw: str) -> List[str]:
        token_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,120}$")
        normalized = (raw or "").replace("，", ",").replace("；", ";")
        parts = [p.strip() for p in re.split(r"[,;|\s]+", normalized) if p.strip()]
        models: List[str] = []
        seen: set[str] = set()
        for part in parts:
            if not token_re.match(part):
                continue
            key = part.lower()
            if key in seen:
                continue
            seen.add(key)
            models.append(part)
        return models

    def _target_models(self) -> List[str]:
        defaults = ["gpt-5.3-codex", "gpt-5.2-codex", "gpt-5.2"]
        user_models = self._split_model_input(self.model_edit.text().strip())
        merged: List[str] = []
        seen: set[str] = set()
        for model in user_models + defaults:
            key = model.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(model)
        return merged

    def _reasoning_efforts_literal(self) -> str:
        return (
            '[{reasoningEffort:"minimal",description:"minimal effort"},'
            '{reasoningEffort:"low",description:"low effort"},'
            '{reasoningEffort:"medium",description:"medium effort"},'
            '{reasoningEffort:"high",description:"high effort"},'
            '{reasoningEffort:"xhigh",description:"xhigh effort"}]'
        )

    def _merge_models_into_js_array(self, body: str, models: List[str]) -> tuple[str, bool]:
        quote = '"' if '"' in body else "'"
        existing = re.findall(r'["\']([^"\']+)["\']', body)
        merged: List[str] = []
        seen: set[str] = set()
        for model in models + existing:
            key = model.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(model)
        new_body = ",".join(f"{quote}{item}{quote}" for item in merged)
        changed = new_body != body
        return new_body, changed

    def _apply_allowlist_patch(self, content: str, models: List[str]) -> tuple[str, bool]:
        set_pattern = re.compile(r'([A-Za-z_$][\w$]*)=new Set\(\[(.*?)\]\)', re.S)
        touched = False

        def repl(match: re.Match[str]) -> str:
            nonlocal touched
            set_name = match.group(1)
            if "AUTH_ONLY" in set_name.upper():
                return match.group(0)

            if set_name != "SUe" and f':{set_name}).has(v.model)' not in content:
                return match.group(0)

            body = match.group(2)
            existing = re.findall(r'["\']([^"\']+)["\']', body)
            gpt_like_count = sum(1 for m in existing if m.startswith("gpt-"))
            if not (
                "gpt-5.2-codex" in existing
                or "gpt-5.1-codex-mini" in existing
                or gpt_like_count >= 3
            ):
                return match.group(0)

            touched = True
            existing_lower = {m.lower() for m in existing}
            missing = [m for m in models if m.lower() not in existing_lower]
            if not missing:
                return match.group(0)

            quote = '"' if '"' in body else "'"
            prefix = ",".join(f"{quote}{m}{quote}" for m in missing)
            new_body = f"{prefix},{body}" if body.strip() else prefix
            return f"{match.group(1)}=new Set([{new_body}])"

        updated = set_pattern.sub(repl, content)
        if touched:
            return updated, True

        # Fallback for builds that still expose SUe only.
        if "SUe=new Set" in content:
            pattern = re.compile(r"SUe=new Set\(\[(.*?)\]\)")
            match = pattern.search(content)
            if match:
                body = match.group(1)
                existing = re.findall(r'["\']([^"\']+)["\']', body)
                existing_lower = {m.lower() for m in existing}
                missing = [m for m in models if m.lower() not in existing_lower]
                if not missing:
                    return content, True
                quote = '"' if '"' in body else "'"
                prefix = ",".join(f"{quote}{m}{quote}" for m in missing)
                new_body = f"{prefix},{body}" if body.strip() else prefix
                return content[: match.start(1)] + new_body + content[match.end(1) :], True

        # Fallback for the newer max flow (MODEL_ORDER_BY_AUTH_METHOD).
        model_order_pattern = re.compile(
            r'(MODEL_ORDER_BY_AUTH_METHOD\s*=\s*\{.*?apikey\s*:\s*\[)(.*?)(\])',
            re.S,
        )
        model_order_match = model_order_pattern.search(content)
        if model_order_match:
            body = model_order_match.group(2)
            new_body, changed = self._merge_models_into_js_array(body, models)
            if not changed:
                return content, True
            patched = content[: model_order_match.start(2)] + new_body + content[model_order_match.end(2) :]
            return patched, True

        return content, False

    def _apply_chatgpt_auth_only_models_patch(self, content: str, models: List[str]) -> tuple[str, bool]:
        if "CHAT_GPT_AUTH_ONLY_MODELS" not in content:
            return content, False

        pattern = re.compile(r'CHAT_GPT_AUTH_ONLY_MODELS\s*=\s*new Set\(\[(.*?)\]\)', re.S)
        match = pattern.search(content)
        if not match:
            return content, False

        body = match.group(1)
        quote = '"' if '"' in body else "'"
        existing = re.findall(r'["\']([^"\']+)["\']', body)
        deny_set = {m.lower() for m in models}
        filtered = [item for item in existing if item.lower() not in deny_set]

        # No user model in deny-list is also considered a successful match.
        if filtered == existing:
            return content, True

        new_body = ",".join(f"{quote}{item}{quote}" for item in filtered)
        patched = content[: match.start(1)] + new_body + content[match.end(1) :]
        return patched, True

    def _apply_chatgpt_auth_guard_patch(self, content: str) -> tuple[str, bool]:
        marker = "CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))"
        if marker not in content:
            return content, False

        already_pattern = re.compile(
            r'[A-Za-z_$][\w$]*!=="apikey"\s*&&\s*!!mt\s*&&\s*CHAT_GPT_AUTH_ONLY_MODELS\.has\(normalizeModel\(mt\)\)'
        )
        if already_pattern.search(content):
            return content, True

        idx = content.find(marker)
        window = content[max(0, idx - 800) : idx]
        auth_var = ""
        for found in re.finditer(r'([A-Za-z_$][\w$]*)===\"(?:chatgpt|apikey)\"', window):
            auth_var = found.group(1)

        if not auth_var:
            return content, False

        tight_src = "&&!!mt&&CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))"
        tight_dst = f'&&{auth_var}!=="apikey"&&!!mt&&CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))'
        if tight_src in content:
            return content.replace(tight_src, tight_dst, 1), True

        spaced = re.compile(r'&&\s*!!mt\s*&&\s*CHAT_GPT_AUTH_ONLY_MODELS\.has\(normalizeModel\(mt\)\)')
        matched = spaced.search(content)
        if not matched:
            return content, False

        replacement = f'&& {auth_var}!=="apikey" && !!mt && CHAT_GPT_AUTH_ONLY_MODELS.has(normalizeModel(mt))'
        patched = content[: matched.start()] + replacement + content[matched.end() :]
        return patched, True

    def _apply_apikey_filter_patch(self, content: str, models: List[str]) -> tuple[str, bool]:
        patched = content
        gate_ok = False

        if 'i==="chatgpt"||i==="apikey"?!0:' in patched:
            gate_ok = True
        else:
            src = 'i==="chatgpt"?!0:(i==="copilot"?kUe:SUe).has(v.model)'
            dst = 'i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?kUe:SUe).has(v.model)'
            if src in patched:
                patched = patched.replace(src, dst, 1)
                gate_ok = True
            else:
                pattern = re.compile(
                    r'i==="chatgpt"\?!0:\(i==="copilot"\?([A-Za-z_$][\w$]*):([A-Za-z_$][\w$]*)\)\.has\(v\.model\)'
                )
                match = pattern.search(patched)
                if match:
                    replacement = (
                        f'i==="chatgpt"||i==="apikey"?!0:(i==="copilot"?{match.group(1)}:{match.group(2)}).has(v.model)'
                    )
                    patched = patched[: match.start()] + replacement + patched[match.end() :]
                    gate_ok = True

        # These two patches are useful hardening, but they are not sufficient to
        # guarantee API-key routing on their own. Keep gate_ok as the success signal.
        patched, _ = self._apply_chatgpt_auth_guard_patch(patched)
        patched, _ = self._apply_chatgpt_auth_only_models_patch(patched, models)

        return patched, gate_ok


    def _apply_apikey_order_inject_patch(self, content: str, models: List[str]) -> tuple[str, bool]:
        prefix = re.compile(
            r'i==="apikey"&&\(\(\)=>\{const Y=\[(.*?)\],X=new Map\(Y\.map\(\(A,R\)=>\[A,R\]\)\);',
            re.S,
        )
        match = prefix.search(content)
        if not match:
            return content, False

        body = match.group(1)
        quote = '"' if '"' in body else "'"
        existing_y = re.findall(r'["\']([^"\']+)["\']', body)
        y_merged: List[str] = []
        seen: set[str] = set()
        for model in models + existing_y:
            key = model.lower()
            if key in seen:
                continue
            seen.add(key)
            y_merged.append(model)
        new_y_body = ",".join(f"{quote}{m}{quote}" for m in y_merged)
        if new_y_body != body:
            content = content[: match.start(1)] + new_y_body + content[match.end(1) :]
            match = prefix.search(content)
            if not match:
                return content, False

        block_start = match.start()
        block_end = content.find('})()', block_start)
        if block_end == -1:
            block_end = min(len(content), block_start + 5000)
        sort_idx = content.find('m.models.sort(', block_start, block_end)
        if sort_idx == -1:
            sort_idx = content.find('m.models.sort(', block_start)
        if sort_idx == -1:
            return content, False

        efforts = self._reasoning_efforts_literal()
        block_segment = content[block_start:block_end]
        injections: List[str] = []
        for model in models:
            marker = f'm.models.find(A=>A.model==="{model}")||m.models.unshift({{'
            if marker in block_segment:
                continue
            injections.append(
                f'm.models.find(A=>A.model==="{model}")||m.models.unshift({{model:"{model}",supportedReasoningEfforts:{efforts},defaultReasoningEffort:"medium"}}),'
            )

        if injections:
            content = content[:sort_idx] + "".join(injections) + content[sort_idx:]
        return content, True

    def _apply_initial_data_patch(self, content: str, models: List[str]) -> tuple[str, bool]:
        if 'initialData:i==="apikey"?{data:[' not in content:
            return content, False

        efforts = self._reasoning_efforts_literal()
        data_entries = [
            f'{{model:"{model}",supportedReasoningEfforts:{efforts},defaultReasoningEffort:"medium",isDefault:!1}}'
            for model in models
        ]
        desired = 'initialData:i==="apikey"?{data:[' + ",".join(data_entries) + ']}:void 0'
        if desired in content:
            return content, True

        pattern = re.compile(r'initialData:i===\"apikey\"\?\{data:\[(.*?)\]\}:void 0', re.S)
        match = pattern.search(content)
        if not match:
            return content, False
        return content[: match.start()] + desired + content[match.end() :], True

    def apply_patch(self) -> None:
        if not self._index_path or not self._index_path.exists():
            message_warn(self, "提示", "请先扫描并选择 index 文件")
            return

        target_models = self._target_models()
        if not target_models:
            message_warn(self, "提示", "请输入至少一个模型名称（可用逗号分隔）")
            return

        try:
            original = self._index_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            message_error(self, "失败", str(exc))
            return

        content, ok1 = self._apply_allowlist_patch(original, target_models)
        content, ok2 = self._apply_apikey_filter_patch(content, target_models)
        content, ok3 = self._apply_apikey_order_inject_patch(content, target_models)
        content, ok4 = self._apply_initial_data_patch(content, target_models)

        critical_failed = []
        if not ok1:
            critical_failed.append("allowlist/model-order")
        if not ok2:
            critical_failed.append("apikey-filter/auth-only")

        if critical_failed:
            message_error(
                self,
                "失败",
                "未能定位关键片段："
                + ", ".join(critical_failed)
                + "。\n"
                + "请先点击“扫描插件”，并确认选择的是目标扩展的 index-*.js 文件。",
            )
            return

        backup_path = self._backup_index(self._index_path)
        try:
            self._index_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            message_error(self, "失败", str(exc))
            return

        optional_failed = []
        if not ok3:
            optional_failed.append("apikey-order")
        if not ok4:
            optional_failed.append("initial-data")

        if optional_failed:
            message_warn(
                self,
                "提示",
                "模型已增加，但部分规则未更新："
                + ", ".join(optional_failed)
                + "。\n"
                + "可重启 VS Code 后验证模型下拉；若仍缺失可改用手动 index 文件。",
            )

        preview = ", ".join(target_models[:5])
        if len(target_models) > 5:
            preview += ", ..."
        if optional_failed:
            self.status_label.setText(f"模型已增加（部分规则未更新），备份：{backup_path}；模型：{preview}")
        else:
            self.status_label.setText(f"模型已增加，规则已更新，备份：{backup_path}；模型：{preview}")

    def _settings_paths(self) -> List[Path]:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        base = Path(appdata)
        candidates = [
            base / "Code" / "User" / "settings.json",
            base / "Code - Insiders" / "User" / "settings.json",
            base / "VSCodium" / "User" / "settings.json",
            base / "Cursor" / "User" / "settings.json",
        ]
        return [p for p in candidates if p.exists()]

    def _load_jsonc(self, text: str) -> dict:
        no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        no_line = re.sub(r"//.*", "", no_block)
        try:
            return json.loads(no_line) if no_line.strip() else {}
        except Exception:
            return {}

    def disable_auto_update(self) -> None:
        paths = self._settings_paths()
        if not paths:
            message_warn(self, "提示", "未找到 VS Code 设置文件")
            return
        updated = 0
        for path in paths:
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                raw = ""
            data = self._load_jsonc(raw)
            data["extensions.autoUpdate"] = False
            data["extensions.autoCheckUpdates"] = False
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                updated += 1
            except Exception:
                continue
        if updated:
            self.status_label.setText(f"已更新 {updated} 个 settings.json")
        else:
            message_warn(self, "提示", "无法写入设置文件")


class SettingsPage(QtWidgets.QWidget):


    def __init__(self, state: AppState, on_update_count_changed: Optional[Callable[[int], None]] = None) -> None:
        super().__init__()
        self.state = state
        self._on_update_count_changed = on_update_count_changed

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("检查更新")
        header.setFont(self._header_font())
        layout.addWidget(header)

        info_group = QtWidgets.QGroupBox("版本信息")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QVBoxLayout(info_group)
        self.current_version = QtWidgets.QLabel(f"当前版本：{APP_VERSION}")
        self.latest_version = QtWidgets.QLabel("最新版本：-")
        self.update_status = QtWidgets.QLabel("状态：未检查")
        info_layout.addWidget(self.current_version)
        info_layout.addWidget(self.latest_version)
        info_layout.addWidget(self.update_status)
        layout.addWidget(info_group)

        notes_group = QtWidgets.QGroupBox("更新内容")
        apply_white_shadow(notes_group)
        notes_layout = QtWidgets.QVBoxLayout(notes_group)
        self.release_notes = QtWidgets.QPlainTextEdit()
        self.release_notes.setReadOnly(True)
        self.release_notes.setMinimumHeight(120)
        notes_layout.addWidget(self.release_notes)
        layout.addWidget(notes_group)

        action_row = QtWidgets.QHBoxLayout()
        self.check_btn = QtWidgets.QPushButton("立即检查")
        self.check_btn.clicked.connect(self.check_update)
        self.open_release_btn = QtWidgets.QPushButton("打开发布页")
        self.open_release_btn.clicked.connect(self.open_release_page)
        action_row.addWidget(self.check_btn)
        action_row.addWidget(self.open_release_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        feedback_group = QtWidgets.QGroupBox("开发者反馈")
        apply_white_shadow(feedback_group)
        feedback_layout = QtWidgets.QVBoxLayout(feedback_group)
        feedback_tip = QtWidgets.QLabel("本工具永久开源免费，扫码添加开发者好友，反馈bug和需求")
        feedback_tip.setAlignment(QtCore.Qt.AlignHCenter)
        feedback_tip.setStyleSheet("color: #000; font-weight: 600;")
        feedback_layout.addWidget(feedback_tip)
        self.dev_qr_image = QtWidgets.QLabel()
        self.dev_qr_image.setAlignment(QtCore.Qt.AlignCenter)
        self.dev_qr_image.setFixedSize(220, 220)
        self.dev_qr_image.setStyleSheet("border: 1px solid #ddd; border-radius: 8px; background: #fff;")
        feedback_layout.addWidget(self.dev_qr_image, 0, QtCore.Qt.AlignHCenter)
        self.dev_qr_hint = QtWidgets.QLabel("")
        self.dev_qr_hint.setAlignment(QtCore.Qt.AlignCenter)
        self.dev_qr_hint.setWordWrap(True)
        self.dev_qr_hint.setStyleSheet("color: #666;")
        self.dev_qr_hint.setVisible(False)
        feedback_layout.addWidget(self.dev_qr_hint)
        self._load_developer_qr()
        layout.addWidget(feedback_group)
        layout.addStretch(1)
        self._latest_url = f"https://github.com/{APP_REPO}/releases/latest"
        self._checked_once = False
        self._notified = False
        self._last_update_count = 0
        self._checking = False

    def _developer_qr_candidates(self) -> List[Path]:
        roots: List[Path] = []

        def add_root(raw: Optional[Path]) -> None:
            if not raw:
                return
            try:
                resolved = raw.resolve()
            except Exception:
                resolved = raw
            key = str(resolved).lower()
            if key not in seen_roots:
                seen_roots.add(key)
                roots.append(resolved)

        seen_roots: set[str] = set()

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            add_root(Path(meipass))

        script_dir = Path(__file__).resolve().parent
        exe_dir = Path(sys.executable).resolve().parent
        cwd = Path.cwd()

        add_root(script_dir)
        add_root(exe_dir)
        add_root(cwd)

        # Expected binary path: <project>/dist/CodexSwitcher.exe
        add_root(exe_dir.parent)
        add_root(exe_dir.parent.parent)
        if exe_dir.name.lower() == "dist":
            add_root(exe_dir.parent)

        # Some builds flatten everything directly under dist.
        for root in list(roots):
            add_root(root / "dist")

        names = [
            "developer_qr.png",
            "developer_qr.jpg",
            "developer_qr.jpeg",
            "dev_qr.png",
            "dev_qr.jpg",
            "dev_qr.jpeg",
            "feedback_qr.png",
            "feedback_qr.jpg",
            "feedback_qr.jpeg",
        ]

        paths: List[Path] = []
        seen_paths: set[str] = set()
        for root in roots:
            for name in names:
                candidate = root / name
                key = str(candidate).lower()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                paths.append(candidate)
        return paths

    def _load_developer_qr(self) -> None:
        target = max(120, min(self.dev_qr_image.width(), self.dev_qr_image.height()) - 20)
        candidates = self._developer_qr_candidates()
        for path in candidates:
            if not path.exists():
                continue
            pixmap = QtGui.QPixmap(str(path))
            if pixmap.isNull():
                continue
            scaled = pixmap.scaled(
                target,
                target,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self.dev_qr_image.setPixmap(scaled)
            self.dev_qr_image.setText("")
            self.dev_qr_hint.clear()
            self.dev_qr_hint.setVisible(False)
            return

        self.dev_qr_image.setPixmap(QtGui.QPixmap())
        self.dev_qr_image.setText("未找到二维码图片")
        self.dev_qr_hint.setText("请将二维码保存为 developer_qr.png 并放到程序目录")
        self.dev_qr_hint.setVisible(True)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        if not self._checked_once:
            self._checked_once = True
            self.check_update(auto=True)
        return

    def open_release_page(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._latest_url))

    def check_update(self, auto: bool = False) -> None:
        if self._checking:
            return
        self._checking = True
        self.check_btn.setEnabled(False)
        self.update_status.setText("状态：检查中...")
        self.latest_version.setText("最新版本：-")
        if hasattr(self, "release_notes"):
            self.release_notes.setPlainText("正在获取更新内容...")

        def runner() -> None:
            try:
                ok, latest_ver, url, msg = self._get_latest_release()
            except Exception as exc:
                ok, latest_ver, url, msg = False, "-", "", str(exc)
            notes_text = ""
            if ok:
                try:
                    notes_text = self._get_release_notes(APP_VERSION, latest_ver)
                except Exception as exc:
                    notes_text = f"无法获取更新内容：{exc}"

            def done() -> None:
                self._checking = False
                self.check_btn.setEnabled(True)
                if ok:
                    self._latest_url = url or self._latest_url
                    self.latest_version.setText(f"最新版本：{latest_ver}")
                    if hasattr(self, "release_notes"):
                        self.release_notes.setPlainText(notes_text or "无更新内容")
                    status_text, has_update = self._compare_versions(APP_VERSION, latest_ver)
                    self.update_status.setText(status_text)
                    update_count = self._version_gap_count(APP_VERSION, latest_ver) if has_update else 0
                    self._emit_update_count(update_count)
                    if has_update and not self._notified:
                        self._notified = True
                        message_info(self, "发现新版本", f"检测到新版本：{latest_ver}\n请前往发布页下载。")
                else:
                    self.update_status.setText(f"状态：{msg}")
                    if hasattr(self, "release_notes"):
                        self.release_notes.setPlainText(msg)

            run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

    def _emit_update_count(self, count: int) -> None:
        if count == self._last_update_count:
            return
        self._last_update_count = count
        if self._on_update_count_changed:
            self._on_update_count_changed(count)

    def _version_gap_count(self, local: Optional[str], latest: Optional[str]) -> int:
        local_sem = self._extract_semver(local or "")
        latest_sem = self._extract_semver(latest or "")
        if not local_sem or not latest_sem:
            return 0
        release_count = self._count_releases_behind(local_sem, latest_sem)
        if release_count > 0:
            return release_count
        try:
            local_parts = [int(p) for p in local_sem.split(".")]
            latest_parts = [int(p) for p in latest_sem.split(".")]
        except Exception:
            return 0
        while len(local_parts) < 3:
            local_parts.append(0)
        while len(latest_parts) < 3:
            latest_parts.append(0)
        if tuple(latest_parts) <= tuple(local_parts):
            return 0
        return max(1, latest_parts[2] - local_parts[2])

    def _count_releases_behind(self, local_sem: str, latest_sem: str) -> int:
        if local_sem == latest_sem:
            return 0
        api_url = f"https://api.github.com/repos/{APP_REPO}/releases?per_page=100"
        req = urllib_request.Request(api_url, headers={"User-Agent": "CodexSwitcher"})
        try:
            with urllib_request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return 0
        if not isinstance(data, list):
            return 0
        versions: List[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag_name") or item.get("name") or ""
            ver = self._extract_semver(tag)
            if not ver or ver in seen:
                continue
            seen.add(ver)
            versions.append(ver)
        if latest_sem not in versions:
            return 0
        latest_index = versions.index(latest_sem)
        if local_sem in versions:
            local_index = versions.index(local_sem)
            if local_index <= latest_index:
                return 0
            return local_index - latest_index
        return 1

    def _filter_release_sections(self, body: str) -> str:
        wanted = {"标题", "变更"}
        lines = body.splitlines()
        out: list[str] = []
        keep = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("##"):
                heading = stripped.lstrip("#").strip()
                normalized = heading.replace("：", ":").strip()
                keep = False
                for w in wanted:
                    if normalized == w or normalized.startswith(f"{w}:") or normalized.startswith(f"{w} "):
                        keep = True
                        out.append(f"## {w}")
                        break
                continue
            if keep:
                out.append(line.rstrip())
        cleaned: list[str] = []
        blank = False
        for line in out:
            if not line.strip():
                if not blank:
                    cleaned.append("")
                blank = True
            else:
                cleaned.append(line)
                blank = False
        while cleaned and not cleaned[0].strip():
            cleaned.pop(0)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        return "\n".join(cleaned).strip()

    def _get_release_notes(self, local_ver: str, latest_ver: str) -> str:
        latest_sem = self._extract_semver(latest_ver) or latest_ver
        if not latest_sem:
            return "无法解析版本号，无法生成更新内容。"

        api_url = f"https://api.github.com/repos/{APP_REPO}/releases?per_page=20"
        req = urllib_request.Request(api_url, headers={"User-Agent": "CodexSwitcher"})
        with urllib_request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            return "无法获取更新内容。"

        target = None
        for item in data:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag_name") or ""
            name = item.get("name") or ""
            ver = self._extract_semver(tag) or self._extract_semver(name) or ""
            if ver and ver == latest_sem:
                target = item
                break
            if tag == latest_ver or name == latest_ver:
                target = item
                break
        if target is None:
            target = data[0]

        body = target.get("body") or ""
        filtered = self._filter_release_sections(body)
        if not filtered:
            return "未找到Release中的标题/变更内容。"
        return filtered

    def _get_latest_release(self) -> tuple[bool, str, str, str]:
        try:
            api_url = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
            req = urllib_request.Request(api_url, headers={"User-Agent": "CodexSwitcher"})
            with urllib_request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name") or data.get("name") or "未知"
            url = data.get("html_url") or f"https://github.com/{APP_REPO}/releases/latest"
            ver = self._extract_semver(tag) or tag
            return True, ver, url, ""
        except urllib_error.URLError:
            return False, "-", "", "网络不可用或无法访问 GitHub，请检查网络/代理后重试。"
        except Exception as exc:
            return False, "-", "", str(exc)

    def _extract_semver(self, text: str) -> Optional[str]:
        match = re.search(r"\d+\.\d+\.\d+", text)
        return match.group(0) if match else None

    def _compare_versions(self, local: Optional[str], latest: Optional[str]) -> tuple[str, bool]:
        local_sem = self._extract_semver(local or "")
        latest_sem = self._extract_semver(latest or "")
        if local_sem and latest_sem:
            if local_sem == latest_sem:
                return "状态：已是最新版本。", False
            try:
                local_parts = tuple(int(p) for p in local_sem.split("."))
                latest_parts = tuple(int(p) for p in latest_sem.split("."))
                if local_parts > latest_parts:
                    return f"状态：本地版本 {local_sem} 高于最新 {latest_sem}。", False
            except Exception:
                pass
            return f"状态：发现新版本 {latest_sem}。", True
        if latest:
            return f"状态：最新版本 {latest}。", False
        return "状态：无法比较版本。", False





class SessionManagerPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._sessions: list[dict] = []
        self._history_index: dict[str, str] = {}
        self._loaded_once = False
        self._search_cancel = threading.Event()
        self._active_search_id = 0

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("Codex会话管理")
        header.setFont(self._header_font())
        layout.addWidget(header)

        content = QtWidgets.QWidget()
        self._session_content = content
        content_layout = QtWidgets.QHBoxLayout(content)
        self._session_split_layout = content_layout
        content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content, 1)

        # Left panel: search + list
        left = QtWidgets.QWidget()
        self._session_left = left
        left.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left.setMinimumWidth(0)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        search_row = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setFixedHeight(24)
        self.search_edit.setPlaceholderText("关键词过滤（history 优先，必要时深度搜索）")
        self.search_edit.textChanged.connect(self.apply_filter)
        self.refresh_btn = QtWidgets.QPushButton("刷新索引")
        self.refresh_btn.setFixedHeight(24)
        self.refresh_btn.clicked.connect(self.refresh_index)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.refresh_btn)
        left_layout.addLayout(search_row)

        search_option_row = QtWidgets.QHBoxLayout()
        search_option_row.addWidget(QtWidgets.QLabel("模式"))
        self.search_mode = QtWidgets.QComboBox()
        self.search_mode.addItems(["OR（任一命中）", "AND（全部命中）"])
        self.search_mode.currentIndexChanged.connect(self.apply_filter)
        search_option_row.addWidget(self.search_mode)
        search_option_row.addSpacing(6)
        search_option_row.addWidget(QtWidgets.QLabel("最多扫描"))
        self.scan_limit = QtWidgets.QSpinBox()
        self.scan_limit.setRange(1, 10000)
        self.scan_limit.setValue(200)
        self.scan_limit.setSuffix(" 条")
        self.scan_limit.valueChanged.connect(self.apply_filter)
        search_option_row.addWidget(self.scan_limit)
        search_option_row.addSpacing(6)
        search_option_row.addWidget(QtWidgets.QLabel("最近"))
        self.scan_days = QtWidgets.QSpinBox()
        self.scan_days.setRange(1, 3650)
        self.scan_days.setValue(90)
        self.scan_days.setSuffix(" 天")
        self.scan_days.valueChanged.connect(self.apply_filter)
        search_option_row.addWidget(self.scan_days)
        search_option_row.addStretch(1)
        left_layout.addLayout(search_option_row)

        self.search_hint = QtWidgets.QLabel("高级语法：空格分词；模式选 AND/OR；包含“|”强制 OR")
        self.search_hint.setWordWrap(True)
        self.search_hint.setStyleSheet("color: #666;")
        left_layout.addWidget(self.search_hint)

        left_layout.addSpacing(20)

        self.count_label = QtWidgets.QLabel("共 0 条")
        self.count_label.setTextFormat(QtCore.Qt.RichText)
        self.count_label.setWordWrap(True)
        left_layout.addWidget(self.count_label)

        self.search_status = QtWidgets.QLabel("")
        self.search_status.setWordWrap(True)
        self.search_status.setStyleSheet("color: #666;")
        left_layout.addWidget(self.search_status)

        progress_row = QtWidgets.QHBoxLayout()
        self.search_progress = QtWidgets.QProgressBar()
        self.search_progress.setFixedHeight(16)
        self.search_progress.setTextVisible(True)
        self.search_progress.setFormat("%v/%m")
        self.search_progress.setVisible(False)
        self.search_cancel_btn = QtWidgets.QPushButton("取消搜索")
        self.search_cancel_btn.setFixedHeight(22)
        self.search_cancel_btn.setVisible(False)
        self.search_cancel_btn.clicked.connect(self.cancel_search)
        progress_row.addWidget(self.search_progress, 1)
        progress_row.addWidget(self.search_cancel_btn)
        left_layout.addLayout(progress_row)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setMinimumWidth(0)
        self.list_widget.setMinimumHeight(200)
        self.list_widget.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        self.list_widget.setTextElideMode(QtCore.Qt.ElideRight)
        self.list_widget.currentRowChanged.connect(self.on_select)
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_session_menu)
        left_layout.addWidget(self.list_widget, 1)

        content_layout.addWidget(left, 0)

        # Right panel: details + export
        right = QtWidgets.QWidget()
        self._session_right = right
        right.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right.setMinimumWidth(0)
        right_layout = QtWidgets.QVBoxLayout(right)

        option_row = QtWidgets.QHBoxLayout()
        self.only_ua_check = QtWidgets.QCheckBox("仅显示 user/assistant")
        self.only_ua_check.setChecked(True)
        self.only_ua_check.stateChanged.connect(self._reload_current_detail)
        option_row.addWidget(self.only_ua_check)
        option_row.addStretch(1)
        right_layout.addLayout(option_row)

        self.only_ua_hint = QtWidgets.QLabel("不勾选：看“所有原始记录”，包括系统提示、开发者说明、工具响应等")
        self.only_ua_hint.setWordWrap(True)
        self.only_ua_hint.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.only_ua_hint.setStyleSheet("color: #666;")
        right_layout.addWidget(self.only_ua_hint)

        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMinimumHeight(120)
        right_layout.addWidget(self.detail_text, 1)

        export_row = QtWidgets.QHBoxLayout()
        self.export_json_btn = QtWidgets.QPushButton("导出 JSON")
        self.export_json_btn.clicked.connect(self.export_json)
        self.export_md_btn = QtWidgets.QPushButton("导出 Markdown")
        self.export_md_btn.clicked.connect(self.export_markdown)
        export_row.addWidget(self.export_json_btn)
        export_row.addWidget(self.export_md_btn)
        export_row.addStretch(1)
        right_layout.addLayout(export_row)

        content_layout.addWidget(right, 0)
        self._update_session_split()

        # Cleanup group
        cleanup_group = QtWidgets.QGroupBox("统一清理")
        apply_white_shadow(cleanup_group)
        cleanup_layout = QtWidgets.QHBoxLayout(cleanup_group)
        self.clean_mode = QtWidgets.QComboBox()
        self.clean_mode.addItems(["按日期（早于）", "按大小（大于）"])
        self.clean_mode.currentIndexChanged.connect(self._update_clean_mode)
        self.clean_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.clean_date.setCalendarPopup(True)
        self.clean_size = QtWidgets.QSpinBox()
        self.clean_size.setRange(1, 10240)
        self.clean_size.setValue(100)
        self.clean_size.setSuffix(" MB")
        self.clean_history = QtWidgets.QCheckBox("同时清理 history.jsonl")
        self.clean_history.setChecked(True)
        self.clean_btn = QtWidgets.QPushButton("执行清理")
        self.clean_btn.clicked.connect(self.run_cleanup)
        cleanup_layout.addWidget(self.clean_mode)
        cleanup_layout.addWidget(self.clean_date)
        cleanup_layout.addWidget(self.clean_size)
        cleanup_layout.addWidget(self.clean_history)
        cleanup_layout.addWidget(self.clean_btn)
        layout.addWidget(cleanup_group)

        self._update_clean_mode()

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_session_split()

    def _update_session_split(self) -> None:
        if not hasattr(self, "_session_split_guard"):
            self._session_split_guard = False
        if self._session_split_guard:
            return
        if not hasattr(self, "_session_split_layout"):
            return
        left = getattr(self, "_session_left", None)
        right = getattr(self, "_session_right", None)
        content = getattr(self, "_session_content", None)
        if left is None or right is None or content is None:
            return
        total = content.width()
        margins = self._session_split_layout.contentsMargins()
        spacing = self._session_split_layout.spacing()
        available = total - margins.left() - margins.right() - spacing
        if available <= 0:
            return
        left_w = int(available * 0.5)
        right_w = max(available - left_w, 0)
        self._session_split_guard = True
        try:
            left.setMinimumWidth(left_w)
            left.setMaximumWidth(left_w)
            right.setMinimumWidth(right_w)
            right.setMaximumWidth(right_w)
        finally:
            self._session_split_guard = False

    def on_show(self) -> None:
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh_index()

    def _update_clean_mode(self) -> None:
        mode = self.clean_mode.currentIndex()
        self.clean_date.setVisible(mode == 0)
        self.clean_size.setVisible(mode == 1)

    def refresh_index(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.list_widget.clear()
        self.detail_text.setPlainText("正在加载会话索引...")

        def runner() -> None:
            sessions = self._load_sessions()
            history = self._load_history_index()

            def done() -> None:
                self._sessions = sessions
                self._history_index = history
                self.refresh_btn.setEnabled(True)
                self.apply_filter()

            run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

    def _load_sessions(self) -> list[dict]:
        base = Path.home() / ".codex" / "sessions"
        if not base.exists():
            return []
        items = []
        for path in base.rglob("*.jsonl"):
            meta = self._read_session_meta(path)
            if not meta:
                continue
            meta["path"] = str(path)
            meta["size"] = path.stat().st_size
            meta["mtime"] = path.stat().st_mtime
            items.append(meta)
        items.sort(key=lambda x: x.get("ts_epoch", 0), reverse=True)
        return items

    def _read_session_meta(self, path: Path) -> Optional[dict]:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for _ in range(50):
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("type") != "session_meta":
                        continue
                    payload = data.get("payload") or {}
                    sid = payload.get("id", "")
                    ts = payload.get("timestamp", "")
                    cwd = payload.get("cwd", "")
                    model = payload.get("model_provider", "")
                    git = payload.get("git") or {}
                    branch = git.get("branch", "") if isinstance(git, dict) else ""
                    display_time, epoch = self._format_time(ts)
                    return {
                        "id": sid,
                        "timestamp": ts,
                        "ts_epoch": epoch,
                        "time_display": display_time,
                        "cwd": cwd,
                        "model": model,
                        "branch": branch,
                    }
        except Exception:
            return None
        return None

    def _format_time(self, ts: str) -> tuple[str, float]:
        if not ts:
            return ("-", 0.0)
        try:
            ts_norm = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_norm)
            local = dt.astimezone()
            return (local.strftime("%Y-%m-%d %H:%M:%S"), local.timestamp())
        except Exception:
            return (ts, 0.0)

    def _load_history_index(self) -> dict[str, str]:
        history = Path.home() / ".codex" / "history.jsonl"
        if not history.exists():
            return {}
        index: dict[str, str] = {}
        try:
            with history.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    sid = data.get("session_id") or ""
                    text = data.get("text") or ""
                    if not sid:
                        continue
                    prev = index.get(sid, "")
                    if len(prev) < 2000:
                        merged = (prev + "\n" + text).strip()
                        index[sid] = merged[:2000].lower()
        except Exception:
            return index
        return index

    def _parse_keywords(self, raw: str) -> tuple[list[str], str]:
        raw = raw.strip().lower()
        if not raw:
            return [], "OR"
        force_or = "|" in raw
        terms = [t for t in re.split(r"[\s|]+", raw) if t]
        mode = "AND" if self.search_mode.currentIndex() == 1 else "OR"
        if force_or:
            mode = "OR"
        return terms, mode

    def _match_text(self, text: str, terms: list[str], mode: str) -> bool:
        if not terms:
            return True
        if not text:
            return False
        if mode == "AND":
            return all(t in text for t in terms)
        return any(t in text for t in terms)

    def _apply_list(self, items: list[dict], show_empty: bool = True) -> None:
        self.list_widget.clear()
        shown = 0
        for item in items:
            display = f"{item.get('time_display', '-')} | {item.get('model', '-') or '-'} | {item.get('branch', '-') or '-'} | {item.get('cwd', '-') or '-'}"
            row = QtWidgets.QListWidgetItem(display)
            row.setData(QtCore.Qt.UserRole, item)
            self.list_widget.addItem(row)
            shown += 1
        self.count_label.setText(f"共 {shown} 条<b>【ⓘ 提示：鼠标右键Codex CLI/VS Code继续该会话、管理会话。】</b>")
        if shown == 0 and show_empty:
            self.detail_text.setPlainText("无匹配会话。")

    def _show_search_progress(self, total: int) -> None:
        self.search_progress.setMaximum(max(total, 1))
        self.search_progress.setValue(0)
        self.search_progress.setVisible(True)
        self.search_cancel_btn.setEnabled(True)
        self.search_cancel_btn.setVisible(True)

    def _update_search_progress(self, value: int, total: int, search_id: int) -> None:
        if search_id != self._active_search_id:
            return
        self.search_progress.setMaximum(max(total, 1))
        self.search_progress.setValue(value)
        self.search_status.setText(f"深度搜索中 {value}/{total}（可能耗时）")

    def _hide_search_progress(self) -> None:
        self.search_progress.setVisible(False)
        self.search_cancel_btn.setVisible(False)

    def cancel_search(self) -> None:
        if self.search_progress.isVisible():
            self._search_cancel.set()
            self.search_cancel_btn.setEnabled(False)
            self.search_status.setText("正在取消搜索...")

    def _select_deep_candidates(self) -> list[dict]:
        max_count = self.scan_limit.value()
        days = self.scan_days.value()
        cutoff = time.time() - days * 86400
        candidates = []
        for item in self._sessions:
            ts = item.get("ts_epoch", 0) or item.get("mtime", 0)
            if ts and ts < cutoff:
                continue
            candidates.append(item)
            if len(candidates) >= max_count:
                break
        return candidates

    def _session_contains_terms(self, path: str, terms: list[str], mode: str) -> bool:
        found = set()
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("type") != "response_item":
                        continue
                    payload = data.get("payload") or {}
                    if payload.get("type") != "message":
                        continue
                    contents = payload.get("content") or []
                    text_parts = []
                    if isinstance(contents, list):
                        for c in contents:
                            if not isinstance(c, dict):
                                continue
                            ctype = c.get("type")
                            if ctype in ("input_text", "output_text", "text"):
                                text_parts.append(c.get("text", ""))
                            elif ctype and "image" in ctype:
                                text_parts.append("[image]")
                    msg = "\n".join([p for p in text_parts if p]).strip().lower()
                    if not msg:
                        continue
                    if mode == "OR":
                        if any(t in msg for t in terms):
                            return True
                    else:
                        for term in terms:
                            if term in msg:
                                found.add(term)
                        if len(found) == len(terms):
                            return True
        except Exception:
            return False
        return False

    def _start_deep_search(self, terms: list[str], mode: str, search_id: int) -> None:
        candidates = self._select_deep_candidates()
        if not candidates:
            self._hide_search_progress()
            self.search_status.setText("深度搜索范围为空。")
            self.detail_text.setPlainText("无匹配会话。")
            return
        total = len(candidates)
        self._search_cancel.clear()
        self._show_search_progress(total)
        self.search_status.setText(f"history 无匹配，开始深度搜索（可能耗时），范围 {total} 条...")

        def runner() -> None:
            matches = []
            for idx, item in enumerate(candidates, 1):
                if self._search_cancel.is_set() or search_id != self._active_search_id:
                    break
                path = item.get("path", "")
                if path and self._session_contains_terms(path, terms, mode):
                    matches.append(item)
                if idx == total or idx % 3 == 0:
                    run_in_ui(lambda i=idx, t=total, sid=search_id: self._update_search_progress(i, t, sid))
            canceled = self._search_cancel.is_set() or search_id != self._active_search_id

            def done() -> None:
                if search_id != self._active_search_id:
                    return
                self._hide_search_progress()
                if canceled:
                    if matches:
                        self.search_status.setText(f"搜索已取消（已命中 {len(matches)} 条）。")
                        self._apply_list(matches)
                    else:
                        self.search_status.setText("搜索已取消。")
                        self._apply_list([], show_empty=True)
                else:
                    if matches:
                        self.search_status.setText(f"深度搜索完成：命中 {len(matches)} 条。")
                        self._apply_list(matches)
                    else:
                        self.search_status.setText("深度搜索完成：无匹配。")
                        self._apply_list([], show_empty=True)

            run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

    def apply_filter(self) -> None:
        raw = self.search_edit.text()
        terms, mode = self._parse_keywords(raw)
        self._search_cancel.set()
        self._active_search_id += 1
        current_id = self._active_search_id

        if not terms:
            self.search_status.setText("")
            self._hide_search_progress()
            self._apply_list(self._sessions)
            return

        matched = []
        for item in self._sessions:
            sid = item.get("id", "")
            text = self._history_index.get(sid, "")
            if self._match_text(text, terms, mode):
                matched.append(item)
        if matched:
            self.search_status.setText(f"history 命中 {len(matched)} 条。")
            self._hide_search_progress()
            self._apply_list(matched)
            return

        self._apply_list([], show_empty=False)
        self._start_deep_search(terms, mode, current_id)

    def on_select(self, index: int) -> None:
        if index < 0:
            return
        item = self.list_widget.item(index)
        if not item:
            return
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, dict):
            return
        self._render_detail(data)

    def _reload_current_detail(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        data = item.data(QtCore.Qt.UserRole)
        if isinstance(data, dict):
            self._render_detail(data)

    def _build_rendered_text(self, meta: dict, only_ua: bool) -> str:
        path = meta.get("path", "")
        if not path:
            return ""
        lines = []
        lines.append(f"时间：{meta.get('time_display', '-')}")
        lines.append(f"模型：{meta.get('model', '-')}")
        lines.append(f"分支：{meta.get('branch', '-')}")
        lines.append(f"目录：{meta.get('cwd', '-')}")
        lines.append(f"文件：{path}")
        lines.append("")
        prev_role = None
        separator = "-" * 30
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("type") != "response_item":
                        continue
                    payload = data.get("payload") or {}
                    if payload.get("type") != "message":
                        continue
                    role = payload.get("role") or ""
                    if only_ua and role not in ("user", "assistant"):
                        continue
                    contents = payload.get("content") or []
                    text_parts = []
                    if isinstance(contents, list):
                        for c in contents:
                            if not isinstance(c, dict):
                                continue
                            ctype = c.get("type")
                            if ctype in ("input_text", "output_text", "text"):
                                text_parts.append(c.get("text", ""))
                            elif ctype and "image" in ctype:
                                text_parts.append("[image]")
                    msg = "\n".join([p for p in text_parts if p]).strip()
                    if not msg:
                        continue
                    if prev_role is not None and role != prev_role:
                        lines.append(separator)
                    lines.append(f"[{role}]")
                    lines.append(msg)
                    lines.append("")
                    prev_role = role
        except Exception as exc:
            lines.append(f"读取失败：{exc}")
        return "\n".join(lines).strip()

    def _render_detail(self, meta: dict) -> None:
        path = meta.get("path", "")
        if not path:
            return
        only_ua = self.only_ua_check.isChecked()
        content = self._build_rendered_text(meta, only_ua)
        self.detail_text.setPlainText(content)

    def _show_session_menu(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        menu = QtWidgets.QMenu(self)
        resume_session = menu.addAction("继续该会话（Codex CLI）")
        resume_vscode = menu.addAction("继续该会话（VS Code）")
        menu.addSeparator()
        open_folder = menu.addAction("打开文件夹")
        delete_session = menu.addAction("删除该会话")
        repair_webview = menu.addAction("WebView 修复")
        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == resume_session:
            self._resume_session(item)
        elif action == resume_vscode:
            self._resume_session_vscode(item)
        elif action == open_folder:
            self._open_session_folder(item)
        elif action == delete_session:
            self._delete_session(item)
        elif action == repair_webview:
            self._resume_session_vscode(item, fix_webview=True)

    def _delete_session(self, item: QtWidgets.QListWidgetItem) -> None:
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        fpath = meta.get("path", "")
        sid = meta.get("id", "")
        if not fpath:
            message_warn(self, "提示", "未找到会话文件路径，无法删除")
            return
        path = Path(fpath)
        size_mb = 0.0
        try:
            if path.exists():
                size_mb = path.stat().st_size / (1024 * 1024)
        except Exception:
            size_mb = 0.0

        prompt = f"将删除该会话文件：\n{fpath}\n\n大小约 {size_mb:.1f} MB。是否继续？"
        if sid:
            prompt += "\n\n同时会清理 history.jsonl 中该会话关联记录。"
        reply = QtWidgets.QMessageBox.question(self, "确认删除", prompt)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            message_error(self, "失败", str(exc))
            return

        if sid:
            self._cleanup_history({sid})
        self.refresh_index()
        self.detail_text.setPlainText("已删除该会话。")

    def _open_session_folder(self, item: QtWidgets.QListWidgetItem) -> None:
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        path = meta.get("path", "")
        if not path:
            return
        folder = os.path.dirname(path)
        if not folder:
            return
        try:
            os.startfile(folder)
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def _resume_session(self, item: QtWidgets.QListWidgetItem) -> None:
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        sid = meta.get("id", "")
        if not sid:
            message_warn(self, "提示", "未找到会话 ID，无法继续")
            return
        cwd = meta.get("cwd", "")
        if cwd and not os.path.isdir(cwd):
            cwd = ""
        exe = find_codex_exe()
        if not exe:
            message_warn(self, "提示", "未检测到 codex 命令，请先安装")
            return
        args = ["resume", sid]
        if cwd:
            args += ["--cd", cwd]
        env = os.environ.copy()
        run_cwd = cwd or None
        try:
            exe_lower = exe.lower()
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)

                def _ps_quote(value: str) -> str:
                    return "'" + value.replace("'", "''") + "'"

                ps_args = " ".join(_ps_quote(a) for a in args)
                ps_cmd = f"& {_ps_quote(exe)} {ps_args}"
                if cwd:
                    ps_cmd = f"Set-Location -LiteralPath {_ps_quote(cwd)}; {ps_cmd}"
                ps_encoded = base64.b64encode(ps_cmd.encode("utf-16le")).decode("ascii")

                wt = shutil.which("wt")
                if wt:
                    wt_cmd = ["wt", "-d", cwd or os.getcwd(), "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", ps_encoded]
                    subprocess.Popen(wt_cmd, env=env)
                else:
                    ps_exec = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", ps_encoded]
                    subprocess.Popen(ps_exec, env=env, cwd=run_cwd, creationflags=creationflags)
            else:
                cmd = [exe] + args
                subprocess.Popen(cmd, env=env, cwd=run_cwd)
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def _resume_session_vscode(self, item: QtWidgets.QListWidgetItem, fix_webview: bool = False) -> None:
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        cwd = meta.get("cwd", "")
        if not cwd or not os.path.isdir(cwd):
            message_warn(self, "提示", "未找到会话工作目录，无法在 VS Code 中继续")
            return
        sid = meta.get("id", "")
        if not sid:
            message_warn(self, "提示", "未找到会话 ID，无法继续")
            return
        if fix_webview:
            def worker() -> None:
                self._kill_vscode_processes()
                self._clear_vscode_cache(self._get_saved_vscode_install_dir())
                run_in_ui(lambda: self._launch_vscode_for_session(cwd, sid))

            threading.Thread(target=worker, daemon=True).start()
            return
        self._launch_vscode_for_session(cwd, sid)

    def _open_url(self, url: str) -> tuple[bool, str]:
        try:
            if os.name == "nt":
                os.startfile(url)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _build_vscode_thread_uris(self, sid: str) -> List[str]:
        safe_sid = urlquote((sid or "").strip(), safe="")
        if not safe_sid:
            return []
        return [
            f"vscode://openai.chatgpt/local/{safe_sid}",
            f"vscode://openai.chatgpt/thread-overlay/{safe_sid}",
            f"vscode://openai.chatgpt/remote/{safe_sid}",
        ]

    def _log_vscode_uri_debug(self, sid: str, cwd: str, phase: str, attempts: list[dict], opened_uri: str) -> None:
        lines = [
            f"phase={phase}",
            f"session_id={sid or '-'}",
            f"cwd={cwd or '-'}",
            f"opened_uri={opened_uri or '-'}",
            f"attempt_count={len(attempts)}",
        ]
        for idx, attempt in enumerate(attempts, 1):
            status = "OK" if attempt.get("ok") else "FAIL"
            uri = attempt.get("uri", "")
            err = (attempt.get("error") or "").strip()
            if err:
                lines.append(f"{idx}. {status} | uri={uri} | error={err}")
            else:
                lines.append(f"{idx}. {status} | uri={uri}")
        log_diagnosis("VS Code URI Debug", "\n".join(lines))

    def _try_open_vscode_thread_by_uri(self, sid: str, cwd: str = "") -> bool:
        uris = self._build_vscode_thread_uris(sid)
        if not uris:
            self._log_vscode_uri_debug(sid, cwd, "first-empty", [], "")
            return False

        attempts: list[dict] = []
        opened_uri = ""
        for uri in uris:
            ok, err = self._open_url(uri)
            attempts.append({"uri": uri, "ok": ok, "error": err})
            if ok:
                opened_uri = uri
                break

        self._log_vscode_uri_debug(sid, cwd, "first", attempts, opened_uri)

        if opened_uri:
            def retry() -> None:
                time.sleep(1.2)
                retry_attempts: list[dict] = []
                retry_opened_uri = ""
                for uri in uris:
                    ok, err = self._open_url(uri)
                    retry_attempts.append({"uri": uri, "ok": ok, "error": err})
                    if ok:
                        retry_opened_uri = uri
                        break
                self._log_vscode_uri_debug(sid, cwd, "retry", retry_attempts, retry_opened_uri)

            threading.Thread(target=retry, daemon=True).start()
            return True

        return False

    def _launch_vscode_for_session(self, cwd: str, sid: str = "") -> None:
        code_cli = self._find_vscode_cli()
        args = None
        if code_cli and self._vscode_supports_command(code_cli):
            args = [code_cli, "-r", cwd, "--command", "chatgpt.openSidebar"]
        else:
            if code_cli:
                args = [code_cli, "-r", cwd]
            else:
                code_exe = self._find_vscode_exe()
                if code_exe:
                    args = [code_exe, cwd]
            self._ensure_open_on_startup(Path(cwd))
        if not args:
            message_warn(self, "提示", "未找到 VS Code，可先安装或在 PATH 中启用 code 命令")
            return
        try:
            _popen_hidden_cmd_on_windows(args)
        except Exception as exc:
            message_error(self, "失败", str(exc))
            return

        if self._try_open_vscode_thread_by_uri(sid, cwd):
            message_info(
                self,
                "提示",
                "已尝试通过 URI 按会话 ID 打开该会话；如插件版本暂不支持，将自动回退为仅打开该会话工作目录。",
            )
            return

        message_info(
            self,
            "提示",
            "当前环境未能通过 URI 按会话ID直达会话，已打开该会话工作目录。",
        )

    def _get_saved_vscode_install_dir(self) -> Optional[Path]:
        saved = self.state.vscode_install_dir if isinstance(self.state.vscode_install_dir, str) else None
        if saved:
            path = Path(saved)
            if path.exists():
                return path
        return None

    def _find_vscode_cli(self) -> Optional[str]:
        for name in ("code", "code.cmd", "code.exe", "code-insiders", "code-insiders.cmd"):
            path = shutil.which(name)
            if path:
                return path
        return None

    def _find_vscode_exe_in_dir(self, root: Path) -> Optional[str]:
        candidates = [
            root / "Code.exe",
            root / "Code - Insiders.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def _find_vscode_exe(self) -> Optional[str]:
        saved = self.state.vscode_install_dir if isinstance(self.state.vscode_install_dir, str) else None
        if saved:
            root = Path(saved)
            if root.exists():
                exe = self._find_vscode_exe_in_dir(root)
                if exe:
                    return exe
        candidates = []
        local = os.environ.get("LOCALAPPDATA")
        program = os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES")
        program_x86 = os.environ.get("ProgramFiles(x86)") or os.environ.get("PROGRAMFILES(X86)")
        if local:
            candidates.append(Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(local) / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        if program:
            candidates.append(Path(program) / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(program) / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        if program_x86:
            candidates.append(Path(program_x86) / "Microsoft VS Code" / "Code.exe")
            candidates.append(Path(program_x86) / "Microsoft VS Code Insiders" / "Code - Insiders.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def _vscode_supports_command(self, code_cli: str) -> bool:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run([code_cli, "--help"], capture_output=True, text=True, timeout=3, creationflags=creationflags)
        except Exception:
            return False
        output = (proc.stdout or "") + (proc.stderr or "")
        return "--command" in output

    def _load_jsonc(self, text: str) -> dict:
        no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        no_line = re.sub(r"//.*", "", no_block)
        try:
            return json.loads(no_line) if no_line.strip() else {}
        except Exception:
            return {}

    def _ensure_open_on_startup(self, workspace: Path) -> bool:
        settings_path = workspace / ".vscode" / "settings.json"
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            raw = settings_path.read_text(encoding="utf-8", errors="ignore") if settings_path.exists() else ""
            data = self._load_jsonc(raw)
            data["chatgpt.openOnStartup"] = True
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def _kill_vscode_processes(self) -> None:
        targets = [
            "Code.exe",
            "Code - Insiders.exe",
            "msedgewebview2.exe",
            "ServiceHub.RoslynCodeAnalysisService.exe",
            "ServiceHub.Host.Node.x64.exe",
            "ServiceHub.TestWindowStoreHost.exe",
        ]
        for name in targets:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True, text=True)
            except Exception:
                continue

    def _clear_vscode_cache(self, install_dir: Optional[Path] = None) -> None:
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        paths = []
        channel = None
        portable_user_data = None
        if install_dir:
            if (install_dir / "Code - Insiders.exe").is_file():
                channel = "insiders"
            elif (install_dir / "Code.exe").is_file():
                channel = "stable"
            portable_root = install_dir / "data" / "user-data"
            if portable_root.is_dir():
                portable_user_data = portable_root
        if portable_user_data:
            base = portable_user_data
            paths += [
                base / "WebView",
                base / "CachedData",
                base / "Cache",
                base / "GPUCache",
                base / "Local Storage",
                base / "Service Worker" / "CacheStorage",
                base / "Service Worker" / "ScriptCache",
                base / "User" / "workspaceStorage",
                base / "User" / "globalStorage",
            ]
        else:
            if channel == "stable":
                names = ["Code"]
            elif channel == "insiders":
                names = ["Code - Insiders"]
            else:
                names = ["Code", "Code - Insiders"]
            if appdata:
                base = Path(appdata)
                for name in names:
                    root = base / name
                    paths += [
                        root / "WebView",
                        root / "CachedData",
                        root / "Cache",
                        root / "GPUCache",
                        root / "Local Storage",
                        root / "Service Worker" / "CacheStorage",
                        root / "Service Worker" / "ScriptCache",
                    ]
            if local:
                base = Path(local) / "Microsoft"
                if channel == "stable":
                    local_names = ["Code"]
                elif channel == "insiders":
                    local_names = ["Code - Insiders"]
                else:
                    local_names = ["Code", "Code - Insiders"]
                for name in local_names:
                    root = base / name
                    paths += [
                        root / "User" / "workspaceStorage",
                        root / "User" / "globalStorage",
                    ]
                paths.append(Path(local) / "Temp" / "Code")
        for p in paths:
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink(missing_ok=True)
            except Exception:
                continue

    def export_json(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            message_warn(self, "提示", "请先选择会话")
            return
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        path = meta.get("path", "")
        if not path:
            return
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出 JSON", "session.json", "JSON (*.json)")
        if not file_path:
            return
        only_ua = self.only_ua_check.isChecked()
        items = []
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if not only_ua:
                        items.append(data)
                        continue
                    if data.get("type") == "session_meta":
                        items.append(data)
                        continue
                    if data.get("type") != "response_item":
                        continue
                    payload = data.get("payload") or {}
                    role = payload.get("role") or ""
                    if role in ("user", "assistant"):
                        items.append(data)
            rendered_text = self._build_rendered_text(meta, only_ua)
            payload = {"items": items, "rendered_text": rendered_text}
            with open(file_path, "w", encoding="utf-8") as out:
                json.dump(payload, out, ensure_ascii=False, indent=2)
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def export_markdown(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            message_warn(self, "提示", "请先选择会话")
            return
        meta = item.data(QtCore.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出 Markdown", "session.md", "Markdown (*.md)")
        if not file_path:
            return
        try:
            only_ua = self.only_ua_check.isChecked()
            content = self._build_rendered_text(meta, only_ua)
            with open(file_path, "w", encoding="utf-8") as out:
                out.write(content)
        except Exception as exc:
            message_error(self, "失败", str(exc))

    def run_cleanup(self) -> None:
        mode = self.clean_mode.currentIndex()
        if not self._sessions:
            message_warn(self, "提示", "暂无可清理的会话")
            return
        targets = []
        if mode == 0:
            date = self.clean_date.date().toPython()
            cutoff = datetime.combine(date, datetime.min.time()).timestamp()
            for item in self._sessions:
                ts = item.get("ts_epoch", 0) or item.get("mtime", 0)
                if ts and ts < cutoff:
                    targets.append(item)
        else:
            size_mb = self.clean_size.value()
            limit = size_mb * 1024 * 1024
            for item in self._sessions:
                if item.get("size", 0) >= limit:
                    targets.append(item)
        if not targets:
            message_info(self, "提示", "没有匹配的会话文件")
            return
        total_mb = sum(i.get("size", 0) for i in targets) / (1024 * 1024)
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清理",
            f"将删除 {len(targets)} 个会话文件，约 {total_mb:.1f} MB。是否继续？",
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        deleted_ids = set()
        for item in targets:
            fpath = item.get("path", "")
            sid = item.get("id", "")
            if not fpath:
                continue
            try:
                Path(fpath).unlink(missing_ok=True)
                if sid:
                    deleted_ids.add(sid)
            except Exception:
                continue
        if self.clean_history.isChecked() and deleted_ids:
            self._cleanup_history(deleted_ids)
        self.refresh_index()

    def _cleanup_history(self, deleted_ids: set[str]) -> None:
        history = Path.home() / ".codex" / "history.jsonl"
        if not history.exists():
            return
        tmp = history.with_suffix(".jsonl.tmp")
        try:
            with history.open("r", encoding="utf-8", errors="ignore") as fh, tmp.open("w", encoding="utf-8") as out:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    sid = data.get("session_id") or ""
                    if sid in deleted_ids:
                        continue
                    out.write(json.dumps(data, ensure_ascii=False) + "\n")
            tmp.replace(history)
        except Exception:
            return


class OpenAIStatusPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("OpenAI官网状态")
        header.setFont(self._header_font())
        layout.addWidget(header)

        status_group = QtWidgets.QGroupBox("OpenAI官网组件状态")
        apply_white_shadow(status_group)
        status_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        status_layout = QtWidgets.QVBoxLayout(status_group)
        self.status_text = QtWidgets.QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMinimumHeight(200)
        self.status_text.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.status_text.setPlainText("尚未获取状态。")
        status_layout.addWidget(self.status_text)
        layout.addWidget(status_group, 1)

        action_row = QtWidgets.QHBoxLayout()
        self.refresh_status_btn = QtWidgets.QPushButton("刷新状态")
        self.refresh_status_btn.setMinimumWidth(110)
        self.refresh_status_btn.setStyleSheet("padding: 4px 10px;")
        self.refresh_status_btn.clicked.connect(self.refresh_status)
        self.open_status_btn = QtWidgets.QPushButton("打开status.openai.com页")
        self.open_status_btn.setMinimumWidth(220)
        self.open_status_btn.setStyleSheet("padding: 4px 10px;")
        self.open_status_btn.clicked.connect(self.open_status_page)
        action_row.addWidget(self.refresh_status_btn)
        action_row.addWidget(self.open_status_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        info_group = QtWidgets.QGroupBox("数据来源说明")
        apply_white_shadow(info_group)
        info_layout = QtWidgets.QVBoxLayout(info_group)
        info_text = QtWidgets.QLabel(
            ""
            "<ul style='margin:4px 0 0 18px; padding:0;'>"
            "<li>OpenAI 官方状态页 API：<a href='https://status.openai.com/api/v2/summary.json'>https://status.openai.com/api/v2/summary.json</a></li>"
            "<li>本页展示的组件状态来自上述 API 的 components 列表</li>"
            "</ul>"
        )
        info_text.setWordWrap(True)
        info_text.setOpenExternalLinks(True)
        info_layout.addWidget(info_text)
        layout.addWidget(info_group)

        self._status_url = "https://status.openai.com"
        self._status_checked = False

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        if not self._status_checked:
            self._status_checked = True
            self.refresh_status(auto=True)

    def open_status_page(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._status_url))

    def refresh_status(self, auto: bool = False) -> None:
        self.refresh_status_btn.setEnabled(False)
        self.status_text.setPlainText("正在获取 OpenAI 状态...")

        def runner() -> None:
            try:
                content = self._get_status_summary()
                err = ""
            except Exception as exc:
                content = ""
                err = str(exc)

            def done() -> None:
                self.refresh_status_btn.setEnabled(True)
                if content:
                    self.status_text.setHtml(content)
                else:
                    self.status_text.setPlainText(f"无法获取状态：{err}")

            run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

    def _get_status_summary(self) -> str:
        api_url = "https://status.openai.com/api/v2/summary.json"
        req = urllib_request.Request(api_url, headers={"User-Agent": "CodexSwitcher"})
        with urllib_request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            return "无法解析状态数据。"

        comp_map = {}
        for comp in data.get("components", []) or []:
            if isinstance(comp, dict):
                name = comp.get("name")
                if isinstance(name, str):
                    comp_map[name] = comp

        status = data.get("status") or {}
        indicator = status.get("indicator") or "-"
        desc = status.get("description") or "-"

        header = f"总体状态：{html.escape(str(desc))} ({html.escape(str(indicator))})"

        status_colors = {
            "under_maintenance": "#5bc0de",
            "degraded_performance": "#f0ad4e",
            "partial_outage": "#fd7e14",
            "major_outage": "#d9534f",
            "unknown": "#888888",
        }

        abnormal: list[tuple[str, str]] = []
        normal: list[str] = []
        for comp in data.get("components", []) or []:
            if not isinstance(comp, dict):
                continue
            name = comp.get("name")
            if not isinstance(name, str):
                continue
            raw_status = comp.get("status", "unknown")
            status_text = STATUS_TEXT.get(raw_status, raw_status)
            line = f"- [{status_text}] {name}"
            line = html.escape(line)
            if raw_status == "operational":
                normal.append(line)
            else:
                color = status_colors.get(raw_status, "#d9534f")
                abnormal.append((line, color))

        html_lines = [header, ""]
        if abnormal:
            html_lines.append("<b>异常/需关注：</b>")
            for line, color in abnormal:
                html_lines.append(f"<span style='color:{color};'>{line}</span>")
            html_lines.append("")
        html_lines.append("<b>组件状态：</b>")
        html_lines.extend(normal)

        return "<br>".join(html_lines).strip()

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        icon_path = resolve_asset("icon_tray.png")
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.resize(860, 620)
        self.setMinimumSize(860, 620)
        self.state = AppState()
        central = QtWidgets.QWidget()
        central.setObjectName("appRoot")
        root = QtWidgets.QHBoxLayout(central)
        self.setCentralWidget(central)

        nav_widget = QtWidgets.QWidget()
        nav = QtWidgets.QVBoxLayout(nav_widget)
        root.addWidget(nav_widget)
        nav_widget.setFixedWidth(140)

        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)

        self.pages = {}
        self.pages["account"] = AccountPage(self.state)
        if hasattr(self.pages["account"], "refresh_pages"):
            self.pages["account"].refresh_pages = self.refresh_pages
        self.pages["config_toml"] = ConfigTomlPage(self.state)
        self.pages["opencode"] = OpencodeConfigPage(self.state)
        self.pages["network"] = NetworkDiagnosticsPage(self.state)
        self.pages["codex_status"] = CodexStatusPage(self.state)
        self.pages["vscode_plugin"] = VSCodePluginPage(self.state)
        self.pages["skills"] = SkillsPage(self.state)
        self.pages["settings"] = SettingsPage(self.state, on_update_count_changed=self._on_update_count_changed)
        self.pages["openai_status"] = OpenAIStatusPage(self.state)
        self.pages["sessions"] = SessionManagerPage(self.state)

        for page in self.pages.values():
            self.stack.addWidget(page)

        self.buttons = []
        self._nav_button_map: Dict[str, QtWidgets.QPushButton] = {}
        self._settings_nav_btn: Optional[NavBadgeButton] = None
        self._add_nav_button(nav, "Codex CLI状态", "codex_status")
        self._add_nav_button(nav, "VSCode Codex", "vscode_plugin")
        self._add_nav_button(nav, "config.toml配置", "config_toml")
        self._add_nav_button(nav, "opencode 配置", "opencode")
        self._add_nav_button(nav, "多账号切换", "account")
        self._add_nav_button(nav, "Codex会话管理", "sessions")
        self._add_nav_button(nav, "Skill 管理", "skills")
        self._add_nav_button(nav, "中转站接口", "network")
        self._add_nav_button(nav, "OpenAI官网状态", "openai_status")
        self._add_nav_button(nav, "检查更新", "settings")
        nav.addStretch(1)

        self._update_check_timer = QtCore.QTimer(self)
        self._update_check_timer.setInterval(15 * 60 * 1000)
        self._update_check_timer.timeout.connect(self._auto_check_updates)
        self._update_check_timer.start()
        QtCore.QTimer.singleShot(1200, self._auto_check_updates)

        self.show_page("account")

    def _add_nav_button(self, layout: QtWidgets.QVBoxLayout, label: str, key: str) -> None:
        if key == "settings":
            btn = NavBadgeButton(label)
            self._settings_nav_btn = btn
        else:
            btn = QtWidgets.QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _: self.show_page(key))
        layout.addWidget(btn)
        self.buttons.append((key, btn))
        self._nav_button_map[key] = btn

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        for k, btn in self.buttons:
            btn.setChecked(k == key)
        if hasattr(page, "on_show"):
            getattr(page, "on_show")()

    def refresh_pages(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "on_show"):
                getattr(page, "on_show")()

    def _auto_check_updates(self) -> None:
        page = self.pages.get("settings")
        if isinstance(page, SettingsPage):
            page.check_update(auto=True)

    def _on_update_count_changed(self, count: int) -> None:
        if self._settings_nav_btn is not None:
            self._settings_nav_btn.set_badge_count(count)


def apply_material_theme(app: QtWidgets.QApplication) -> bool:
    if apply_stylesheet is None:
        return False
    try:
        apply_stylesheet(app, theme='light_teal.xml')
        return True
    except Exception:
        return False


def apply_light_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(245, 245, 245))
    palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(245, 245, 245))
    palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(255, 255, 255))
    palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(245, 245, 245))
    palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor(200, 0, 0))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(0, 136, 136))
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QWidget#appRoot {
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 #F4A3D6, stop: 0.4 #F1A7E0, stop: 0.8 #C5B2FF, stop: 1 #A8D6FF
            );
        }
        QLabel {
            color: #2C2540;
        }
        QGroupBox {
            border: 1px solid #FFFFFF;
            border-radius: 8px;
            margin-top: 10px;
            background-color: rgba(255, 255, 255, 200);
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 8px;
            color: #2C2540;
            background: transparent;
        }
        QLineEdit, QPlainTextEdit, QListWidget, QTableWidget {
            border: 1px solid rgba(108, 99, 255, 150);
            border-radius: 6px;
            background: rgba(255, 255, 255, 230);
            color: #2C2540;
        }
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTableWidget:focus {
            border: 1px solid rgba(108, 99, 255, 220);
        }
        QPushButton {
            background: rgba(255, 255, 255, 220);
            border: 1px solid rgba(108, 99, 255, 140);
            border-radius: 6px;
            padding: 4px 10px;
            color: #2C2540;
        }
        QPushButton:hover {
            border: 1px solid rgba(108, 99, 255, 200);
        }
        QPushButton:checked {
            background: rgba(108, 99, 255, 40);
            border: 1px solid rgba(108, 99, 255, 220);
        }
        QHeaderView::section {
            background-color: rgba(255, 255, 255, 200);
            border: 1px solid rgba(108, 99, 255, 140);
            padding: 4px 6px;
            color: #2C2540;
        }
"""
    )


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    if not apply_material_theme(app):
        apply_light_theme(app)
    icon_path = resolve_asset("icon_tray.png")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()



