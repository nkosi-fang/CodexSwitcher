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
from typing import Dict, List, Optional
from urllib import request as urllib_request
from urllib import error as urllib_error
from urllib.parse import urlparse

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
APP_VERSION = "2.0.2"
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

    model_supported: Optional[bool] = None
    model_source = ""
    model_in_list: Optional[bool] = None
    response_model = ""
    response_model_source = ""

    def parse_models(body: str) -> set[str]:
        try:
            data = json.loads(body)
        except Exception:
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
        try:
            data = json.loads(body)
        except Exception:
            return ""
        if isinstance(data, dict):
            model_value = data.get("model")
            if isinstance(model_value, str):
                return model_value
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

    def apply_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.account_items):
            message_warn(self, "提示", "请选择账号")
            return
        account = self.account_items[row]
        apply_account_config(self.state.store, account)
        apply_env_for_account(account)
        set_active_account(self.state.store, account)
        self.state.active_account = account
        self.refresh()
        self.refresh_pages()
        message_info(self, "完成", "账号已应用")

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
        message_info(self, "完成", "账号已保存")

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
        latest_layout.addWidget(self.latest_status)
        latest_layout.addWidget(self.latest_version)
        latest_layout.addWidget(self.latest_hint)
        self.latest_group = latest_group
        layout.addWidget(self.latest_group)
        
        self.compare_status = QtWidgets.QLabel("")
        self.compare_status.setVisible(False)
        self.progress_label = QtWidgets.QLabel("")
        layout.addWidget(self.progress_label)
        layout.addWidget(self.compare_status)

        debug_group = QtWidgets.QGroupBox("诊断信息")
        debug_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        debug_layout = QtWidgets.QVBoxLayout(debug_group)
        self.debug_text = QtWidgets.QPlainTextEdit()
        self.debug_text.setReadOnly(True)
        self.debug_text.setMinimumHeight(140)
        self.debug_text.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        debug_layout.addWidget(self.debug_text)

        debug_btn_row = QtWidgets.QHBoxLayout()
        self.copy_debug_btn = QtWidgets.QPushButton("复制诊断")
        self.copy_debug_btn.clicked.connect(self.copy_debug)
        debug_btn_row.addWidget(self.copy_debug_btn)
        debug_btn_row.addStretch(1)
        debug_layout.addLayout(debug_btn_row)
        layout.addWidget(debug_group)
        layout.setStretch(layout.count() - 1, 1)


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


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

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
        letter_group = QtWidgets.QGroupBox("开发者留言")
        apply_white_shadow(letter_group)
        letter_layout = QtWidgets.QVBoxLayout(letter_group)
        note = QtWidgets.QLabel(
            "各位佬：<br><br>"
            "感谢使用Codex Switcher。因为市面上已经有太多类似CC switch的成熟多账号管理工具。本质上，这就是一个套娃工具，基于对config.toml、auth.json、opencode.json 文件读取和修改，一开始我只是作为一个切换脚本供自己便用的。后来无意中分享给了一位群友，并在他的建议下进一步做了UI界面。<br><br>"
            "我深知如果把它作为一个产品来说，本身是有很多不足和bug的；再者从我自己的使用习惯和角度来说，主力就是codex，所以开发时并没有考虑添加claude code的账号管理功能，以及对一些优秀的国产大模型的支持。我甚至有想过用Rust来重构，因为开源项目本身就是靠情怀支撑的。<br><br>"
            "无奈手里还有太多的活要干（要给自己赚工资），只能利用闲暇之余，再慢慢debug和升级。<br><br>"
            "如果佬对本产品有更好的想法和建议，欢迎交流、反馈，更欢迎您一起""fork+pr""，共同推动这个小工具的进步，这大概是我们在AI大浪潮席卷的时代，能够唯一留下的轻微足迹。<br><br>"
            "反馈渠道:    L站、GitHub或者电子邮件:nkosi.fang@gmail.com<br>"
            "<div style=\"text-align:right;\">nkosi</div>"
        )
        note.setStyleSheet("color: #000;")
        note.setWordWrap(True)
        note.setTextFormat(QtCore.Qt.RichText)
        letter_layout.addWidget(note)
        layout.addWidget(letter_group)
        layout.addStretch(1)
        self._latest_url = f"https://github.com/{APP_REPO}/releases/latest"
        self._checked_once = False
        self._notified = False

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
                self.check_btn.setEnabled(True)
                if ok:
                    self._latest_url = url or self._latest_url
                    self.latest_version.setText(f"最新版本：{latest_ver}")
                    if hasattr(self, "release_notes"):
                        self.release_notes.setPlainText(notes_text or "无更新内容")
                    status_text, has_update = self._compare_versions(APP_VERSION, latest_ver)
                    self.update_status.setText(status_text)
                    if has_update and not self._notified:
                        self._notified = True
                        message_info(self, "发现新版本", f"检测到新版本：{latest_ver}\n请前往发布页下载。")
                else:
                    self.update_status.setText(f"状态：{msg}")
                    if hasattr(self, "release_notes"):
                        self.release_notes.setPlainText(msg)

            run_in_ui(done)

        threading.Thread(target=runner, daemon=True).start()

    def _filter_release_sections(self, body: str) -> str:
        wanted = {"标题", "平台", "变更"}
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
            return "未找到Release中的标题/平台/变更内容。"
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
        cleanup_group = QtWidgets.QGroupBox("清理")
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
        self.count_label.setText(f"共 {shown} 条<b>【ⓘ 提示：鼠标右键可打开文件夹或继续该会话（Codex CLI）】</b>")
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
        open_folder = menu.addAction("打开文件夹")
        resume_session = menu.addAction("继续该会话（Codex CLI）")
        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == open_folder:
            self._open_session_folder(item)
        elif action == resume_session:
            self._resume_session(item)

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
            "<b>数据来源：</b>"
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
        self.pages["settings"] = SettingsPage(self.state)
        self.pages["openai_status"] = OpenAIStatusPage(self.state)
        self.pages["sessions"] = SessionManagerPage(self.state)

        for page in self.pages.values():
            self.stack.addWidget(page)

        self.buttons = []
        self._add_nav_button(nav, "Codex CLI状态", "codex_status")
        self._add_nav_button(nav, "config.toml配置", "config_toml")
        self._add_nav_button(nav, "opencode 配置", "opencode")
        self._add_nav_button(nav, "多账号切换", "account")
        self._add_nav_button(nav, "Codex会话管理", "sessions")
        self._add_nav_button(nav, "中转站接口", "network")
        self._add_nav_button(nav, "OpenAI官网状态", "openai_status")
        self._add_nav_button(nav, "检查更新", "settings")
        nav.addStretch(1)

        self.show_page("account")

    def _add_nav_button(self, layout: QtWidgets.QVBoxLayout, label: str, key: str) -> None:
        btn = QtWidgets.QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _: self.show_page(key))
        layout.addWidget(btn)
        self.buttons.append((key, btn))

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



