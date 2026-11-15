using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 报检单（QM01 / QM02）弃审：VoucherOperate("unconfirm")，传按 ID 读出的 DOM 和 voucherid。给 QmDelete（删除已审核的
    // 报检单前先弃审）和 QmInsUnverify（产品报检单单独弃审）用。实测 unconfirm 自己提交（调用后 @@TRANCOUNT 为 0）：不包 CoTrans；调用后先确认请求连接上没有残留事务
    // （有就回滚并 504），再在新连接上确认状态。
    // 报检单不开放单独审核（来料报检单连弃审也不开放，vouchers/verify 400）：实测 confirm 对未审核的报检单总是失败（ErrBag「单据审核失败！」），
    // U8 的审核 SQL 要写 IVERIFYSTATE，QMINSPECTVOUCHER 没有这一列；报检单只在保存时由 U8 按选项自动审核。
    internal static class QmVerify
    {
        const string NoInspectVerify = "来料报检单不支持单独审核、弃审（保存时由 U8 自动审核；删除时桥会先弃审）";

        // vouchers/verify 拒绝不可审核的类型时用的消息：报检单另给原因。
        public static string RefuseText(VoucherKind kind)
        {
            QmSpec spec = kind == null ? null : QmSpec.Of(kind);
            return spec != null && spec.Inspect ? NoInspectVerify : "该单据类型不支持直接审核";
        }

        // 弃审一次并在新连接上确认已弃审（到了就算成功）。没到：U8 原文 409；其他异常或回读失败 504。
        internal static Dictionary<string, object> Unconfirm(WorkContext ctx, QmCo co, QmSpec spec, int id)
        {
            // unconfirm 自己提交：预演（校验模式）在这里停（QmDelete 已先停；这里兜底）。
            DryRun.Stop(ctx, "UFQMCo.VoucherOperate(unconfirm)");
            object[] doms = QmDom.LoadedPair(ctx.Conn, spec, id);
            QmOutcome outcome;
            try
            {
                outcome = co.Operate(doms, "unconfirm", id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                QmDom.Release(doms);
            }
            if (!outcome.Ok)
            {
                CoRows.Note(ctx.Item, "VoucherOperate " + QmGen.Text(outcome));
            }
            if (QmGen.TranLeft(ctx))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 弃审后请求连接上仍有未结束的事务，已回滚，结果未知");
            }
            bool read;
            Dictionary<string, object> after = QmDoc.Fresh(ctx, spec, id, out read);
            if (after != null && !QmDoc.Verified(after))
            {
                return after;
            }
            throw Missed(outcome, read && after != null);
        }

        static BridgeException Missed(QmOutcome outcome, bool read)
        {
            if (!read)
            {
                return new BridgeException(504, "outcome_unknown", "已调用 U8 弃审，回读失败，结果未知");
            }
            if (outcome.Error.Length > 0)
            {
                return new BridgeException(409, "u8_rejected", outcome.Error);
            }
            if (outcome.Lost != null)
            {
                return new BridgeException(504, "outcome_unknown", "U8 弃审调用异常，结果未知：" + QmGen.Text(outcome));
            }
            return new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
        }
    }
}
