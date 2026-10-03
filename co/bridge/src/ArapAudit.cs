using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security;

namespace U8Co
{
    // 采购发票应付审核、销售发票应收审核及弃审（vouchers/verify，action = arap_verify / arap_unverify）。
    // 登录子系统 AP / AR（Requests.NeedVerifyAction 已换）。UFAPBO：clsAccount_AP.Init(login, 子系统) 按值传、返回值不看；
    // clsPub_AP.Init(login, 请求连接, acc, 子系统) 引用 {0,1,2,3}。先 PUVouchCanSign / SAVouchCanSign（引用 {3}），
    // 再 Sign_PurBill / Sign_SaleBill 或 CancelSign_*（引用 {1}，msg 以 "" 起），返回 true 为成功。
    // 测试账套实测（采购）：Sign / CancelSign 都在请求连接的事务里（@@TRANCOUNT 1，回滚能撤销），照常包 CoTrans。
    internal static class ArapAudit
    {
        public const string Verify = "arap_verify";
        public const string Unverify = "arap_unverify";
        const string AccProg = "UFAPBO.clsAccount_AP";
        const string PubProg = "UFAPBO.clsPub_AP";

        public static bool IsAction(string action)
        {
            return action == Verify || action == Unverify;
        }

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            ArapAuditSpec spec = ArapAuditSpec.Of(kind);
            if (!IsAction(action))
            {
                throw new BridgeException(400, "bad_request", "action 只能是 arap_verify 或 arap_unverify");
            }
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            bool undo = action == Unverify;
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(undo ? spec.UnverifyRule : spec.VerifyRule);
            PermCheck.RequireRule(perm, rule);
            object acc = null;
            object pub = null;
            try
            {
                ctx.DropLogin();
                acc = Make(AccProg);
                ComUtil.Call(acc, "Init", new object[] { ctx.Session.Login, spec.Sub });
                pub = Make(PubProg);
                ComUtil.CallRef(pub, "Init", new object[] { ctx.Session.Login, ctx.Conn, acc, spec.Sub },
                    new int[] { 0, 1, 2, 3 });
                Tran(ctx, spec, pub, id, undo, rule);
            }
            finally
            {
                ComUtil.Final(pub);
                ComUtil.Final(acc);
            }
            return Reread(ctx, kind, spec, id, action);
        }

        static object Make(string progId)
        {
            object made = ComUtil.Create(progId);
            if (made == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
            }
            return made;
        }

        static void Tran(WorkContext ctx, ArapAuditSpec spec, object pub, int id, bool undo, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Dictionary<string, object> head = ArapAuditSql.Locked(conn, spec, id);
                // 数据权限按表头（PermCheck.Of 已在 Run 里取过，这里取同一份快照）。
                if (!PermCheck.RowAllowed(PermCheck.Of(ctx), rule, head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
                ArapAuditGate.Check(conn, spec, head, undo, ctx.Item.Date);
                string type = CoRows.Col(head, "vtype");
                string code = CoRows.Col(head, "code");
                CanSign(ctx, spec, pub, type, code, undo);
                string method = undo ? spec.CancelSign : spec.Sign;
                string msg;
                bool ok = Call(pub, method, Cond(spec, id, type, code), out msg);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoRows.Note(ctx.Item, method + " " + (ok ? "true" : "false") + (msg.Length > 0 ? " " + msg : ""));
                if (!ok)
                {
                    throw new BridgeException(409, "u8_rejected", ArapCo.Said(msg, "U8 拒绝了" + spec.Side + "审核"));
                }
                if (!undo)
                {
                    ArapAuditKm.Fill(ctx, spec, pub, type, code);
                }
                string name = OperatorOf(ctx);
                StockCall.AfterCheck(ctx, delegate(object c) { InTran(c, spec, id, !undo, name); },
                    spec.IdAttr + " " + id.ToString(CultureInfo.InvariantCulture));
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 取消时 bCancel=true（未经实测）。返回 false 时 msg 是 U8 的原因，409 原文带回。
        static void CanSign(WorkContext ctx, ArapAuditSpec spec, object pub, string type, string code, bool undo)
        {
            object[] args = new object[] { type, code, undo, "" };
            object ret = ComUtil.CallRef(pub, spec.CanSign, args, new int[] { 3 });
            string msg = Values.Text(args[3]).Trim();
            CoRows.Note(ctx.Item, spec.CanSign + " " + Values.Text(ret) + (msg.Length > 0 ? " " + msg : ""));
            if (!Values.Flag(ret))
            {
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(msg, "U8 不允许" + spec.Side + (undo ? "弃审" : "审核")));
            }
        }

        static bool Call(object pub, string method, string cond, out string msg)
        {
            object[] args = new object[] { cond, "" };
            object ret = ComUtil.CallRef(pub, method, args, new int[] { 1 });
            msg = Values.Text(args[1]).Trim();
            return Values.Flag(ret);
        }

        // 照 U8 界面（UFAPSuite）拼的单张条件；属性名大小写敏感，值一律转义。不带 cLink / Ufts / bFirst。
        internal static string Cond(ArapAuditSpec spec, int id, string type, string code)
        {
            if (type.Length == 0 || code.Length == 0)
            {
                throw new BridgeException(500, "internal", "单据条件缺少类型或单号");
            }
            return "<condition type='0' " + spec.IdAttr + "='" + Esc(id.ToString(CultureInfo.InvariantCulture))
                + "' cVouchType='" + Esc(type) + "' cVouchID='" + Esc(code) + "' bneedcheck='1'/>";
        }

        static string Esc(string text)
        {
            return SecurityElement.Escape(text ?? "") ?? "";
        }

        static string OperatorOf(WorkContext ctx)
        {
            string name = ctx.Session == null ? null : ctx.Session.OperatorName;
            return name == null ? "" : name.Trim();
        }

        // 提交前：审核人写成了登录操作员姓名且有登记行（弃审：审核人已清、登记行已删），否则回滚。
        static void InTran(object conn, ArapAuditSpec spec, int id, bool want, string name)
        {
            string problem = Mismatch(ArapAuditSql.State(conn, spec, id), spec, want, name);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", problem);
            }
        }

        // 与目标状态不符时返回原因，符合返回 null。
        static string Mismatch(Dictionary<string, object> row, ArapAuditSpec spec, bool want, string name)
        {
            if (row == null)
            {
                return "单据不存在";
            }
            string auditor = CoRows.Col(row, "auditor");
            bool dated = CoRows.Col(row, "audit_date").Length > 0;
            int own = ArapAuditSql.Count(row, "own");
            // 审核日期同审核人一起写入、一起清除：两者应同为空或同不为空。
            if (!want)
            {
                bool cleared = auditor.Length == 0 && !dated && own == 0;
                return cleared ? null : "U8 没有清除" + spec.Side + "审核人、审核日期或审核登记行";
            }
            if (auditor.Length == 0 || !dated || own == 0)
            {
                return "U8 没有写入" + spec.Side + "审核人、审核日期或审核登记行";
            }
            if (!string.Equals(auditor, name, StringComparison.Ordinal))
            {
                return spec.Side + "审核人与登录操作员姓名不一致";
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）回读；读不出来或对不上都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, VoucherKind kind, ArapAuditSpec spec, int id, string action)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = ArapAuditSql.State(conn, spec, id);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapAudit " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交但未能回读" + spec.Side + "审核状态，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            string problem = Mismatch(row, spec, action == Verify, OperatorOf(ctx));
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", spec.Side + "审核已提交但回读状态不符（标识 "
                    + id.ToString(CultureInfo.InvariantCulture) + "）：" + problem + "；请先查询核对，不要直接重试");
            }
            Dictionary<string, object> state = ArapAuditSql.StateOf(row);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["type"] = kind.Name;
            body["id"] = id;
            body["action"] = action;
            body["verified_by"] = state["arap_verifier"];
            body["verified_at"] = state["arap_verified_at"];
            body["state"] = state;
            return ApiResult.Ok(body);
        }

        // vouchers/load：采购发票、销售发票的 state 另加 arap_verified / arap_verifier / arap_verified_at / gl_voucher。
        public static ApiResult WithState(object conn, VoucherKind kind, int id, ApiResult result)
        {
            if (!ArapAuditSpec.Supports(kind) || result == null || result.Body == null)
            {
                return result;
            }
            object raw;
            result.Body.TryGetValue("state", out raw);
            Dictionary<string, object> state = raw as Dictionary<string, object>;
            Dictionary<string, object> row = state == null ? null : ArapAuditSql.State(conn, ArapAuditSpec.Of(kind), id);
            if (row == null)
            {
                return result;
            }
            foreach (KeyValuePair<string, object> pair in ArapAuditSql.StateOf(row))
            {
                state[pair.Key] = pair.Value;
            }
            return result;
        }
    }
}
