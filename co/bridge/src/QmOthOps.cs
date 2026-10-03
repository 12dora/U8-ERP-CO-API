using System;
using System.Collections.Generic;

namespace U8Co
{
    // 其他报检单、其他检验单写路由的入口（Dispatch 分派）与其他检验单的审核、弃审。登录子系统 QM（QmOthReq.Check）。
    // 新增 QmOthIns、生单 QmOthChk、删除 QmOthDel。审核、弃审：GetTheVoucher 载入后 AuditVoucher / UnAuditVoucher，U8 自己提交：
    // 不包 CoTrans，调用前查完闸门、预演（校验模式）在调用前停，调用后在新连接上确认。其他检验单不受审批流控制（IsWfControlled 恒为 0），
    // 万一受控 409 workflow_enabled（类型没有接审批流，workflow/* 本身 400）。弃审要求没有下游（入库、不良品处理）。
    // 其他报检单同样可审核、弃审（VO 的 AddVoucher 不按 QM.bOtherInspectAutoVerify 审核，桥在新增后补审核，
    // 补审核失败或没有审核权限时单据是未审核的，要能经接口补审）；弃审要求还没有其他检验单。
    internal static class QmOthOps
    {
        internal const string ChkDocSql = "select CCHECKCODE as code, CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " CVENCODE, CDEPCODE, CINVCODE, CWHCODE, convert(varchar(20), INSPECTAUTOID) as line,"
            + " convert(varchar(5), isnull(IsWfControlled,0)) as wf, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs,"
            + " convert(varchar(40), isnull(FSUMQUANTITY,0)) as sumq from QMCHECKVOUCHER where ID=? and CVOUCHTYPE=?";
        internal const string InsDocSql = "select CINSPECTCODE as code, CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " CVENCODE, CDEPCODE, CINSPECTDEPCODE from QMINSPECTVOUCHER where ID=? and CVOUCHTYPE=?";
        // 其他检验单的下游：各类入库行的 iCheckIdBaks、不良品处理单的 CHECKID（下游引用照样查询）。
        const string DownSql = "select top 1 x.k from (select 'in' as k from rdrecords01 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords08 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords09 where iCheckIdBaks=?"
            + " union all select 'in' as k from rdrecords10 where iCheckIdBaks=?"
            + " union all select 'reject' as k from QMREJECTVOUCHER where CHECKID=?) x";

        public static bool Handles(VoucherKind kind)
        {
            return QmOthSpec.Handles(kind);
        }

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (!QmOthSpec.IsInspect(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            return QmOthIns.Run(ctx, kind, head, lines);
        }

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            if (!QmOthSpec.IsCheck(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            return QmOthChk.Run(ctx, kind, sourceId, head, lines);
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            return QmOthDel.Run(ctx, Need(kind), id);
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            QmOthSpec spec = Need(kind);
            if (action != "verify" && action != "unverify")
            {
                throw BridgeException.BadField("action", "action 只能是 verify 或 unverify");
            }
            bool undo = action == "unverify";
            Dictionary<string, object> doc = Gate(ctx, spec, id, action);
            if (spec.Inspect)
            {
                InspectGate(ctx.Conn, doc, id, undo);
            }
            else
            {
                VerifyGate(ctx.Conn, doc, id, undo);
            }
            string method = undo ? "UnAuditVoucher" : "AuditVoucher";
            DryRun.Stop(ctx, "UFQMCo." + (spec.Inspect ? "clsOtherInspectVoucherCO." : "clsOtherCheckVoucherCO.") + method);
            QmOutcome outcome = Call(ctx, spec, id, method);
            bool read;
            Dictionary<string, object> after = Fresh(ctx, spec, id, out read);
            if (after != null && QmDoc.Verified(after) != undo)
            {
                return Result(spec, id, action, after);
            }
            throw Missed(outcome, read && after != null, undo ? "弃审" : "审核");
        }

        // 其他报检单（没有审批流列）：审核要求未审核；弃审要求已审核且没有其他检验单（INSPECTID 指向本单或行 BFLAG=1）。
        static void InspectGate(object conn, Dictionary<string, object> doc, int id, bool undo)
        {
            if (QmDoc.Verified(doc) != undo)
            {
                throw new BridgeException(409, "state_mismatch", undo ? "单据未审核" : "单据已审核");
            }
            if (undo && QmSql.Dec(QmSql.Scalar(conn, QmOthDel.InsDownSql, id, id)) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "已有其他检验单，不能弃审");
            }
        }

        static void VerifyGate(object conn, Dictionary<string, object> doc, int id, bool undo)
        {
            RequireWfFree(doc);
            if (QmDoc.Verified(doc) != undo)
            {
                throw new BridgeException(409, "state_mismatch", undo ? "单据未审核" : "单据已审核");
            }
            if (undo)
            {
                RequireNoDownstream(conn, id, doc, "弃审");
            }
        }

        internal static void RequireWfFree(Dictionary<string, object> doc)
        {
            if (CoRows.Col(doc, "wf") == "1" || CoRows.Col(doc, "vs") != "0")
            {
                throw new BridgeException(409, "workflow_enabled", "单据受审批流控制，请到 U8 客户端处理");
            }
        }

        // 已入库（FSUMQUANTITY）、被入库单或不良品处理单引用：409。
        internal static void RequireNoDownstream(object conn, int id, Dictionary<string, object> doc, string what)
        {
            if (QmSql.Dec(CoRows.Col(doc, "sumq")) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "其他检验单已入库，不能" + what);
            }
            string down = QmSql.Scalar(conn, DownSql, id, id, id, id, id);
            if (down.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch",
                    (down == "reject" ? "其他检验单已生成不良品处理单" : "其他检验单已被入库单引用") + "，不能" + what);
            }
        }

        // 功能权限 + 单据存在 + 数据权限（检验单一行：供应商、部门、存货、仓库取表头）。
        static Dictionary<string, object> Gate(WorkContext ctx, QmOthSpec spec, int id, string op)
        {
            PermContext perm = RequireRule(ctx, spec, op);
            Dictionary<string, object> doc = Doc(ctx.Conn, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey(op)), QmOthDel.PermRows(ctx.Conn, spec, id, doc));
            return doc;
        }

        internal static Dictionary<string, object> Doc(object conn, QmOthSpec spec, int id)
        {
            return QmSql.One(conn, spec.Inspect ? InsDocSql : ChkDocSql, id, spec.VouchType);
        }

        internal static PermContext RequireRule(WorkContext ctx, QmOthSpec spec, string op)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, PermRegistry.ForKey(spec.RuleKey(op)));
            return perm;
        }

        // 载入后调用一次；调用后请求连接上不能留着事务（有就回滚并 504）。
        internal static QmOutcome Call(WorkContext ctx, QmOthSpec spec, int id, string method)
        {
            QmOutcome outcome;
            using (QmRejCo co = spec.Open(ctx))
            {
                co.Load(id);
                outcome = co.Act(method);
            }
            AfterCall(ctx);
            return outcome;
        }

        internal static void AfterCall(WorkContext ctx)
        {
            if (QmGen.TranLeft(ctx))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 调用后请求连接上仍有未结束的事务，已回滚，结果未知");
            }
        }

        internal static Dictionary<string, object> Fresh(WorkContext ctx, QmOthSpec spec, int id, out bool read)
        {
            read = false;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> doc = Doc(conn, spec, id);
                read = true;
                return doc;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthOps " + ex.Message);
                return null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        internal static BridgeException Missed(QmOutcome outcome, bool read, string what)
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

        static ApiResult Result(QmOthSpec spec, int id, string action, Dictionary<string, object> doc)
        {
            Dictionary<string, object> state = CoRows.StateOf(doc);
            state["verified"] = QmDoc.Verified(doc);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(doc, "code");
            body["action"] = action;
            body["state"] = state;
            body["verified_by"] = CoRows.Col(doc, "verifier");
            body["verified_at"] = CoRows.Col(doc, "verify_date");
            return ApiResult.Ok(body);
        }

        internal static QmOthSpec Need(VoucherKind kind)
        {
            QmOthSpec spec = QmOthSpec.Of(kind);
            if (spec == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不是其他报检单或其他检验单");
            }
            return spec;
        }
    }
}
