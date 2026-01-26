#!/usr/bin/env python3
"""PySide6 UI for Codex Switcher (UI-only refactor)."""

from __future__ import annotations

import sys
import json
import os
import subprocess
import re
import shutil
import threading
import ctypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
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
    test_model,
    upsert_account,
)


APP_TITLE = "Codex Switcher"


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

        list_width = 320
        form_width = 360
        card_gap = 12
        account_group_width = list_width + form_width + card_gap + 10

        current_group = QtWidgets.QGroupBox("当前账号")
        apply_white_shadow(current_group)
        current_group.setFixedWidth(account_group_width)
        current_layout = QtWidgets.QHBoxLayout(current_group)
        self.current_label = QtWidgets.QLabel("未选择")
        self.current_label.setWordWrap(False)
        self.current_label.setToolTip("未选择")
        current_layout.addWidget(self.current_label)
        current_layout.addStretch(1)
        layout.addWidget(current_group, alignment=QtCore.Qt.AlignLeft)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(card_gap)
        layout.addLayout(body)

        account_group = QtWidgets.QGroupBox("多账号管理")
        apply_white_shadow(account_group)
        account_group.setFixedWidth(account_group_width)
        account_layout = QtWidgets.QHBoxLayout(account_group)
        account_layout.setSpacing(card_gap)

        # 左侧列表
        list_panel = QtWidgets.QWidget()
        list_panel.setFixedWidth(list_width)
        list_layout = QtWidgets.QVBoxLayout(list_panel)
        list_title = QtWidgets.QLabel("账号列表")
        list_layout.addWidget(list_title)
        self.list_widget = QtWidgets.QListWidget()
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
        form_panel.setFixedWidth(form_width)
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
        body.addStretch(1)

        hint_group = QtWidgets.QGroupBox("提示")
        apply_white_shadow(hint_group)
        hint_group.setFixedWidth(account_group_width)
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
        layout.addWidget(hint_group, alignment=QtCore.Qt.AlignLeft)

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


class ModelProbePage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("账号池可用模型探测")
        header.setFont(self._header_font())
        layout.addWidget(header)

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
        self.start_btn = QtWidgets.QPushButton("开始探测")
        self.start_btn.clicked.connect(self.start_probe)
        cfg_layout.addWidget(QtWidgets.QLabel("Base URL"))
        cfg_layout.addWidget(self.base_edit, 1)
        cfg_layout.addWidget(QtWidgets.QLabel("API Key"))
        cfg_layout.addWidget(self.key_edit, 1)
        cfg_layout.addWidget(QtWidgets.QLabel("重试次数"))
        cfg_layout.addWidget(self.retries_spin)
        cfg_layout.addWidget(QtWidgets.QLabel("超时(s)"))
        cfg_layout.addWidget(self.timeout_spin)
        cfg_layout.addWidget(self.start_btn)
        layout.addWidget(cfg_group)

        body = QtWidgets.QHBoxLayout()
        layout.addLayout(body)

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
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["模型", "OK", "Endpoint", "错误"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        result_layout.addWidget(self.table)
        body.addWidget(result_group)
        body.setStretch(0, 1)
        body.setStretch(1, 3)

        self.status_label = QtWidgets.QLabel("就绪")
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        account = self.state.active_account
        if account:
            self.base_edit.setText(account.get("base_url", ""))
            self.key_edit.setText(account.get("api_key", ""))

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
        models = [model]
        retries = int(self.retries_spin.value())
        timeout = int(self.timeout_spin.value())
        self.status_label.setText("探测中...")
        self.table.setRowCount(0)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        def runner() -> None:
            for model in models:
                result = test_model(base, headers, model, retries=retries, wait_seconds=2, timeout=timeout)
                run_in_ui(lambda r=result: self.append_result(r))
            run_in_ui(lambda: self.status_label.setText("完成"))

        threading.Thread(target=runner, daemon=True).start()

    def append_result(self, result: Dict[str, object]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [result.get("model"), result.get("ok"), result.get("endpoint"), result.get("error")]
        for col, value in enumerate(values):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))


class NetworkDiagnosticsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("中转站接口")
        header.setFont(self._header_font())
        layout.addWidget(header)

        diag_group = QtWidgets.QGroupBox("关键诊断")
        apply_white_shadow(diag_group)
        diag_layout = QtWidgets.QVBoxLayout(diag_group)

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
        self.detail_text.setMinimumHeight(60)
        diag_layout.addWidget(self.detail_text)

        copy_row = QtWidgets.QHBoxLayout()
        self.copy_urls_btn = QtWidgets.QPushButton("复制可用接口(URL)")
        self.copy_urls_btn.clicked.connect(self.copy_supported_urls)
        copy_row.addWidget(self.copy_urls_btn)
        copy_row.addStretch(1)
        diag_layout.addLayout(copy_row)

        layout.addWidget(diag_group)

        self.model_page = ModelProbePage(state)
        layout.addWidget(self.model_page)
        layout.addStretch(1)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

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
        if hasattr(self.model_page, "on_show"):
            self.model_page.on_show()

    
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
        self.conclusion_label.setText("结论：诊断中...")
        self.detail_text.setPlainText("")

        def runner() -> None:
            try:
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

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if org_id:
                    headers["OpenAI-Organization"] = org_id

                def get_json(url: str, timeout: int = 60) -> tuple[bool, str]:
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
                        return post_json(url, headers, payload, timeout=60)
                    if endpoint == "/embeddings":
                        payload = {"model": embedding_model, "input": "hello"}
                        return post_json(url, headers, payload, timeout=60)
                    if endpoint == "/chat/completions":
                        payload = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
                        return post_json(url, headers, payload, timeout=60)
                    if endpoint == "/completions":
                        payload = {"model": model, "prompt": "hello"}
                        return post_json(url, headers, payload, timeout=60)
                    payload = {"model": model, "input": "hello"}
                    return post_json(url, headers, payload, timeout=60)

                model_supported: Optional[bool] = None
                model_source = ""

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

                for label, ep, _url, ok, body in results:
                    if ok and ep in ("/responses", "/chat/completions", "/completions"):
                        set_model_support(True, ep)
                    if ep == "/models" and ok:
                        models = parse_models(body)
                        if models:
                            set_model_support(model in models, "/models")
                if model_supported is None:
                    for label, ep, _url, ok, body in results:
                        if (ok is False) and ep in ("/responses", "/chat/completions", "/completions") and is_model_error(body):
                            set_model_support(False, ep)

                model_text = "可用" if model_supported is True else "不可用" if model_supported is False else "未知"
                model_hint = f"（来源: {model_source}）" if model_source else ""

                errors_text = " ".join(str(body).lower() for _label, _ep, _url, _ok, body in results)
                supported = [label for label, _ep, _url, ok, _body in results if ok]
                supported_urls = []
                for label, _ep, url, ok, _body in results:
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

                lines = []
                lines.append(f"Base URL: {base}")
                lines.append(f"Base Host: {base_host}")
                lines.append(
                    "Base 连通："
                    f"Ping={fmt_ms(ping_avg)} / "
                    f"HTTP={fmt_ms(http_avg)} / "
                    f"Port={'OK' if port_ok else 'FAIL' if port_ok is not None else '不可用'}"
                )
                lines.append(f"\n可用接口：{supported_text}")
                if supported_urls:
                    lines.append("可用接口(URL)：")
                    for url in supported_urls:
                        lines.append(f"- {url}")
                lines.append(f"模型可用性（{model}）：{model_text}{model_hint}")
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
                self._supported_labels = supported
                self._supported_urls = supported_urls
                if not success_endpoint:
                    log_diagnosis("诊断失败", f"{conclusion}\n{detail}")
                def done() -> None:
                    self.run_btn.setEnabled(True)
                    self.conclusion_label.setText(conclusion)
                    self.detail_text.setPlainText(detail)

                run_in_ui(done)
            except Exception as exc:
                log_exception(exc)

                def done() -> None:
                    self.run_btn.setEnabled(True)
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
        debug_layout = QtWidgets.QVBoxLayout(debug_group)
        self.debug_text = QtWidgets.QPlainTextEdit()
        self.debug_text.setReadOnly(True)
        self.debug_text.setMinimumHeight(140)
        debug_layout.addWidget(self.debug_text)

        debug_btn_row = QtWidgets.QHBoxLayout()
        self.copy_debug_btn = QtWidgets.QPushButton("复制诊断")
        self.copy_debug_btn.clicked.connect(self.copy_debug)
        debug_btn_row.addWidget(self.copy_debug_btn)
        debug_btn_row.addStretch(1)
        debug_layout.addLayout(debug_btn_row)
        layout.addWidget(debug_group)

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
        header = QtWidgets.QLabel("设置 / 关于")
        header.setFont(self._header_font())
        layout.addWidget(header)

        note = QtWidgets.QLabel("安装命令：npm i -g @openai/codex\n安装后请重新启动程序或终端")
        note.setStyleSheet("color: #AAA;")
        layout.addWidget(note)
        layout.addStretch(1)

    def _header_font(self) -> QtGui.QFont:
        font = QtGui.QFont("Segoe UI", 12)
        font.setBold(True)
        return font

    def on_show(self) -> None:
        return




class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        icon_path = resolve_asset("icon_tray.png")
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.resize(860, 620)
        self.setFixedSize(860, 620)
        self.state = AppState()
        central = QtWidgets.QWidget()
        central.setObjectName("appRoot")
        root = QtWidgets.QHBoxLayout(central)
        self.setCentralWidget(central)

        nav = QtWidgets.QVBoxLayout()
        root.addLayout(nav)

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

        for page in self.pages.values():
            self.stack.addWidget(page)

        self.buttons = []
        self._add_nav_button(nav, "Codex CLI状态", "codex_status")
        self._add_nav_button(nav, "config.toml配置", "config_toml")
        self._add_nav_button(nav, "opencode 配置", "opencode")
        self._add_nav_button(nav, "多账号切换", "account")
        self._add_nav_button(nav, "中转站接口", "network")
        self._add_nav_button(nav, "设置/关于", "settings")
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



