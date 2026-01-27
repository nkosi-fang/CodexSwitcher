#!/usr/bin/env python3
"""Shared utilities for Codex Switcher desktop UI."""

from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib import error as urllib_error
from urllib import request as urllib_request


CODEX_DIR = Path.home() / ".codex"
PROFILE_STORE = CODEX_DIR / "codex_profiles.json"
CONFIG_PATH = CODEX_DIR / "config.toml"
AUTH_PATH = CODEX_DIR / "auth.json"
LOG_PATH = CODEX_DIR / "codex_switcher.log"
_WIN_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_WIN_READONLY = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)

TEAM_PROFILE = {
    "name": "Team Official",
    "api_key": "sk-team-xxxx",
    "org_id": "org-xxxx",
    "base_url": "https://api.openai.com/v1",
}

PING_TIMEOUT_MS = 1000
HTTP_TIMEOUT = 3.0

PING_REGEX = re.compile(r"(?:time|时间)[=<]?\s*(\d+)\s*ms", re.IGNORECASE)

def load_store() -> Dict[str, object]:
    if PROFILE_STORE.exists():
        try:
            raw = json.loads(PROFILE_STORE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("检测到损坏的 codex_profiles.json，已使用空模板重新创建。")
            raw = {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    teams = raw.get("teams")
    if not isinstance(teams, dict):
        teams = {}
    raw["profiles"] = profiles
    raw["teams"] = teams
    if "active" not in raw:
        raw["active"] = None
    return raw


def save_store(store: Dict[str, object]) -> None:
    PROFILE_STORE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_STORE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def update_config_base_url(new_url: str) -> None:
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        text = CONFIG_PATH.read_text(encoding="utf-8")
    else:
        text = ""
    line_ending = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    if not lines:
        lines = [
            'model_provider = "codexzh"',
            "",
            "[model_providers.codexzh]",
            f'base_url = "{new_url}"',
        ]
        CONFIG_PATH.write_text(line_ending.join(lines) + line_ending, encoding="utf-8")
        return

    section_start = None
    in_target_section = False
    updated = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip().strip("'\"")
            in_target_section = section_name == "model_providers.codexzh"
            if in_target_section:
                section_start = idx
            continue
        if in_target_section and stripped.startswith("base_url"):
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f'{indent}base_url = "{new_url}"'
            updated = True
            break

    if not updated:
        if section_start is not None:
            insert_at = section_start + 1
            lines.insert(insert_at, f'base_url = "{new_url}"')
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["[model_providers.codexzh]", f'base_url = "{new_url}"'])
    text_out = line_ending.join(lines)
    if not text_out.endswith(line_ending):
        text_out += line_ending
    try:
        safe_write_text(CONFIG_PATH, text_out)
    except PermissionError as err:
        raise PermissionError(
            f"无法写入 {CONFIG_PATH}，请确认文件未被其他程序占用并具有写入权限。"
        ) from err


def update_auth_key(api_key: str) -> None:
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("auth.json 内容无法解析，已重新生成。")
            data = {}
    else:
        data = {}
    data["OPENAI_API_KEY"] = api_key
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        safe_write_text(AUTH_PATH, json.dumps(data, indent=2) + "\n")
    except PermissionError as err:
        raise PermissionError(
            f"无法写入 {AUTH_PATH}，请确认文件未被其他程序占用并具有写入权限。"
        ) from err


def update_auth_org_id(org_id: str) -> None:
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("auth.json 内容无法解析，已重新生成。")
            data = {}
    else:
        data = {}
    if org_id:
        data["OPENAI_ORG_ID"] = org_id
    else:
        data.pop("OPENAI_ORG_ID", None)
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        safe_write_text(AUTH_PATH, json.dumps(data, indent=2) + "\n")
    except PermissionError as err:
        raise PermissionError(
            f"无法写入 {AUTH_PATH}，请确认文件未被其他程序占用并具有写入权限。"
        ) from err


def apply_account_config(store: Dict[str, object], account: Dict[str, str]) -> None:
    update_config_base_url(account.get("base_url", ""))
    update_auth_key(account.get("api_key", ""))
    if account.get("is_team") == "1":
        update_auth_org_id(account.get("org_id", ""))
        name = account.get("name", "")
        store["active"] = f"team:{name}" if name else "team:unknown"
    else:
        update_auth_org_id("")
        name = account.get("name", "")
        store["active"] = name or None
    save_store(store)


def is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def parse_ping_time(output: str) -> Optional[int]:
    match = PING_REGEX.search(output)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _subprocess_hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs: dict = {}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def ping_once(host: str) -> Optional[int]:
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            **_subprocess_hidden_kwargs(),
        )
    except Exception:
        return None
    output = (proc.stdout or "") + (proc.stderr or "")
    return parse_ping_time(output)


def ping_average(host: str, attempts: int) -> Tuple[Optional[float], float]:
    times: List[int] = []
    failures = 0
    for _ in range(attempts):
        value = ping_once(host)
        if value is None:
            failures += 1
        else:
            times.append(value)
    loss_pct = failures / attempts * 100.0 if attempts > 0 else 100.0
    if not times:
        return None, loss_pct
    return sum(times) / len(times), loss_pct


def http_head_average(url: str, api_key: str, attempts: int) -> Optional[float]:
    try:
        import requests
        import urllib3
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(
            "缺少 requests 依赖，请先执行：pip install requests"
        ) from exc
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    headers = {"User-Agent": user_agent}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    verify = True
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host and is_ip_address(host):
        verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session = requests.Session()
    times: List[float] = []
    for _ in range(attempts):
        start = time.perf_counter()
        try:
            resp = session.head(
                url,
                headers=headers,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                verify=verify,
            )
            resp.close()
        except requests.exceptions.SSLError:
            if verify:
                try:
                    resp = session.head(
                        url,
                        headers=headers,
                        timeout=HTTP_TIMEOUT,
                        allow_redirects=True,
                        verify=False,
                    )
                    resp.close()
                except requests.RequestException:
                    return None
            else:
                return None
        except requests.RequestException:
            return None
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    if not times:
        return None
    return sum(times) / len(times)


def is_placeholder_team_profile(profile: Dict[str, str]) -> bool:
    api_key = profile.get("api_key", "")
    org_id = profile.get("org_id", "")
    if not api_key or not org_id:
        return True
    if "xxxx" in api_key or "xxxx" in org_id:
        return True
    return False


def build_accounts(store: Dict[str, object]) -> List[Dict[str, str]]:
    profiles = store["profiles"]
    assert isinstance(profiles, dict)
    teams = store.get("teams")
    if not isinstance(teams, dict):
        teams = {}
    accounts: List[Dict[str, str]] = []
    if not is_placeholder_team_profile(TEAM_PROFILE):
        accounts.append(
            {
                "name": TEAM_PROFILE["name"],
                "api_key": TEAM_PROFILE["api_key"],
                "org_id": TEAM_PROFILE["org_id"],
                "base_url": TEAM_PROFILE["base_url"],
                "is_team": "1",
                "account_type": "team",
            }
        )
    for name in sorted(teams.keys()):
        profile = teams[name]
        accounts.append(
            {
                "name": name,
                "api_key": profile.get("api_key", ""),
                "org_id": profile.get("org_id", ""),
                "base_url": profile.get("base_url", ""),
                "is_team": "1",
                "account_type": "team",
            }
        )
    for name in sorted(profiles.keys()):
        profile = profiles[name]
        base_url = profile.get("base_url", "")
        account_type = profile.get("account_type")
        if not account_type:
            account_type = "official" if base_url == "https://api.openai.com/v1" else "proxy"
        accounts.append(
            {
                "name": name,
                "api_key": profile.get("api_key", ""),
                "base_url": base_url,
                "account_type": account_type,
                "is_team": "0",
            }
        )
    return accounts


def extract_host(base_url: str) -> str:
    if not base_url:
        return ""
    if base_url.startswith("http://") or base_url.startswith("https://"):
        parsed = urlparse(base_url)
        return parsed.hostname or ""
    return base_url


def apply_env_for_account(account: Dict[str, str]) -> None:
    os.environ["OPENAI_API_KEY"] = account.get("api_key", "")
    os.environ["OPENAI_BASE_URL"] = account.get("base_url", "")
    if account.get("is_team") == "1":
        org_id = account.get("org_id", "")
        if org_id:
            os.environ["OPENAI_ORG_ID"] = org_id
        else:
            os.environ.pop("OPENAI_ORG_ID", None)
            print("警告：Team 配置缺少 org_id，已忽略 OPENAI_ORG_ID。")
    else:
        os.environ.pop("OPENAI_ORG_ID", None)
    print(f"当前账号：{account.get('name', '')}")
    print(f"Base URL：{account.get('base_url', '')}")


def _build_codex_search_paths() -> List[str]:
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


def _which_in_paths(cmd: str, paths: List[str]) -> Optional[str]:
    exts = [".exe", ".cmd", ".bat", ".ps1", ""]
    for base in paths:
        for ext in exts:
            name = cmd if cmd.lower().endswith(ext) else f"{cmd}{ext}"
            candidate = Path(base) / name
            if candidate.is_file():
                return str(candidate)
    return None


def pick_best_match(lines: List[str]) -> Optional[str]:
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


def get_where_exe() -> Optional[str]:
    exe = shutil.which("where") or shutil.which("where.exe")
    if exe:
        return exe
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        candidate = Path(system_root) / "System32" / "where.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def find_codex_exe() -> Optional[str]:
    exe = shutil.which("codex")
    if exe:
        return exe
    exe = _which_in_paths("codex", _build_codex_search_paths())
    if exe:
        return exe
    where_exe = get_where_exe()
    if where_exe:
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.run([where_exe, "codex"], capture_output=True, text=True, timeout=2, creationflags=creationflags)
            if proc.returncode == 0:
                lines = (proc.stdout or "").splitlines()
                best = pick_best_match(lines)
                if best:
                    return best
        except Exception:
            return None
    return None


def run_codex_chat() -> None:
    exe = find_codex_exe()
    if not exe:
        raise FileNotFoundError("未找到 codex 命令，请确认已安装并加入 PATH。")
    if exe.lower().endswith(".ps1"):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, "chat", "-m", "gpt-5.2-codex"], check=False)
    else:
        subprocess.run([exe, "chat", "-m", "gpt-5.2-codex"], check=False)




def check_codex_available() -> bool:
    return find_codex_exe() is not None


def get_active_account(store: Dict[str, object]) -> Dict[str, str]:
    active = store.get("active")
    if isinstance(active, str):
        if active.startswith("team:"):
            name = active[5:]
            teams = store.get("teams")
            if isinstance(teams, dict) and name in teams:
                data = dict(teams[name])
                data["name"] = name
                data["is_team"] = "1"
                return data
        else:
            profiles = store.get("profiles")
            if isinstance(profiles, dict) and active in profiles:
                data = dict(profiles[active])
                data["name"] = active
                data["is_team"] = "0"
                return data
    return {}


def set_active_account(store: Dict[str, object], account: Dict[str, str]) -> None:
    name = account.get("name", "")
    if not name:
        store["active"] = None
    elif account.get("is_team") == "1":
        store["active"] = f"team:{name}"
    else:
        store["active"] = name
    save_store(store)


def upsert_account(
    store: Dict[str, object],
    name: str,
    base_url: str,
    api_key: str,
    org_id: str,
    is_team: bool,
    account_type: Optional[str] = None,
) -> None:
    if is_team:
        teams = store.get("teams")
        if not isinstance(teams, dict):
            teams = {}
            store["teams"] = teams
        profiles = store.get("profiles")
        if isinstance(profiles, dict):
            profiles.pop(name, None)
        teams[name] = {"base_url": base_url, "api_key": api_key, "org_id": org_id}
    else:
        profiles = store.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
            store["profiles"] = profiles
        teams = store.get("teams")
        if isinstance(teams, dict):
            teams.pop(name, None)
        profile_data = {"base_url": base_url, "api_key": api_key}
        if account_type:
            profile_data["account_type"] = account_type
        profiles[name] = profile_data
    save_store(store)


def delete_account(store: Dict[str, object], account: Dict[str, str]) -> None:
    name = account.get("name", "")
    if not name:
        return
    if account.get("is_team") == "1":
        teams = store.get("teams")
        if isinstance(teams, dict):
            teams.pop(name, None)
    else:
        profiles = store.get("profiles")
        if isinstance(profiles, dict):
            profiles.pop(name, None)
    active = store.get("active")
    if active in (name, f"team:{name}"):
        store["active"] = None
    save_store(store)


def post_json(url: str, headers: Dict[str, str], payload: Dict[str, object], timeout: int = 90) -> Tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
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


def error_summary(message: str) -> str:
    msg = message.lower()
    if "model" in msg:
        return "model_not_found_or_not_allowed"
    if "401" in msg or "403" in msg:
        return "auth_failed"
    if "404" in msg:
        return "endpoint_not_supported"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "other_error"


def test_model(
    base: str,
    headers: Dict[str, str],
    model: str,
    retries: int = 3,
    wait_seconds: int = 2,
    timeout: int = 90,
) -> Dict[str, object]:
    payload = {"model": model, "input": "ping"}
    last_err = ""
    for i in range(1, retries + 1):
        ok, msg = post_json(f"{base}/responses", headers, payload, timeout=timeout)
        if ok:
            return {"model": model, "ok": True, "endpoint": "responses", "error": ""}
        last_err = msg
        if i < retries:
            time.sleep(wait_seconds)
    return {
        "model": model,
        "ok": False,
        "endpoint": "responses",
        "error": f"{error_summary(last_err)}: {last_err}",
    }


def log_exception(exc: Exception) -> None:
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {exc}\n")
        traceback.print_exc(file=fh)
        fh.write("\n")


def _clear_windows_attributes_temporarily(path: Path) -> Optional[int]:
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    get_attrs = kernel32.GetFileAttributesW
    get_attrs.argtypes = [ctypes.c_wchar_p]
    get_attrs.restype = ctypes.c_uint32
    attrs = get_attrs(str(path))
    if attrs == 0xFFFFFFFF:
        return None
    mask = _WIN_HIDDEN | _WIN_READONLY
    if not (attrs & mask):
        return None
    set_attrs = kernel32.SetFileAttributesW
    set_attrs.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    cleared = attrs & ~mask
    if set_attrs(str(path), cleared):
        return attrs
    return None


def safe_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original_attrs = _clear_windows_attributes_temporarily(path)
    try:
        path.write_text(data, encoding=encoding)
    finally:
        if original_attrs is not None:
            kernel32 = ctypes.windll.kernel32
            set_attrs = kernel32.SetFileAttributesW
            set_attrs.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
            set_attrs(str(path), original_attrs)


