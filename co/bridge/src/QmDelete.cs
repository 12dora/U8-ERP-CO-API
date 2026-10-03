using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据删除（QM01–QM04）：VoucherOperate("delete")，传按 ID 读出的 DOM 和 voucherid（空白 DOM 会被 U8 当成
    // 「参照来源单据已被他人修改或删除」）。实测 delete 自己提交，不包 CoTrans：调用前查完闸门，调用后在新连接上确认单据已不在。
    // 报检单：已有检验单（行累计检验数量大于 0 或有检验单挂着）409；已审核的先弃审再删（U8「单据已审核不能删除！」），
    // 只在操作员有弃审权限时这样做，否则 409。弃审由 U8 自己提交，之后删除只要没成功（U8 拒绝、回读不符、任何异常）
    // 都按 504 outcome_unknown 返回并注明单据已弃审：状态已经变了，不能用表示「什么都没写」的 409。
    // 不再重新审核：U8 的 confirm 对报检单总是失败（见 QmVerify），接口没有办法恢复。
    // 检验单：已提交审批或已审核、已入库、已有不良品处理单 409。U8 删除后回退报检单行 FSUMCHECKQTY / 到货行 fInspectQuantity。
    internal static class QmDelete
    {
        const string GoneInsSql = "select count(*) from QMINSPECTVOUCHER where ID=?";
        const string GoneChkSql = "select count(*) from QMCHECKVOUCHER where ID=?";
        const string Unverified = "（报检单已弃审且未删除；接口无法重新审核，请在 U8 客户端处理或再次调用删除）";

        public static ApiResult Run(WorkContext ctx, QmSpec spec, int id)
        {
            PermContext perm = QmGen.RequireRule(ctx, spec, "delete");
            Dictionary<string, object> doc = QmDoc.Need(ctx.Conn, spec, id);
            QmGen.CheckRows(perm, spec, "delete", QmDoc.PermRows(ctx.Conn, spec, id, doc));
            bool verified = QmDoc.Verified(doc);
            Gate(ctx.Conn, spec, id, doc, perm);
            // 预演（校验模式）：删除、弃审都由 U8 自己提交，闸门查完就停。
            DryRun.Set("unverify_first", spec.Inspect && verified);
            DryRun.Stop(ctx, spec.Inspect && verified ? "UFQMCo.VoucherOperate(unconfirm, delete)" : "UFQMCo.VoucherOperate(delete)");
            bool unverified = false;
            using (QmCo co = QmCo.Open(ctx, spec))
            {
                if (spec.Inspect && verified)
                {
                    QmVerify.Unconfirm(ctx, co, spec, id);
                    unverified = true;
                    RemoveUnconfirmed(ctx, co, spec, id);
                }
                else
                {
                    Remove(ctx, co, spec, id);
                }
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(doc, "code");
            body["deleted"] = true;
            if (spec.Inspect)
            {
                body["unverified"] = unverified;
            }
            return ApiResult.Ok(body);
        }

        static void Gate(object conn, QmSpec spec, int id, Dictionary<string, object> doc, PermContext perm)
        {
            if (!spec.Inspect)
            {
                QmDoc.RequireCheckFree(conn, id, doc);
                return;
            }
            QmDoc.RequireNoCheck(conn, id, "删除");
            if (QmDoc.Verified(doc) && perm != null && !perm.HasAny(new string[] { spec.UnverifyAuth }))
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核，没有弃审权限，不能删除");
            }
        }

        // 弃审已提交之后的删除：没删掉的一切结果都是 504 并带上「已弃审」说明（Remove 自己的 504 同样补上说明）。
        static void RemoveUnconfirmed(WorkContext ctx, QmCo co, QmSpec spec, int id)
        {
            try
            {
                Remove(ctx, co, spec, id);
            }
            catch (BridgeException ex)
            {
                throw new BridgeException(504, "outcome_unknown", ex.Message + Unverified);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmDelete " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "弃审后删除出错：" + ex.Message + Unverified);
            }
        }

        static void Remove(WorkContext ctx, QmCo co, QmSpec spec, int id)
        {
            object[] doms = QmDom.LoadedPair(ctx.Conn, spec, id);
            QmOutcome outcome;
            try
            {
                outcome = co.Operate(doms, "delete", id.ToString(CultureInfo.InvariantCulture));
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
                throw new BridgeException(504, "outcome_unknown", "U8 删除后请求连接上仍有未结束的事务，已回滚，结果未知");
            }
            int left = Left(ctx, spec, id);
            if (left == 0)
            {
                return;
            }
            if (left < 0)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 删除，回读失败，结果未知");
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
            if (outcome.Lost != null)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 删除调用异常，结果未知：" + QmGen.Text(outcome));
            }
            throw new BridgeException(409, "state_mismatch", "U8 返回成功但单据仍在");
        }

        // 新连接上数一下：0 已删除，1 还在，-1 读不到。
        static int Left(WorkContext ctx, QmSpec spec, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return QmSql.Dec(QmSql.Scalar(conn, spec.Inspect ? GoneInsSql : GoneChkSql, id)) > 0m ? 1 : 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmDelete " + ex.Message);
                return -1;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
