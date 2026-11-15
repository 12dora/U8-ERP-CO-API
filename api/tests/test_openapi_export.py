"""离线导出的 OpenAPI：确定性、路径齐全、每个操作有摘要/分组/Bearer，只有占位服务器地址。"""

import json
import re

from tests.support import make_client
from u8co_api.openapi_export import PLACEHOLDER_SERVER, build_document, dumps, main, operations

_URL = re.compile(r"https?://([A-Za-z0-9.-]+)")


def test_export_is_deterministic(tmp_path):
    first = tmp_path / "a.json"
    second = tmp_path / "b" / "c.json"
    main(["--out", str(first)])
    main(["--out", str(second)])
    text = first.read_text(encoding="utf-8")
    assert text == second.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.loads(text) == json.loads(dumps(build_document()))


def test_export_has_every_co_path_of_the_app():
    client = make_client()
    runtime = client.get("/v1/openapi.json").json()
    exported = build_document()
    assert set(exported["paths"]) == set(runtime["paths"])
    assert all(path.startswith("/v1/co/") for path in exported["paths"])
    assert exported["components"] == runtime["components"]
    assert exported["security"] == [{"bearerAuth": []}]
    assert exported["openapi"].startswith("3.1")


def test_every_operation_has_summary_tag_and_bearer():
    document = build_document()
    found = operations(document)
    assert found
    declared = {tag["name"] for tag in document["tags"]}
    for operation in found:
        assert operation.get("summary", "").strip()
        assert operation.get("tags")
        assert set(operation["tags"]) <= declared
        assert operation.get("security") == [{"bearerAuth": []}]
    assert all(tag["description"] for tag in document["tags"])


def test_only_the_placeholder_server_and_neutral_accounts():
    document = build_document()
    assert document["servers"] == [PLACEHOLDER_SERVER]
    assert "账套以部署配置为准" in document["info"]["description"]
    assert "为空，所有业务调用" not in document["info"]["description"]
    hosts = set(_URL.findall(dumps(document)))
    assert all(host == "example.com" or host.endswith(".example.com") for host in hosts), hosts


def test_accounts_option_lists_the_given_accounts(tmp_path):
    out = tmp_path / "openapi.json"
    main(["--out", str(out), "--accounts", "803,902"])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert "803、902" in document["info"]["description"]
