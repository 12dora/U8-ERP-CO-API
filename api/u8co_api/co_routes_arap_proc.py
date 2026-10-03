"""应收应付处理路由（写）：应收冲应付 / 应付冲应收、并账、红票对冲、取消处理、处理制单、汇兑损益与取消；坏账处理。
注册见 co_routes.register_co_routes。汇兑损益、坏账（含其取消、制单）默认关闭（桥在登录前 403 feature_disabled），
桥打开 enableReplicatedWrites 后只对测试账套开放，其余 403 test_account_only。"""

from u8co_api.co_models_arap_bad import BAD_DEBT_HELP, BAD_DEBT_SUMMARY, CoArapBadDebtIn, CoArapBadDebtOut
from u8co_api.co_models_arap_batch import (
    CANCEL_HELP,
    CANCEL_SUMMARY,
    EXGAIN_CANCEL_HELP,
    EXGAIN_CANCEL_SUMMARY,
    EXGAIN_HELP,
    EXGAIN_SUMMARY,
    VOUCHER_HELP,
    VOUCHER_SUMMARY,
    CoArapExGainCancelIn,
    CoArapExGainIn,
    CoArapProcCancelIn,
    CoArapProcVoucherIn,
)
from u8co_api.co_models_arap_proc import (
    MERGE_HELP,
    MERGE_SUMMARY,
    RED_OFFSET_HELP,
    RED_OFFSET_SUMMARY,
    TRANSFER_HELP,
    TRANSFER_SUMMARY,
    CoArapMergeIn,
    CoArapRedOffsetIn,
    CoArapTransferIn,
)
from u8co_api.co_models_arap_proc_out import (
    CoArapExGainCancelOut,
    CoArapExGainOut,
    CoArapMergeOut,
    CoArapProcCancelOut,
    CoArapProcVoucherOut,
    CoArapRedOffsetOut,
    CoArapTransferOut,
)
from u8co_api.co_table import CoRoute

TAG_ARAP_PROC = "应收应付处理"


def _route(name: str, models: tuple[type, type], texts: tuple[str, str], operation_id: str) -> CoRoute:
    body, out = models
    summary, description = texts
    return CoRoute(
        "/v1/co/" + name, "/v1/" + name, body, out, summary, description, operation_id, "co:" + name, TAG_ARAP_PROC
    )


ARAP_PROC_ROUTES = (
    _route("arap/transfer", (CoArapTransferIn, CoArapTransferOut), (TRANSFER_SUMMARY, TRANSFER_HELP), "coArapTransfer"),
    _route("arap/merge", (CoArapMergeIn, CoArapMergeOut), (MERGE_SUMMARY, MERGE_HELP), "coArapMerge"),
    _route(
        "arap/red_offset",
        (CoArapRedOffsetIn, CoArapRedOffsetOut),
        (RED_OFFSET_SUMMARY, RED_OFFSET_HELP),
        "coArapRedOffset",
    ),
    _route(
        "arap/process/cancel",
        (CoArapProcCancelIn, CoArapProcCancelOut),
        (CANCEL_SUMMARY, CANCEL_HELP),
        "coArapProcessCancel",
    ),
    _route(
        "arap/process/voucher",
        (CoArapProcVoucherIn, CoArapProcVoucherOut),
        (VOUCHER_SUMMARY, VOUCHER_HELP),
        "coArapProcessVoucher",
    ),
    # 第二级写入：默认关闭（feature_disabled），打开后只对测试账套开放。
    _route(
        "arap/exchange_gain",
        (CoArapExGainIn, CoArapExGainOut),
        (EXGAIN_SUMMARY, EXGAIN_HELP),
        "coArapExchangeGain",
    ),
    _route(
        "arap/exchange_gain/cancel",
        (CoArapExGainCancelIn, CoArapExGainCancelOut),
        (EXGAIN_CANCEL_SUMMARY, EXGAIN_CANCEL_HELP),
        "coArapExchangeGainCancel",
    ),
    # 坏账发生 / 收回 / 计提（第二级写入）。
    _route(
        "arap/bad_debt",
        (CoArapBadDebtIn, CoArapBadDebtOut),
        (BAD_DEBT_SUMMARY, BAD_DEBT_HELP),
        "coArapBadDebt",
    ),
)
