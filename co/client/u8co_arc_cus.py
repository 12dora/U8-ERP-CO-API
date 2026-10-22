"""客户主数据的专用写入（受控 SQL，不走客户 EAI）：专管业务员、简称 / 地区分类 / 停用日期、收货地址（改、增、删）。混入 U8CoClient。

EAI 的客户修改会清空开户银行与银行账号、改写最后收款日期，U8 也没有收货地址组件，所以三个操作都只改
请求里列出的列。fields 与 expect 的写法同 API（docs/api-reference.md「客户主数据专用写入」）；
这里只做本地预检，细的校验（地区分类是否末级、停用日期不早于建档日期、地址编码引用）在桥。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _arc_code, _common, _text, _ymd
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

# 客户字段：键 → 最大长度（Customer.cCusAbbName 60、cDCCode 12、dEndDate 写成 yyyy-MM-dd）。
CUSTOMER_FIELD_LIMIT = {"abbrname": 60, "district": 12, "end_date": 10}
# 收货地址：code → cAddCode（30），name → cDeliverAdd、unit → cDeliverUnit（255），default → bDefault。
ADDRESS_FIELD_LIMIT = {"code": 30, "name": 255, "unit": 255}
ADDRESS_KEYS = (*ADDRESS_FIELD_LIMIT, "default")
# 改这些键必须在 expect 里给出当前值（地址表没有行版本）。
ADDRESS_NEEDS_EXPECT = ("name", "unit", "default")
# 新增可给的键：code 是 address 参数，不放进 fields。
ADDRESS_ADD_KEYS = ADDRESS_NEEDS_EXPECT


@dataclass(frozen=True)
class CustomerAddressEdit:
    """改一个收货地址：address 是当前地址编码；expect 必须覆盖 fields 里除 code 以外的每一项。"""

    code: str
    address: str
    fields: dict[str, Any]
    expect: dict[str, Any] | None = None


@dataclass(frozen=True)
class CustomerAddressAdd:
    """新增一个收货地址：address 是新地址编码；fields 必须有 name，可选 unit（空串按空）、default（缺省 false）。"""

    code: str
    address: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class CustomerAddressDelete:
    """删除一个收货地址：expect 必须给全 name、unit、default 的当前值（地址表没有行版本）。"""

    code: str
    address: str
    expect: dict[str, Any]


class U8CoArcCusMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def arc_customer_person(
        self,
        call: U8Call,
        code: str,
        person: str,
        expect: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fields = _common(call)
        fields["code"] = _arc_code("customer", code, "档案编码 code")
        fields["person"] = _text(person, "person", 20)
        if expect is not None:
            fields["expect"] = "" if expect == "" else _text(expect, "expect", 20)
        return self.call("/v1/archives/customer_person", with_key(fields, idempotency_key))

    def arc_customer_fields(
        self,
        call: U8Call,
        code: str,
        fields: dict[str, str],
        expect: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _common(call)
        body["code"] = _arc_code("customer", code, "档案编码 code")
        body["fields"] = _customer_values(fields, "fields", tuple(CUSTOMER_FIELD_LIMIT), True)
        if expect is not None:
            body["expect"] = _customer_values(expect, "expect", tuple(body["fields"]), False)
        return self.call("/v1/archives/customer_fields", with_key(body, idempotency_key))

    def arc_customer_address(
        self, call: U8Call, edit: CustomerAddressEdit, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = _common(call)
        body["code"] = _arc_code("customer", edit.code, "档案编码 code")
        body["address"] = _text(edit.address, "address", ADDRESS_FIELD_LIMIT["code"])
        body["fields"] = _address_values(edit.fields, "fields", ADDRESS_KEYS, nonempty=True)
        wanted = tuple(key for key in ADDRESS_NEEDS_EXPECT if key in body["fields"])
        expect = _address_values(edit.expect or {}, "expect", wanted)
        missing = [key for key in wanted if key not in expect]
        if missing:
            raise ValueError(f"fields 改 {'、'.join(missing)} 时必须在 expect 里给出当前值")
        if expect:
            body["expect"] = expect
        return self.call("/v1/archives/customer_address", with_key(body, idempotency_key))

    def arc_customer_address_add(
        self, call: U8Call, add: CustomerAddressAdd, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = _common(call)
        body["code"] = _arc_code("customer", add.code, "档案编码 code")
        body["address"] = _new_address(add.address)
        fields = _address_values(add.fields, "fields", ADDRESS_ADD_KEYS, nonempty=True)
        if "name" not in fields or fields["name"].strip() == "":
            raise ValueError("fields.name 必填，不能为空")
        body["fields"] = fields
        return self.call("/v1/archives/customer_address_add", with_key(body, idempotency_key))

    def arc_customer_address_delete(
        self, call: U8Call, drop: CustomerAddressDelete, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = _common(call)
        body["code"] = _arc_code("customer", drop.code, "档案编码 code")
        body["address"] = _text(drop.address, "address", ADDRESS_FIELD_LIMIT["code"])
        expect = _address_values(drop.expect, "expect", ADDRESS_NEEDS_EXPECT, nonempty=True)
        missing = [key for key in ADDRESS_NEEDS_EXPECT if key not in expect]
        if missing:
            raise ValueError(f"expect 必须给出 {'、'.join(missing)} 的当前值（地址表没有行版本）")
        body["expect"] = expect
        return self.call("/v1/archives/customer_address_delete", with_key(body, idempotency_key))


def _object(values: object, label: str, allowed: tuple[str, ...], nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError(f"{label} 必须是对象")
    if nonempty and not values:
        raise ValueError(f"{label} 至少要有一项")
    unknown = sorted(str(key) for key in values if key not in allowed)
    if unknown:
        raise ValueError(f"{label} 有不允许的键: {', '.join(unknown)}；可用 {'、'.join(allowed)}")
    return dict(values)


def _plain(value: object, label: str, limit: int) -> str:
    # 可为空串的文本：不含控制字符，不超过 limit。
    if type(value) is not str or len(value) > limit:
        raise ValueError(f"{label} 必须是不超过 {limit} 个字符的字符串")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} 不能含控制字符")
    return value


def _customer_values(values: object, label: str, allowed: tuple[str, ...], writing: bool) -> dict[str, str]:
    found = _object(values, label, allowed, nonempty=writing)
    for key, value in found.items():
        where = f"{label}.{key}"
        if key == "end_date":
            if value != "":
                _ymd(value, where)
        elif writing:
            _customer_text(key, _plain(value, where, CUSTOMER_FIELD_LIMIT[key]), where)
        elif type(value) is not str or len(value) > CUSTOMER_FIELD_LIMIT[key]:
            # expect 只核对当前值，不要求写法合规。
            raise ValueError(f"{where} 必须是不超过 {CUSTOMER_FIELD_LIMIT[key]} 个字符的字符串")
    return found


def _customer_text(key: str, text: str, where: str) -> None:
    if key == "abbrname" and text.strip() == "":
        raise ValueError(f"{where} 不能为空")
    if key == "district" and text != text.strip():
        raise ValueError(f"{where} 首尾不能有空白")


def _new_address(value: object) -> str:
    text = _text(value, "address", ADDRESS_FIELD_LIMIT["code"])
    if text != text.strip():
        raise ValueError("address 首尾不能有空白")
    return text


def _address_values(values: object, label: str, allowed: tuple[str, ...], nonempty: bool = False) -> dict[str, Any]:
    found = _object(values, label, allowed, nonempty)
    for key, value in found.items():
        where = f"{label}.{key}"
        if key == "default":
            if type(value) is not bool:
                raise ValueError(f"{where} 必须是布尔值")
        elif key == "code":
            if _text(value, where, ADDRESS_FIELD_LIMIT[key]) != value.strip():
                raise ValueError(f"{where} 首尾不能有空白")
        else:
            _plain(value, where, ADDRESS_FIELD_LIMIT[key])
    return found
