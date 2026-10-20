"""假桥对客户主数据专用写入（customer_person / customer_fields / customer_address）的应答，并入 test_co_gate._BUILDERS。"""


def _arc_customer_person(payload: dict) -> dict:
    return {"ok": True, "code": payload["code"], "before": None, "after": payload.get("person")}


def _arc_customer_fields(payload: dict) -> dict:
    return {"ok": True, "code": payload["code"], "changed": True, "before": {}, "after": dict(payload.get("fields", {}))}


def _arc_customer_address(payload: dict) -> dict:
    new = payload["fields"].get("code", payload["address"])
    return {
        "ok": True,
        "code": payload["code"],
        "address": payload["address"],
        "new_address": new,
        "changed": True,
        "before": [],
        "after": [],
    }


CUSTOMER_BUILDERS = {
    "/v1/archives/customer_person": _arc_customer_person,
    "/v1/archives/customer_fields": _arc_customer_fields,
    "/v1/archives/customer_address": _arc_customer_address,
}
