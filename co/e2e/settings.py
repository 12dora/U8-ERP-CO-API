"""e2e 和 probe 的站点配置。只从一个 JSON 文件读，路径可用环境变量 U8CO_E2E_CONFIG 覆盖。

run_e2e、session、comprobe 都经 conf() 读这一份；多条调试通道就是每次调用用 U8CO_E2E_CONFIG 指定各自的文件，
通道之间的冲突检查在 co/e2e/lane.py。

没有配置文件就不运行：服务器地址、测试账套和禁止碰的账套都没有缺省值。
键的含义见 co/e2e/e2e.example.json 和 co/e2e/README.md。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from co.e2e import daytime
from co.e2e import lane as lanes
from co.e2e import settings_parse as sp
from co.e2e.seed.config import SeedOverrides
from co.e2e.seed.config import parse as parse_seed
from co.e2e.settings_parse import ConfigError

ENV_PATH = "U8CO_E2E_CONFIG"
DEFAULT_PATH = "~/.config/u8co/e2e.json"
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
BRIDGE_EXE = "u8co-bridge.exe"
_PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
_SQLCMD = r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\SQLCMD.EXE"

__all__ = ["ConfigError", "E2EConfig", "conf", "config_path", "from_dict", "lane_env", "load", "load_lane", "remote_doc", "use"]


@dataclass(frozen=True)
class E2EConfig:
    host: str
    ssh_user: str
    windows_principal: str
    remote_root: str
    u8_home: str
    pwsh: str
    timezone: str
    ssh_key: str
    known_hosts: str
    control_dir: str
    sql_server: str
    sql_port: int
    sql_service: str
    sqlcmd: str
    account: str
    year: str
    data_db: str
    meta_db: str
    operator_file: str
    denied: tuple[str, ...]
    foreign_probe: str
    snap_suffix: str
    task_prefix: str
    bridge_url: str
    bridge_port: int
    bridge_root: str
    service_name: str
    secret_file: str
    report_dir: str
    run_minutes: int
    session_minutes: int
    testdata_file: str
    lane: str = ""
    # 显式开启：允许 close --keep 保留造数结果（与是否写 lane 无关）。
    seedable: bool = False
    # 必需挑单挑不到时记 N/A 而不是 SKIP（样例少的测试账套用）；发布门禁通道不要开。
    sparse_samples: bool = False
    business_hours: daytime.BusinessHours | None = None
    # 权限核对（casespec/perm_check）用的评估操作员凭据文件；空串表示本通道不跑 perm/evaluate 类用例（记 N/A）。
    perm_evaluator_file: str = ""
    # 字段权限用例（col_auth / taxno_auth）用的非主管操作员凭据文件；空串这些用例记 N/A。
    perm_operator_file: str = ""
    # 顶层 seed 段：造数编码、科目和日期的覆盖值（co/e2e/seed/config.py）；没写时为空，造数全用缺省。
    seed: SeedOverrides = SeedOverrides()

    @property
    def databases(self) -> tuple[str, str]:
        return (self.data_db, self.meta_db)

    @property
    def snapshots(self) -> tuple[str, str]:
        return (self.data_db + self.snap_suffix, self.meta_db + self.snap_suffix)

    @property
    def out_dir(self) -> str:
        return self.remote_root + "\\out"

    @property
    def revert_task(self) -> str:
        return self.task_prefix + "-revert"

    @property
    def probe_task(self) -> str:
        return self.task_prefix + "-s4u-probe"

    @property
    def bridge_path(self) -> str:
        return sp.url_path(self.bridge_url)

    @property
    def sql_target(self) -> str:
        if self.sql_port:
            return self.sql_server + "," + str(self.sql_port)
        return self.sql_server

    @property
    def protected(self) -> tuple[str, ...]:
        return sp.protected(self.u8_home, (self.remote_root, self.bridge_root))

    @property
    def ssh_target(self) -> str:
        return self.ssh_user + "@" + self.host

    def remote(self, *parts: str) -> str:
        return "\\".join((self.remote_root,) + parts)

    def owner_label(self) -> str:
        return self.windows_principal or self.ssh_user


_SERVER_KEYS = frozenset(("host", "ssh_user", "windows_principal", "remote_root", "u8_home", "pwsh", "timezone"))
_SSH_KEYS = frozenset(("key", "known_hosts", "control_dir"))
_SQL_KEYS = frozenset(("server", "port", "service", "sqlcmd"))
_ACCOUNT_KEYS = frozenset(
    ("code", "year", "data_db", "meta_db", "operator_file", "denied", "foreign_probe", "seedable", "sparse_samples",
     "perm_evaluator_file", "perm_operator_file")
)
_SNAPSHOT_KEYS = frozenset(("suffix", "task_prefix"))
_BRIDGE_KEYS = frozenset(("url", "port", "root", "service_name", "secret_file"))
_SECTIONS = frozenset(
    ("server", "ssh", "sql", "account", "snapshot", "bridge", "report", "watchdog", "testdata", "seed", daytime.SECTION)
)


def _server(data: dict) -> dict:
    sec = sp.section(data, "server", _SERVER_KEYS)
    user = sp.user(sp.text(sec, "ssh_user", "u8co-ops", "server"))
    return {
        "host": sp.host(sp.required(sec, "host", "server")),
        "ssh_user": user,
        "windows_principal": sp.principal(sp.text(sec, "windows_principal", "", "server")),
        "remote_root": sp.root_path(sp.text(sec, "remote_root", r"C:\ProgramData\U8Co\u8co-e2e", "server"), "server.remote_root"),
        "u8_home": sp.win_path(sp.text(sec, "u8_home", r"D:\U8SOFT", "server"), "server.u8_home"),
        "pwsh": sp.win_path(sp.text(sec, "pwsh", _PWSH, "server"), "server.pwsh"),
        "timezone": sp.timezone(sp.text(sec, "timezone", "Asia/Shanghai", "server")),
    }


def _ssh(data: dict) -> dict:
    sec = sp.section(data, "ssh", _SSH_KEYS)
    control = sp.text(sec, "control_dir", "", "ssh")
    return {
        "ssh_key": sp.local_path(sp.text(sec, "key", "~/.config/u8co/ssh/id_ed25519", "ssh"), "ssh.key"),
        "known_hosts": sp.local_path(sp.text(sec, "known_hosts", "~/.config/u8co/ssh/known_hosts", "ssh"), "ssh.known_hosts"),
        "control_dir": sp.local_path(control, "ssh.control_dir") if control else "",
    }


def _sql(data: dict) -> dict:
    sec = sp.section(data, "sql", _SQL_KEYS)
    return {
        "sql_server": sp.sql_host(sp.text(sec, "server", "localhost", "sql")),
        "sql_port": sp.port(sp.number(sec, "port", 1433, "sql"), "sql.port", allow_zero=True),
        "sql_service": sp.sql_service(sp.text(sec, "service", "MSSQLSERVER", "sql")),
        "sqlcmd": sp.win_path(sp.text(sec, "sqlcmd", _SQLCMD, "sql"), "sql.sqlcmd"),
    }


def _account(data: dict) -> dict:
    sec = sp.section(data, "account", _ACCOUNT_KEYS)
    code = sp.account(sp.required(sec, "code", "account"))
    start = sp.year(sp.required(sec, "year", "account"))
    data_db, meta_db = sp.db_names(sec, code, start)
    deny = sp.denied(sec, code)
    operator = sp.text(sec, "operator_file", "~/.config/u8co/test-operator.json", "account")
    return {
        "account": code,
        "year": start,
        "data_db": data_db,
        "meta_db": meta_db,
        "operator_file": sp.local_path(operator, "account.operator_file"),
        "denied": deny,
        "foreign_probe": sp.foreign(sp.text(sec, "foreign_probe", "", "account"), code, deny),
        "seedable": sp.flag(sec, "seedable", False, "account"),
        "sparse_samples": sp.flag(sec, "sparse_samples", False, "account"),
        "perm_evaluator_file": _optional_path(sec, "perm_evaluator_file"),
        "perm_operator_file": _optional_path(sec, "perm_operator_file"),
    }


def _optional_path(sec: dict, key: str) -> str:
    value = sp.text(sec, key, "", "account")
    return sp.local_path(value, "account." + key) if value else ""


def _snapshot(data: dict) -> dict:
    sec = sp.section(data, "snapshot", _SNAPSHOT_KEYS)
    return {
        "snap_suffix": sp.suffix(sp.text(sec, "suffix", "_u8co_e2e_snap", "snapshot")),
        "task_prefix": sp.task(sp.text(sec, "task_prefix", "u8co-e2e", "snapshot")),
    }


def _bridge(data: dict, host: str) -> dict:
    sec = sp.section(data, "bridge", _BRIDGE_KEYS)
    port = sp.port(sp.number(sec, "port", 18089, "bridge"), "bridge.port")
    url = sp.bridge_url(sp.text(sec, "url", "", "bridge") or f"http://{host}:{port}/u8co", port)
    secret = sp.text(sec, "secret_file", "~/.config/u8co/bridge-secret.hex", "bridge")
    return {
        "bridge_url": url,
        "bridge_port": port,
        "bridge_root": sp.root_path(sp.text(sec, "root", r"C:\ProgramData\U8Co\u8co", "bridge"), "bridge.root"),
        "service_name": sp.service(sp.text(sec, "service_name", "u8co", "bridge")),
        "secret_file": sp.local_path(secret, "bridge.secret_file"),
    }


def _state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return str(Path(base).expanduser() / "u8co" / "e2e-runs")


def _rest(data: dict) -> dict:
    report = sp.section(data, "report", frozenset(("dir",)))
    watch = sp.section(data, "watchdog", frozenset(("run_minutes", "session_minutes")))
    extra = sp.section(data, "testdata", frozenset(("file",)))
    return {
        "report_dir": sp.local_path(sp.text(report, "dir", "", "report") or _state_dir(), "report.dir"),
        "run_minutes": sp.number(watch, "run_minutes", 25, "watchdog"),
        "session_minutes": sp.number(watch, "session_minutes", 120, "watchdog"),
        "testdata_file": sp.local_path(sp.text(extra, "file", "~/.config/u8co/testdata.json", "testdata"), "testdata.file"),
    }


def from_dict(data: dict) -> E2EConfig:
    if not isinstance(data, dict):
        raise ConfigError("e2e 配置必须是 JSON 对象")
    sp.known(data, _SECTIONS | {"lane"}, "e2e 配置顶层")
    fields = _server(data)
    fields.update(_ssh(data))
    fields.update(_sql(data))
    fields.update(_account(data))
    fields.update(_snapshot(data))
    fields.update(_bridge(data, fields["host"]))
    fields.update(_rest(data))
    fields["lane"] = lanes.lane_tag(data.get("lane"))
    fields["business_hours"] = daytime.parse(data, fields["timezone"])
    fields["seed"] = parse_seed(data.get("seed"))
    cfg = E2EConfig(**fields)
    if sp.under(cfg.remote_root, cfg.bridge_root) or sp.under(cfg.bridge_root, cfg.remote_root):
        raise ConfigError("server.remote_root 和 bridge.root 不能互相包含")
    _ = cfg.protected
    lanes.check(cfg, _state_dir())
    return cfg


def config_path() -> Path:
    return Path(os.environ.get(ENV_PATH) or DEFAULT_PATH).expanduser()


def load(path: Path | None = None) -> E2EConfig:
    target = path or config_path()
    if not target.is_file():
        raise ConfigError(f"找不到 e2e 配置 {target}。按 co/e2e/e2e.example.json 写一份，或用 {ENV_PATH} 指定")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读不了 e2e 配置 {target}: {type(exc).__name__}") from None
    return from_dict(data)


def load_lane(path: Path) -> E2EConfig:
    # 多通道编排（co/e2e/multi.py）一次要读几份通道配置；别的模块仍只经 conf()。
    return load(path)


def lane_env(path: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    # 子进程按这份环境选通道配置：配置路径的环境变量名只在本模块出现。
    env = dict(os.environ if base is None else base)
    env[ENV_PATH] = str(path)
    return env


_CURRENT: list[E2EConfig] = []


def conf() -> E2EConfig:
    if not _CURRENT:
        _CURRENT.append(load())
    return _CURRENT[0]


def use(cfg: E2EConfig | None) -> None:
    # 单元测试装一份内存配置；传 None 清掉，下次 conf() 重新读文件。
    _CURRENT.clear()
    if cfg is not None:
        _CURRENT.append(cfg)


def remote_doc(cfg: E2EConfig) -> dict:
    # 上传到服务器 <remote_root>\e2e.json，remote/common.ps1 读它。只有路径和名字，没有口令和密钥。
    return {
        "Version": 1,
        "RemoteRoot": cfg.remote_root,
        "BridgeRoot": cfg.bridge_root,
        "BridgeExe": BRIDGE_EXE,
        "ServiceName": cfg.service_name,
        "BridgePort": cfg.bridge_port,
        "BridgePath": cfg.bridge_path,
        "Account": cfg.account,
        "DataDb": cfg.data_db,
        "MetaDb": cfg.meta_db,
        "DeniedDbs": [prefix + acc for acc in cfg.denied for prefix in ("UFDATA_", "UFMeta_")],
        "SnapSuffix": cfg.snap_suffix,
        "RevertTask": cfg.revert_task,
        "ProbeTask": cfg.probe_task,
        "Principal": cfg.windows_principal,
        "U8Home": cfg.u8_home,
        "Protected": list(cfg.protected),
        "Pwsh": cfg.pwsh,
        "SqlCmd": cfg.sqlcmd,
        "SqlServer": cfg.sql_target,
        "SqlService": cfg.sql_service,
        "MinFreeBytes": MIN_FREE_BYTES,
        # snapshot-drop.ps1（close --keep）只在 Seedable 为 true 时删快照。
        "Seedable": cfg.seedable,
    }
