"""单元测试共用的内存配置和测试数据。只有文档地址和占位值，不读用户目录下的配置文件。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from co.e2e import settings, testdata

_HERE = Path(__file__).resolve().parents[1]


def sample_dict() -> dict:
    data = json.loads((_HERE / "e2e.example.json").read_text(encoding="utf-8"))
    data["report"] = {"dir": "/nonexistent/u8co-test-reports"}
    data["testdata"] = {"file": "/nonexistent/u8co-testdata.json"}
    return data


def sample_testdata() -> dict:
    return json.loads((_HERE / "testdata.example.json").read_text(encoding="utf-8"))


def foreign_account(index: int = 0) -> str:
    """示例配置里的禁止账套号（denied 列表第 index 项）。"""
    return sample_dict()["account"]["denied"][index]


def foreign_data_db(index: int = 0, year: str = "2024") -> str:
    return "UFDATA_" + foreign_account(index) + "_" + year


def foreign_meta_db(index: int = 0) -> str:
    return "UFMeta_" + foreign_account(index)


def lane_dict(code: str = "802") -> dict:
    # co/e2e/lane.example.json 换成另一个账套号；报告和测试数据指向不存在的目录。
    text = (_HERE / "lane.example.json").read_text(encoding="utf-8").replace("802", code)
    data = json.loads(text)
    data["report"] = {"dir": "/nonexistent/u8co-test-reports-" + code}
    data["testdata"] = {"file": "/nonexistent/u8co-testdata-" + code + ".json"}
    return data


def lane_config(code: str = "802") -> settings.E2EConfig:
    return settings.from_dict(lane_dict(code))


def make_config(**changes: object) -> settings.E2EConfig:
    data = copy.deepcopy(sample_dict())
    for dotted, value in changes.items():
        section, key = dotted.split("__", 1)
        data.setdefault(section, {})[key] = value
    return settings.from_dict(data)


def install(cfg: settings.E2EConfig | None = None, data: dict | None = None) -> settings.E2EConfig:
    chosen = cfg or make_config()
    settings.use(chosen)
    testdata.use(sample_testdata() if data is None else data)
    return chosen


def status_identity() -> dict:
    # session-status.ps1 报的配置标识；guard.fetch_status 要求它与本机配置一致。
    cfg = settings.conf()
    return {
        "account": cfg.account,
        "data_db": cfg.data_db,
        "meta_db": cfg.meta_db,
        "snap_suffix": cfg.snap_suffix,
        "revert_task": cfg.revert_task,
    }


def reset() -> None:
    settings.use(None)
    testdata.use(None)
