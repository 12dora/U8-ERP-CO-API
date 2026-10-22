"""假桥对客户主数据专用写入（customer_person / customer_fields / customer_address 及其增删）和历史发货单表头修正的应答，
并入 test_co_gate._BUILDERS。"""


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


def _arc_customer_address_rows(payload: dict) -> dict:
    return {
        "ok": True,
        "code": payload["code"],
        "address": payload["address"],
        "changed": True,
        "before": [],
        "after": [],
    }


def _voucher_head_fix(payload: dict) -> dict:
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "code": "FH0001",
        "changed": True,
        "before": dict(payload["expect"]),
        "after": dict(payload["fields"]),
        "downstream": [],
    }


CUSTOMER_BUILDERS = {
    "/v1/archives/customer_person": _arc_customer_person,
    "/v1/archives/customer_fields": _arc_customer_fields,
    "/v1/archives/customer_address": _arc_customer_address,
    "/v1/archives/customer_address_add": _arc_customer_address_rows,
    "/v1/archives/customer_address_delete": _arc_customer_address_rows,
    "/v1/vouchers/head_fix": _voucher_head_fix,
}
