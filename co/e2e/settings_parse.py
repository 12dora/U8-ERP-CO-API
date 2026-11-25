"""e2e 配置的逐项校验。不读文件，不连接服务器。

Windows 路径、库名、任务名最终会拼进远端 PowerShell 和 SQL，所以这里只收很窄的字符集。
"""

from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(RuntimeError):
    pass


_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_USER = re.compile(r"^[A-Za-z0-9._-]{1,64}\Z")
_PRINCIPAL = re.compile(r"^[A-Za-z0-9._-]{1,64}\\[A-Za-z0-9._-]{1,64}\Z")
# e2e 目录和桥运行目录会作为脚本参数传过去，远端参数只允许 [A-Za-z0-9_.:\\-]，所以这里不收空格。
_ROOT = re.compile(r"^[A-Za-z]:(?:\\[A-Za-z0-9_.-]+)+\Z")
# U8 安装目录、pwsh、sqlcmd 只写进 e2e.json，允许空格和括号（Program Files (x86)）。
_WINPATH = re.compile(r"^[A-Za-z]:(?:\\[A-Za-z0-9_. ()-]+)+\Z")
_ACC = re.compile(r"^\d{3}\Z")
_YEAR = re.compile(r"^\d{4}\Z")
_SUFFIX = re.compile(r"^_[A-Za-z0-9_]{3,40}\Z")
_TASK = re.compile(r"^[A-Za-z0-9-]{3,40}\Z")
_SERVICE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\Z")
_SQL_HOST = re.compile(r"^[A-Za-z0-9._-]{1,128}(?:\\[A-Za-z0-9_$]{1,64})?\Z")
_SQL_SERVICE = re.compile(r"^[A-Za-z0-9_$]{1,80}\Z")
# 与桥的 listenPrefix 规则一致（ConfigRules.cs）：只收 http，端口写明，路径固定为 /u8co/。
_URL = re.compile(r"^http://([A-Za-z0-9.-]{1,253}):(\d{1,5})(/u8co)/?\Z")


def known(sec: dict, keys: frozenset[str], where: str) -> None:
    # 拼错的键不能悄悄落回缺省值（例如把 u8_home 写错，保护的就成了缺省的 D:\U8SOFT）。
    extra = sorted(key for key in sec if key not in keys)
    if extra:
        raise ConfigError(f"{where} 里有不认识的键 {extra[0]!r}；可用的键：{', '.join(sorted(keys))}")


def section(data: dict, name: str, keys: frozenset[str]) -> dict:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {name} 必须是对象")
    known(value, keys, "配置项 " + name)
    return value


def text(sec: dict, key: str, default: str, where: str) -> str:
    value = sec.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"配置项 {where}.{key} 必须是字符串")
    return value.strip()


def required(sec: dict, key: str, where: str) -> str:
    value = text(sec, key, "", where)
    if value == "":
        raise ConfigError(f"缺少配置项 {where}.{key}")
    return value


def number(sec: dict, key: str, default: int, where: str) -> int:
    value = sec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"配置项 {where}.{key} 必须是整数")
    return value


def flag(sec: dict, key: str, default: bool, where: str) -> bool:
    value = sec.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 {where}.{key} 必须是 true 或 false")
    return value


def match(value: str, pattern: re.Pattern[str], label: str) -> str:
    if pattern.fullmatch(value) is None:
        raise ConfigError(f"配置项 {label} 不合法：{value!r}")
    return value


def host(value: str) -> str:
    return match(value, _HOST, "server.host")


def user(value: str) -> str:
    return match(value, _USER, "server.ssh_user")


def principal(value: str) -> str:
    if value == "":
        return ""
    return match(value, _PRINCIPAL, "server.windows_principal")


def root_path(value: str, label: str) -> str:
    match(value, _ROOT, label)
    return _no_dots(value, label)


def win_path(value: str, label: str) -> str:
    match(value, _WINPATH, label)
    return _no_dots(value, label)


def _no_dots(value: str, label: str) -> str:
    parts = value.split("\\")[1:]
    if any(part in (".", "..") or part.strip() != part for part in parts):
        raise ConfigError(f"配置项 {label} 不能含 . 或 .. 段")
    if len(parts) < 2 and label != "server.u8_home":
        raise ConfigError(f"配置项 {label} 至少要在盘符下两级")
    return value


def account(value: str) -> str:
    return match(value, _ACC, "account.code")


def year(value: str) -> str:
    return match(value, _YEAR, "account.year")


def suffix(value: str) -> str:
    return match(value, _SUFFIX, "snapshot.suffix")


def task(value: str) -> str:
    return match(value, _TASK, "snapshot.task_prefix")


def service(value: str) -> str:
    return match(value, _SERVICE, "bridge.service_name")


def sql_host(value: str) -> str:
    return match(value, _SQL_HOST, "sql.server")


def sql_service(value: str) -> str:
    return match(value, _SQL_SERVICE, "sql.service")


def port(value: int, label: str, allow_zero: bool = False) -> int:
    low = 0 if allow_zero else 1
    if value < low or value > 65535:
        raise ConfigError(f"配置项 {label} 超出端口范围")
    return value


def bridge_url(url: str, bridge_port: int) -> str:
    found = _URL.fullmatch(url)
    if found is None:
        raise ConfigError("配置项 bridge.url 不合法：必须形如 http://主机:端口/u8co（桥只收 http，路径固定为 /u8co/）")
    if int(found.group(2)) != bridge_port:
        raise ConfigError("配置项 bridge.url 的端口必须等于 bridge.port")
    return url.rstrip("/")


def url_path(url: str) -> str:
    found = _URL.fullmatch(url)
    if found is None:
        raise ConfigError("配置项 bridge.url 不合法：必须形如 http://主机:端口/u8co")
    return found.group(3)


def timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"配置项 server.timezone 不是有效时区：{value!r}") from None
    return value


def local_path(value: str, label: str) -> str:
    if value == "" or "\x00" in value:
        raise ConfigError(f"配置项 {label} 为空")
    return str(Path(value).expanduser())


def denied(sec: dict, code: str) -> tuple[str, ...]:
    # 必须显式写出。没有别的账套时写 []；这样忘了配置不会被当成「没有要拒绝的账套」。
    if "denied" not in sec:
        raise ConfigError("缺少配置项 account.denied（禁止碰的账套号列表；确实没有就写 []）")
    items = sec["denied"]
    if not isinstance(items, list):
        raise ConfigError("配置项 account.denied 必须是数组")
    found = []
    for item in items:
        value = match(str(item), _ACC, "account.denied")
        if value == code:
            raise ConfigError("测试账套出现在 account.denied 里，拒绝运行")
        if value not in found:
            found.append(value)
    return tuple(found)


def foreign(value: str, code: str, deny: tuple[str, ...]) -> str:
    # 「账套不在允许列表」用例用的账套号。桥拒绝它时不会登录 U8。
    if value == "":
        value = deny[0] if deny else ("998" if code != "998" else "997")
    match(value, _ACC, "account.foreign_probe")
    if value == code:
        raise ConfigError("account.foreign_probe 不能等于测试账套")
    return value


def db_names(sec: dict, code: str, start: str) -> tuple[str, str]:
    data = text(sec, "data_db", "UFDATA_" + code + "_" + start, "account")
    meta = text(sec, "meta_db", "UFMeta_" + code, "account")
    if re.fullmatch(r"UFDATA_" + code + r"_\d{4}", data) is None:
        raise ConfigError("account.data_db 必须是 UFDATA_<账套号>_<年度>")
    if meta != "UFMeta_" + code:
        raise ConfigError("account.meta_db 必须是 UFMeta_<账套号>")
    return data, meta


def drive(path: str) -> str:
    return path[:2].upper()


def under(path: str, base: str) -> bool:
    low, top = path.lower().rstrip("\\"), base.lower().rstrip("\\")
    return low == top or low.startswith(top + "\\")


def protected(u8_home: str, work_roots: tuple[str, ...]) -> tuple[str, ...]:
    # U8 安装目录永远不碰。它所在的盘和 e2e 目录（work_roots[0]）不在同一个盘时，整盘都不碰（缺省就是 D:\）。
    # 桥运行目录只能落在不受保护的地方，不能反过来放宽保护。
    items = [u8_home]
    home_drive = drive(u8_home)
    if drive(work_roots[0]) != home_drive:
        items.append(home_drive + "\\")
    for root in work_roots:
        if any(under(root, item) for item in items):
            raise ConfigError("e2e 目录或桥运行目录落在 U8 安装目录或其所在盘上")
    return tuple(items)
