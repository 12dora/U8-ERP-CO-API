"""资源标签（casespec/tags.py）：已知互斥的用例对、独占、挑单绑定、读写冲突规则。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec import picks, sched
from co.e2e.casespec.catalog import build_cases
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.tags import (
    NEED_KIND,
    SOURCE_NEEDS,
    auto_tags,
    bind_tags,
    conflicts,
    is_exclusive,
    is_mark,
    need_kind,
)
from co.e2e.tests import support

# 设计里列出的互斥对（同挑单、同编号、同审批流闸门、同审计文件、同档案、读写撞车），冻结在这里。
MUTEX_PAIRS = (
    ("sale-verify", "repeat-unverify"),
    ("sale-close", "update-verified"),
    ("sale-create", "sale-update"),
    ("purchase-invoice-generate", "purchase-invoice-review"),
    ("wf-qm04-cycle", "wf-qm04-withdraw"),
    ("wf-qm04-cycle", "opinion-too-long"),
    ("gl-cycle", "gl-list"),
    ("gl-cycle", "idempotency-gl-create"),
    ("gl-cycle", "gl-bank-cashflow"),
    ("other-in-cycle", "list-changed-since"),
    ("other-in-cycle", "other-in-update"),
    ("account-foreign", "account-foreign-load"),
    ("dispatch-generate", "sale-return-cycle"),
    ("arrival-generate", "purchase-return-cycle"),
    ("ar-receipt-cycle", "ar-receipt-edit"),
    ("ar-receipt-cycle", "ar-bill-cycle"),
    ("archive-customer-cycle", "archive-list"),
    ("archive-customer-cycle", "archive-customer-inventory-write"),
    ("archive-unit-group-write", "archive-unit-write"),
    ("archive-unit-group-write", "archive-rw8-delete-guard"),
    ("archive-rd-style-write", "archive-purchase-type-write"),
    ("archive-read-unit-group", "archive-unit-group-write"),
    ("stock-current-filter", "other-in-cycle"),
    ("report-stock-summary", "transfer-cycle"),
    ("list-sale-return", "sale-return-cycle"),
    ("list-sale-order", "sale-create"),
    ("list-sale-order", "sale-verify"),
    ("report-gl-balance", "gl-cycle"),
    ("report-arap-balance", "ar-bill-cycle"),
    ("report-arap-aging", "invoice-generate"),
    ("report-bom", "bom-lifecycle"),
    ("report-doc-trace", "dispatch-generate"),
    ("mo-create-delete", "bom-lifecycle"),
    ("mo-create-delete", "mo-create-bad"),
    ("material-out-generate", "product-in-generate"),
    # 单据链：下游生单回写上游订单行，挑单拿不到上游订单 id，按链互斥。
    ("invoice-generate", "sale-close"),
    ("invoice-generate", "update-verified"),
    ("sale-invoice-update", "sale-order-lock"),
    ("purchase-invoice-generate", "po-close-lines"),
    ("qm01-create-delete", "arrival-verify"),
    ("qm02-create-delete", "mo-unverify-verify"),
    # 应收应付处理与挑同一批种子单据的核销、外币核销、红字发票、制单用例互斥。
    ("arap-transfer-cycle", "ar-writeoff"),
    ("arap-transfer-cycle-ap", "ap-writeoff"),
    ("arap-transfer-cycle", "ar-voucher-merge"),
    ("arap-merge-cycle", "ar-writeoff-auto"),
    ("arap-red-offset-cycle", "sale-red-invoice"),
    ("arap-exchange-gain-cycle", "ar-writeoff-fx"),
    ("arap-exchange-gain-cycle-ap", "ap-writeoff-fx"),
    ("arap-exchange-gain-cycle-ap", "openings-arap-cycle"),
    # 票据处理查同样的结账标志，背书扣采购发票余额（同 pu 链）；登记新建收款单。
    ("note-endorse-cycle", "arap-transfer-cycle"),
    ("note-settle-cycle", "ar-receipt-cycle"),
    # 分包票据登记同样新建、审核收款单，票据登记串行。
    ("note-split-register-cycle", "ar-receipt-cycle"),
    ("note-split-register-cycle", "note-register-cycle"),
    # 票据退回另生成应收单（应付单）；应付票据登记新建、审核付款单，票据登记按 flag 串行。
    ("note-return-cycle", "note-register-cycle"),
    ("note-return-cycle", "ar-bill-cycle"),
    ("note-return-split-cycle", "note-split-register-cycle"),
    ("ap-note-cycle", "ap-payment-cycle"),
    ("ap-note-cycle", "ap-bill-cycle"),
    ("ap-note-cycle", "note-settle-cycle"),
    # 坏账三条用例改同一行坏账准备参数；坏账发生挑销售发票（同 sale 链），坏账收回新建收款单、用挑单的空闲客户。
    ("bad-debt-occur-cycle", "bad-debt-provision-cycle"),
    ("bad-debt-recover-cycle", "bad-debt-provision-cycle"),
    ("bad-debt-occur-cycle", "arap-transfer-cycle"),
    ("bad-debt-recover-cycle", "ar-receipt-cycle"),
    # 退款用例挑同一个空闲供应商 / 客户（ap_master / ar_master）；列表避开同类型的新建。
    ("ap-refund-cycle", "ap-payment-cycle"),
    ("ar-refund-cycle", "ar-receipt-cycle"),
    ("list-ap-refund", "ap-refund-cycle"),
    # 退款单制单挑同一个空闲供应商 / 客户，并占总账同类别凭证编号。
    ("refund-voucher-cycle", "ap-refund-cycle"),
    ("ar-refund-voucher-cycle", "ar-refund-cycle"),
    # 固定资产卡片写断言最大 sCardID，彼此互斥，卡片档案读取避开；设备台账读取避开设备新增。
    ("fa-card-cycle", "fa-card-period-refuse"),
    ("archive-read-fa-card", "fa-card-cycle"),
    ("archive-read-equipment", "equipment-create"),
)
# 应当能同时跑的对（防止规则退化成全部冲突）。
FREE_PAIRS = (
    ("health", "gl-cycle"),
    ("meta-fields", "other-in-cycle"),
    ("gl-cycle", "archive-customer-cycle"),
    ("stock-current-filter", "report-stock-summary"),
    ("list-sale-order", "list-ar-receipt"),
    ("archive-read-account", "archive-customer-cycle"),
    ("report-price-list", "sale-create"),
    ("ar-receipt-cycle", "ap-payment-cycle"),
    ("po-verify", "gl-cycle"),
    ("wf-qm06-state", "wf-qm04-cycle"),
    # 不同单据链照常并行。
    ("invoice-generate", "po-close-lines"),
    ("purchase-invoice-generate", "ar-receipt-cycle"),
    ("ap-refund-cycle", "ar-refund-cycle"),
    ("list-ar-refund", "ap-refund-cycle"),
    ("fa-dispose-refused", "fa-card-cycle"),
)


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _catalog() -> dict[str, CaseSpec]:
    return {spec.name: spec for spec in build_cases({})}


def _clash(a: CaseSpec, b: CaseSpec, bag: dict | None = None) -> bool:
    return conflicts(bind_tags(a, bag or {}), bind_tags(b, bag or {}), a.exclusive, b.exclusive)


def _writes(step: Step) -> bool:
    # 期望 200 的新建、生单。
    if step.status != 200:
        return False
    return step.kind == "create" or step.route.endswith("/generate")


class CatalogTagTest(unittest.TestCase):
    def test_every_case_has_tags(self) -> None:
        empty = [spec.name for spec in build_cases({}) if not auto_tags(spec)]
        self.assertEqual(empty, [])

    def test_known_mutex_pairs(self) -> None:
        cases = _catalog()
        for left, right in MUTEX_PAIRS:
            with self.subTest(pair=(left, right)):
                self.assertTrue(_clash(cases[left], cases[right]))
                self.assertTrue(_clash(cases[right], cases[left]))

    def test_known_free_pairs(self) -> None:
        cases = _catalog()
        for left, right in FREE_PAIRS:
            with self.subTest(pair=(left, right)):
                self.assertFalse(_clash(cases[left], cases[right]))

    def test_bridge_concurrency_exclusive(self) -> None:
        cases = _catalog()
        burst = cases["bridge-concurrency"]
        self.assertTrue(burst.exclusive)
        self.assertTrue(is_exclusive(burst))
        self.assertIn("burst", auto_tags(burst))
        self.assertTrue(_clash(burst, cases["health"]))
        self.assertTrue(_clash(cases["report-price-list"], burst))
        # 存货核算预演整月锁单据行、占全局写闸门，也独占；期初完整流程临时改结账标志，也独占；
        # 临时改账套汇率方式、信用余额表选项的两条也独占。
        exclusive = [name for name, spec in cases.items() if spec.exclusive]
        self.assertEqual(
            exclusive,
            [
                "bridge-concurrency",
                "ia-post-dry",
                "ia-period-end-dry",
                "openings-arap-live-cycle",
                "openings-arap-live-cycle-ap",
                "stock-opening-live-cycle",
                "openings-ia-post-live",
                "archive-exchange-rate-floating",
                "report-customer-credit-flip",
                # 销售发票字段权限：临时改测试操作员本人的 07/cDefine10、07/iNatSum。
                "col-auth-mask-sale-invoice-taxno",
                "col-auth-mask-sale-invoice-amount",
                # 字段权限：临时改测试操作员本人的字段权限，期间同一操作员的读取都会被遮蔽。
                "col-auth-mask-sale-out",
                "col-auth-mask-purchase-in",
                "col-auth-mgmt-cogs",
                # 写入策略：临时换整个桥的策略文件，期间别的写入也会被拒。
                "wp-health",
                "wp-deny-unlisted-type",
                "wp-freeze-account",
                "wp-window",
                "wp-operator-deny",
                "wp-max-lines",
                "wp-invalid-keeps-last",
                "wp-tier2-still-test-only",
                "wp-quota-minute",
                "ia-post-period-end-live",
                "periods-unclose-reclose",
                # 存货核算制单一整轮：记账、制单、取消制单、恢复记账，改整月存货核算状态。
                "ia-voucher-post-cycle",
            ],
        )

    def test_creators_carry_new_tag(self) -> None:
        # 期望成功的新建、生单一定带 new:<类型>（新构造器没登记类型时这里先挂）。probe 用例在自定义步骤里断言被拒。
        for spec in build_cases({}):
            if "probe" not in spec.tags and any(_writes(step) for step in spec.steps):
                with self.subTest(case=spec.name):
                    self.assertTrue(any(tag.startswith("new:") for tag in auto_tags(spec)))

    def test_mark_only_writers_are_refusals(self) -> None:
        # 只有 w: 标记、没有互斥标签的用例，必须全是期望被拒绝的调用（不真的写账套）。
        for spec in build_cases({}):
            tags = auto_tags(spec)
            if not any(tag.startswith("w:") for tag in tags) or any(not is_mark(tag) for tag in tags):
                continue
            with self.subTest(case=spec.name):
                acting = [step for step in spec.steps if step.route or step.kind == "create"]
                self.assertTrue(acting)
                self.assertTrue(all(step.status != 200 for step in acting))

    def test_reports_wait_for_writers(self) -> None:
        for spec in build_cases({}):
            if spec.name.startswith("report-"):
                with self.subTest(case=spec.name):
                    tags = auto_tags(spec)
                    self.assertIn("wave:2", tags)
                    self.assertTrue(any(tag.startswith("rpt:") for tag in tags))
                    self.assertTrue(spec.routes)

    def test_custom_step_cases_declare_routes(self) -> None:
        # core / custom 自定义步骤看不出路由（资源标签除外的纯比较步骤不算），必须手写 routes。
        silent = ("room", "bag_compare", "idem_count", "load_same", "load_code")
        for spec in build_cases({}):
            custom = [step for step in spec.steps if step.kind in ("core", "custom") and step.sql_name not in silent]
            if custom and not any(step.route for step in spec.steps):
                with self.subTest(case=spec.name):
                    self.assertTrue(spec.routes)

    def test_workflow_writers_share_wf(self) -> None:
        cases = _catalog()
        for name in ("wf-qm04-cycle", "wf-qm04-withdraw", "opinion-too-long"):
            self.assertIn("wf", auto_tags(cases[name]))
        for name in ("wf-qm06-state", "wf-qm03-state", "wf-other-in-type"):
            self.assertNotIn("wf", auto_tags(cases[name]))


class BindTest(unittest.TestCase):
    def test_same_doc_from_different_keys_conflicts(self) -> None:
        cases = _catalog()
        load, verify = cases["load-sale_order"], cases["sale-verify"]
        self.assertTrue(_clash(load, verify, {"load_sale_order": "501", "sale_id": "501"}))
        self.assertFalse(_clash(load, verify, {"load_sale_order": "502", "sale_id": "501"}))

    def test_qm04_ids_disjoint_but_wf_shared(self) -> None:
        cases = _catalog()
        bag = {"qm04_a": "11", "qm04_b": "12"}
        left = bind_tags(cases["wf-qm04-cycle"], bag)
        right = bind_tags(cases["wf-qm04-withdraw"], bag)
        self.assertIn("doc:qm_product_check:11", left)
        self.assertIn("doc:qm_product_check:12", right)
        self.assertEqual(left & right, frozenset({"wf"}))

    def test_source_bound_to_id_and_need_kept(self) -> None:
        spec = _catalog()["dispatch-generate"]
        tags = bind_tags(spec, {"so_ship": "77", "so_ship_line": "9", "so_ship_wh": "01"})
        self.assertIn("src:sale_order:77", tags)
        self.assertIn("doc:sale_order:77", tags)
        self.assertIn("need:so_ship", tags)
        self.assertNotIn("src:sale_order:so_ship", tags)
        self.assertFalse(any(tag.startswith("doc:") and tag.endswith(":01") for tag in tags))

    def test_missing_or_text_pick_not_bound(self) -> None:
        spec = _catalog()["other-in-cycle"]
        tags = bind_tags(spec, {"other_in_master": "01|0101|01", "vn_prefix": "RK"})
        self.assertFalse(any(tag.startswith("doc:") for tag in tags))
        self.assertEqual(bind_tags(_catalog()["sale-close"], {}), auto_tags(_catalog()["sale-close"]))

    def test_picked_bag_id_is_not_made(self) -> None:
        # 锁定用例按 bag:<挑单键> 取 id：那是挑中的单据，不是本用例新建的，不带 new:。
        tags = auto_tags(_catalog()["sale-order-lock"])
        self.assertNotIn("new:sale_order", tags)
        self.assertIn("chain:sale", tags)

    def test_probe_gets_no_derived_tags(self) -> None:
        tags = auto_tags(_catalog()["create-protected"])
        self.assertNotIn("stock", tags)
        self.assertFalse(any(tag.startswith("chain:") for tag in tags))

    def test_need_kinds(self) -> None:
        self.assertEqual(need_kind("load_sale_invoice"), "sale_invoice")
        self.assertEqual(need_kind("sale_verified"), "sale_order")
        self.assertEqual(need_kind("so_ship_line"), "")
        self.assertTrue(SOURCE_NEEDS <= set(NEED_KIND))

    def test_every_id_pick_has_kind(self) -> None:
        keys = [key for key, _sql in picks._id_queries()] + ["qm04_a", "qm04_b", "dispatch_id"]
        keys += [names[0] for _lead, names, flags, _sql in picks._PACKS if flags[0] and names[0] != "bin_src"]
        for key in keys:
            with self.subTest(key=key):
                self.assertNotEqual(need_kind(key), "")


class RuleTest(unittest.TestCase):
    def test_marks_do_not_self_conflict(self) -> None:
        for tag in ("w:sale_order", "stock-read", "list:other_in", "arc-read:unit", "rpt:gl", "wave:2", "meta", "login"):
            with self.subTest(tag=tag):
                self.assertFalse(conflicts(frozenset({tag}), frozenset({tag})))

    def test_resources_self_conflict(self) -> None:
        tags = ("stock", "wf", "gl", "audit", "new:other_in", "need:sale_id", "arc:unit", "bom", "doc:dispatch:5", "chain:pu")
        for tag in tags:
            with self.subTest(tag=tag):
                self.assertTrue(conflicts(frozenset({tag}), frozenset({tag})))

    def test_reader_hits_writer(self) -> None:
        self.assertTrue(conflicts(frozenset({"stock-read"}), frozenset({"stock"})))
        self.assertTrue(conflicts(frozenset({"new:sale_order"}), frozenset({"list:sale_order"})))
        self.assertTrue(conflicts(frozenset({"list:sale_order"}), frozenset({"w:sale_order"})))
        self.assertFalse(conflicts(frozenset({"list:sale_order"}), frozenset({"new:dispatch"})))
        self.assertTrue(conflicts(frozenset({"arc-read:unit"}), frozenset({"arc:unit"})))
        self.assertTrue(conflicts(frozenset({"rpt:arap"}), frozenset({"w:ar_bill"})))
        self.assertTrue(conflicts(frozenset({"rpt:gl"}), frozenset({"new:gl:付"})))
        self.assertFalse(conflicts(frozenset({"rpt:gl"}), frozenset({"stock"})))
        self.assertFalse(conflicts(frozenset({"stock-read"}), frozenset({"stock-read"})))

    def test_exclusive_flags(self) -> None:
        self.assertTrue(conflicts(frozenset(), frozenset(), True, False))
        self.assertTrue(conflicts(frozenset(), frozenset(), False, True))
        self.assertTrue(conflicts(frozenset({"burst"}), frozenset({"meta"})))
        self.assertFalse(conflicts(frozenset({"meta"}), frozenset({"meta"})))

    def test_sched_picks_up_rules(self) -> None:
        found, bind, exclusive = sched.default_rules()
        self.assertIs(found, conflicts)
        self.assertIs(bind, bind_tags)
        self.assertIs(exclusive, is_exclusive)


if __name__ == "__main__":
    unittest.main()
