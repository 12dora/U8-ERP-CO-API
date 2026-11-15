"""命令行的服务地址和服务密钥从哪里来。没有缺省服务器地址。

优先级：命令行 --base-url > 环境变量 U8CO_BASE_URL > 客户端配置文件的 base_url。
密钥：环境变量 U8CO_SECRET > 环境变量 U8CO_SECRET_FILE 指的文件 > 客户端配置文件的 secret_file。
客户端配置文件路径：环境变量 U8CO_CLIENT_CONFIG，缺省 ~/.config/u8co/client.json；文件可以不存在。
密钥和口令都不能从命令行参数给。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_ENV = "U8CO_CLIENT_CONFIG"
CONFIG_DEFAULT = "~/.config/u8co/client.json"
_KEYS = ("base_url", "secret_file")


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or CONFIG_DEFAULT).expanduser()


def load_config(path: Path | None = None) -> dict[str, str]:
    target = path or config_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(f"客户端配置 {target} 不是可读的 JSON") from None
    if not isinstance(data, dict):
        raise SystemExit(f"客户端配置 {target} 必须是 JSON 对象")
    found: dict[str, str] = {}
    for key in _KEYS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"客户端配置 {target} 的 {key} 必须是非空字符串")
        found[key] = value.strip()
    return found


def resolve_base_url(given: str | None, config: dict[str, str] | None = None) -> str:
    if given:
        return given
    value = os.environ.get("U8CO_BASE_URL", "").strip()
    if value:
        return value
    value = (config if config is not None else load_config()).get("base_url", "")
    if value:
        return value
    raise SystemExit("没有服务地址：用 --base-url、环境变量 U8CO_BASE_URL 或客户端配置的 base_url 指定")


def read_secret_file(path: Path) -> str:
    try:
        mode = path.stat().st_mode
    except OSError:
        raise SystemExit(f"找不到服务密钥文件 {path}") from None
    if mode & 0o077:
        raise SystemExit(f"服务密钥文件 {path} 权限过宽，请 chmod 600")
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise SystemExit(f"读不了服务密钥文件 {path}") from None


def resolve_secret(config: dict[str, str] | None = None) -> str:
    value = os.environ.get("U8CO_SECRET", "")
    if value:
        return value
    name = os.environ.get("U8CO_SECRET_FILE", "").strip()
    if not name:
        name = (config if config is not None else load_config()).get("secret_file", "")
    if not name:
        raise SystemExit("没有服务密钥：设置 U8CO_SECRET、U8CO_SECRET_FILE 或客户端配置的 secret_file")
    return read_secret_file(Path(name).expanduser())
