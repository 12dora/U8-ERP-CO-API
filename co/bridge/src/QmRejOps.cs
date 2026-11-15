using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单（QM05 / QM06）的入口（Dispatch 分派）与审核、弃审、删除。三者都是 GetTheVoucher 载入后调
    // AuditVoucher / UnAuditVoucher / DelVoucher，U8 自己提交：不包 CoTrans，调用前查完闸门、预演在调用前停，调用后在新连接上确认。
    // 受审批流控制的单据（IsWfControlled=1）不直接审核、弃审，走 workflow/*（409 workflow_enabled）。
    // 删除只收未审核、未提交审批、没有下游的单据；U8 删除后回退检验单的 BREJFLAG。弃审同样要求没有下游。
    internal static class QmRejOps
    {
        const string DocSql = "select CREJECTCODE as code, CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " CVENCODE, CINVCODE, CWHCODE, convert(varchar(20), CHECKID) as CheckId, convert(varchar(5), isnull(IsWfControlled,0)) as wf,"
            + " convert(varchar(10), isnull(iVerifyStateNew,0)) as vs from QMREJECTVOUCHER where ID=? and CVOUCHTYPE=?";
        const string GoneSql = "select count(*) from QMREJECTVOUCHER where ID=?";
        const string GoneLinesSql = "select count(*) from QMREJECTVOUCHERS where ID=?";
        const string FlagSql = "select convert(varchar(5), isnull(BREJFLAG,0)) from QMCHECKVOUCHER where ID=?";
        // 下游：各类出入库单行（含 rdrecords34）、报废单行、到货单行按 iRejectIds / irejectautoid 指向表体 AUTOID，
        // 报检单表头 REJECTID 指向本单、表体 REJECTAUTOID 指向表体 AUTOID，领料申请单行 crejectcode 是本单单号；
        // 表体已处理（BFLAG）或已有返工数量也算。参数：前 12 个是本单 ID，最后一个是单号。
        static readonly string DownSql = "select top 1 x.k from ("
            + Rd("rdrecords01") + Rd("rdrecords08") + Rd("rdrecords09") + Rd("rdrecords10") + Rd("rdrecords11")
            + Rd("rdrecords32") + Rd("rdrecords34") + Rd("ScrapVouchs")
            + " select 'arrival' as k from PU_ArrivalVouchs a join QMREJECTVOUCHERS s on a.irejectautoid=s.AUTOID where s.ID=?"
            + " union all select 'inspect' as k from QMINSPECTVOUCHER where REJECTID=?"
            + " union all select 'inspect' as k from QMINSPECTVOUCHERS b join QMREJECTVOUCHERS s on b.REJECTAUTOID=s.AUTOID where s.ID=?"
            + " union all select 'done' as k from QMREJECTVOUCHERS where ID=? and (isnull(BFLAG,0)=1 or isnull(FSUMREWORKQTY,0)<>0)"
            + " union all select 'apply' as k from MaterialAppVouchs where crejectcode=?"
            + ") x";
        const int DownIds = 12;

        public static bool Handles(VoucherKind kind)
        {
            return QmRejSpec.Handles(kind);
        }

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int checkId, Dictionary<string, object> head,
            object[] lines)
        {
            return QmRejGen.Run(ctx, Need(kind), checkId, head, lines);
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            QmRejSpec spec = Need(kind);
            if (action != "verify" && action != "unverify")
            {
                throw BridgeException.BadField("action", "action 只能是 verify 或 unverify");
            }
            bool undo = action == "unverify";
            VerifyGate(ctx, Gate(ctx, spec, id, action), id, undo);
            string method = undo ? "UnAuditVoucher" : "AuditVoucher";
            DryRun.Stop(ctx, "UFQMCo." + method);
            QmOutcome outcome = Call(ctx, spec, id, method);
            bool read;
            Dictionary<string, object> after = Fresh(ctx, spec, id, out read);
            if (after != null && QmDoc.Verified(after) != undo)
            {
                return Result(spec, id, action, after);
            }
            throw Missed(outcome, read && after != null, undo ? "弃审" : "审核");
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            QmRejSpec spec = Need(kind);
            Dictionary<string, object> doc = Gate(ctx, spec, id, "delete");
            if (QmDoc.Verified(doc) || CoRows.Col(doc, "vs") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "单据已提交审批或已审核，不能删除");
            }
            RequireNoDownstream(ctx.Conn, id, CoRows.Col(doc, "code"), "删除");
            DryRun.Stop(ctx, "UFQMCo.DelVoucher");
            QmOutcome outcome = Call(ctx, spec, id, "DelVoucher");
            int left = Left(ctx, id, CoRows.AsId(CoRows.Col(doc, "CheckId")));
            if (left == 1)
            {
                throw Missed(outcome, true, "删除");
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", left < 0 ? "已调用 U8 删除，回读失败，结果未知"
                    : "U8 已删除表头，但表体仍有行或检验单的不良品处理标记（BREJFLAG）没有回退，需在 U8 中核对");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(doc, "code");
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        // 不受审批流控制；审核要求未审核，弃审要求已审核且没有下游。
        static void VerifyGate(WorkContext ctx, Dictionary<string, object> doc, int id, bool undo)
        {
            RequireWfFree(doc);
            if (QmDoc.Verified(doc) != undo)
            {
                throw new BridgeException(409, "state_mismatch", undo ? "单据未审核" : "单据已审核");
            }
            if (undo)
            {
                RequireNoDownstream(ctx.Conn, id, CoRows.Col(doc, "code"), "弃审");
            }
        }

        // 功能权限 + 单据存在 + 数据权限（供应商、存货、仓库取表头）。
        static Dictionary<string, object> Gate(WorkContext ctx, QmRejSpec spec, int id, string op)
        {
            PermContext perm = RequireRule(ctx, spec, op);
            Dictionary<string, object> doc = QmSql.One(ctx.Conn, DocSql, id, spec.VouchType);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(doc);
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey(op)), rows);
            return doc;
        }

        internal static PermContext RequireRule(WorkContext ctx, QmRejSpec spec, string op)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, PermRegistry.ForKey(spec.RuleKey(op)));
            return perm;
        }

        static void RequireWfFree(Dictionary<string, object> doc)
        {
            if (CoRows.Col(doc, "wf") == "1" || CoRows.Col(doc, "vs") != "0")
            {
                throw new BridgeException(409, "workflow_enabled", "单据受审批流控制，请用 workflow/* 提交、审批或弃审");
            }
        }

        static void RequireNoDownstream(object conn, int id, string code, string what)
        {
            object[] args = new object[DownIds + 1];
            for (int i = 0; i < DownIds; i++)
            {
                args[i] = id;
            }
            args[DownIds] = code;
            string down = QmSql.Scalar(conn, DownSql, args);
            if (down.Length == 0)
            {
                return;
            }
            string text = down == "done" ? "不良品已处理（已入库或已返工）" : "已被下游单据引用";
            throw new BridgeException(409, "state_mismatch", text + "，不能" + what);
        }

        // 载入后调用一次；调用后请求连接上不能留着事务（有就回滚并 504）。
        static QmOutcome Call(WorkContext ctx, QmRejSpec spec, int id, string method)
        {
            QmOutcome outcome;
            using (QmRejCo co = QmRejCo.Open(ctx, spec))
            {
                co.Load(id);
                outcome = co.Act(method);
            }
            if (QmGen.TranLeft(ctx))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 调用后请求连接上仍有未结束的事务，已回滚，结果未知");
            }
            return outcome;
        }

        static Dictionary<string, object> Fresh(WorkContext ctx, QmRejSpec spec, int id, out bool read)
        {
            read = false;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> doc = QmSql.One(conn, DocSql, id, spec.VouchType);
                read = true;
                return doc;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmRejOps " + ex.Message);
                return null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 新连接上核对：0 表头、表体都已删除且检验单 BREJFLAG 已回 0，1 表头还在，2 表头没了但表体或标记没回退，-1 读不到。
        static int Left(WorkContext ctx, int id, int checkId)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (QmSql.Dec(QmSql.Scalar(conn, GoneSql, id)) > 0m)
                {
                    return 1;
                }
                bool lines = QmSql.Dec(QmSql.Scalar(conn, GoneLinesSql, id)) > 0m;
                bool flag = checkId > 0 && QmSql.Scalar(conn, FlagSql, checkId) == "1";
                return lines || flag ? 2 : 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmRejOps " + ex.Message);
                return -1;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static BridgeException Missed(QmOutcome outcome, bool read, string what)
        {
            if (!read)
            {
                return new BridgeException(504, "outcome_unknown", "已调用 U8 " + what + "，回读失败，结果未知");
            }
            if (outcome.Error.Length > 0)
            {
                return new BridgeException(409, "u8_rejected", outcome.Error);
            }
            if (outcome.Lost != null)
            {
                return new BridgeException(504, "outcome_unknown", "U8 " + what + "调用异常，结果未知：" + QmGen.Text(outcome));
            }
            return new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
        }

        static ApiResult Result(QmRejSpec spec, int id, string action, Dictionary<string, object> doc)
        {
            Dictionary<string, object> state = CoRows.StateOf(doc);
            // 审核人为准（U8 不良品处理单的审核日期可能为空）。
            state["verified"] = QmDoc.Verified(doc);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(doc, "code");
            body["action"] = action;
            body["state"] = state;
            // 与其他审核路由一致：顶层带审核人、审核时间（弃审后为空串）。
            body["verified_by"] = CoRows.Col(doc, "verifier");
            body["verified_at"] = CoRows.Col(doc, "verify_date");
            return ApiResult.Ok(body);
        }

        // 审批状态（与 workflow/state 的 wf 同名字段）。
        internal static Dictionary<string, object> Wf(Dictionary<string, object> row)
        {
            bool controlled = CoRows.Col(row, "wf") == "1";
            int vs = (int)QmSql.Dec(CoRows.Col(row, "vs"));
            Dictionary<string, object> wf = new Dictionary<string, object>();
            wf["controlled"] = controlled;
            wf["verify_state_new"] = vs;
            wf["status"] = !controlled ? "not_controlled" : (vs == 0 ? "not_submitted" : "in_approval");
            return wf;
        }

        static string Rd(string table)
        {
            return " select 'rd' as k from " + table + " r join QMREJECTVOUCHERS s on r.iRejectIds=s.AUTOID where s.ID=? union all";
        }

        static QmRejSpec Need(VoucherKind kind)
        {
            QmRejSpec spec = QmRejSpec.Of(kind);
            if (spec == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不是不良品处理单");
            }
            return spec;
        }
    }
}
