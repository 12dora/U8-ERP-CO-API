using System;
using System.Collections.Generic;

namespace U8Co
{
    // 其他报检单、其他检验单删除：GetTheVoucher 载入后 DelVoucher，U8 自己提交，不包 CoTrans；调用前查完闸门、预演在调用前停，
    // 调用后在新连接上确认表头、表体都已不在。
    // 其他检验单：只删未审核、未提交审批、没有下游的；U8 删除后回退其他报检单行的 BFLAG，没回退 504（需人工核对）。
    // 其他报检单：已有其他检验单（INSPECTID 指向本单，或行 BFLAG=1）409；已审核的先 UnAuditVoucher 再删（要有弃审权限），
    // 弃审由 U8 自己提交，之后删除没成功一律 504 并注明已弃审（同来料报检单 QmDelete，接口不能重新审核）。
    internal static class QmOthDel
    {
        const string InsLinesSql = "select CINVCODE, CWHCODE from QMINSPECTVOUCHERS where ID=?";
        internal const string InsDownSql = "select count(*) from (select top 1 ID from QMCHECKVOUCHER where INSPECTID=?"
            + " union all select top 1 ID from QMINSPECTVOUCHERS where ID=? and isnull(BFLAG,0)=1) x";
        const string Unverified = "（其他报检单已弃审且未删除；接口无法重新审核，请在 U8 客户端处理或再次调用删除）";

        public static ApiResult Run(WorkContext ctx, QmOthSpec spec, int id)
        {
            PermContext perm = QmOthOps.RequireRule(ctx, spec, "delete");
            Dictionary<string, object> doc = QmOthOps.Doc(ctx.Conn, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey("delete")), PermRows(ctx.Conn, spec, id, doc));
            bool verified = QmDoc.Verified(doc);
            Gate(ctx.Conn, spec, id, doc, perm);
            // 预演（校验模式）：删除、弃审都由 U8 自己提交，闸门查完就停。
            DryRun.Set("unverify_first", spec.Inspect && verified);
            DryRun.Stop(ctx, spec.Inspect && verified ? "UFQMCo.clsOtherInspectVoucherCO.UnAuditVoucher, DelVoucher"
                : "UFQMCo." + (spec.Inspect ? "clsOtherInspectVoucherCO" : "clsOtherCheckVoucherCO") + ".DelVoucher");
            bool unverified = spec.Inspect && verified;
            if (unverified)
            {
                Unverify(ctx, spec, id);
                RemoveUnverified(ctx, spec, id, doc);
            }
            else
            {
                Remove(ctx, spec, id, doc);
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

        static void Gate(object conn, QmOthSpec spec, int id, Dictionary<string, object> doc, PermContext perm)
        {
            if (!spec.Inspect)
            {
                QmOthOps.RequireWfFree(doc);
                if (QmDoc.Verified(doc))
                {
                    throw new BridgeException(409, "state_mismatch", "其他检验单已审核，不能删除");
                }
                QmOthOps.RequireNoDownstream(conn, id, doc, "删除");
                return;
            }
            if (QmSql.Dec(QmSql.Scalar(conn, InsDownSql, id, id)) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "已有其他检验单，不能删除");
            }
            if (QmDoc.Verified(doc) && perm != null && !perm.HasAny(new string[] { spec.UnverifyAuth }))
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核，没有弃审权限，不能删除");
            }
        }

        // 弃审一次并在新连接上确认已弃审；没到：U8 原文 409，其他异常或回读失败 504。
        static void Unverify(WorkContext ctx, QmOthSpec spec, int id)
        {
            QmOutcome outcome = QmOthOps.Call(ctx, spec, id, "UnAuditVoucher");
            bool read;
            Dictionary<string, object> after = QmOthOps.Fresh(ctx, spec, id, out read);
            if (after == null || QmDoc.Verified(after))
            {
                throw QmOthOps.Missed(outcome, read && after != null, "弃审");
            }
        }

        // 弃审已提交之后的删除：没删掉的一切结果都是 504 并带上「已弃审」说明。
        static void RemoveUnverified(WorkContext ctx, QmOthSpec spec, int id, Dictionary<string, object> doc)
        {
            try
            {
                Remove(ctx, spec, id, doc);
            }
            catch (BridgeException ex)
            {
                throw new BridgeException(504, "outcome_unknown", ex.Message + Unverified);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthDel " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "弃审后删除出错：" + ex.Message + Unverified);
            }
        }

        static void Remove(WorkContext ctx, QmOthSpec spec, int id, Dictionary<string, object> doc)
        {
            QmOutcome outcome = QmOthOps.Call(ctx, spec, id, "DelVoucher");
            int left = Left(ctx, spec, id, CoRows.AsId(CoRows.Col(doc, "line")));
            if (left == 0)
            {
                return;
            }
            if (left == 1)
            {
                throw QmOthOps.Missed(outcome, true, "删除");
            }
            throw new BridgeException(504, "outcome_unknown", left < 0 ? "已调用 U8 删除，回读失败，结果未知"
                : "U8 已删除表头，但表体仍有行或其他报检单行的检验标记（BFLAG）没有回退，需在 U8 中核对");
        }

        // 新连接上核对：0 表头、表体都已删除（检验单另要报检单行 BFLAG 已回 0），1 表头还在，2 表头没了但表体或标记没回退，-1 读不到。
        static int Left(WorkContext ctx, QmOthSpec spec, int id, int line)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (QmSql.Dec(QmSql.Scalar(conn, "select count(*) from " + spec.HeadTable + " where ID=?", id)) > 0m)
                {
                    return 1;
                }
                bool lines = QmSql.Dec(QmSql.Scalar(conn, "select count(*) from " + spec.BodyTable + " where ID=?", id)) > 0m;
                bool flag = line > 0 && QmSql.Scalar(conn, QmOthSaved.LineFlagSql, line) == "1";
                return lines || flag ? 2 : 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthDel " + ex.Message);
                return -1;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 数据权限：检验单一行（表头）；报检单按每一行（存货、仓库）加表头供应商、部门（部门空时取报检部门）。
        internal static List<Dictionary<string, object>> PermRows(object conn, QmOthSpec spec, int id, Dictionary<string, object> doc)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (!spec.Inspect)
            {
                rows.Add(doc);
                return rows;
            }
            string dep = CoRows.Col(doc, "CDEPCODE");
            List<Dictionary<string, object>> lines = QmSql.Many(conn, InsLinesSql, 1000, id);
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(lines[i], StringComparer.OrdinalIgnoreCase);
                row["CVENCODE"] = CoRows.Col(doc, "CVENCODE");
                row["CDEPCODE"] = dep.Length > 0 ? dep : CoRows.Col(doc, "CINSPECTDEPCODE");
                rows.Add(row);
            }
            return rows;
        }
    }
}
