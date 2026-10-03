using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售发票：26 专票 VT 0，27 普票 VT 2。复核写 cChecker，应收审核人是 cVerifier。
    internal static class SaleInvoice
    {
        const string VerifySql = "select '' as editprop, salebillvouchzt.* from SaleBillVouchZT "
            + "salebillvouchzt with(nolock) where salebillvouchzt.sbvid=?";

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireKind(kind, "该单据类型不支持");
            Dictionary<string, object> snap = TakeSnap(ctx.Conn, kind, id);
            int vt = VtOf(ctx.Conn, kind, id);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                return CoRows.Pack(kind, id, snap, doms);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            RequireKind(kind, "该单据类型不支持直接审核");
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
            Dictionary<string, object> snap = TakeSnap(ctx.Conn, kind, id);
            int vt = VtOf(ctx.Conn, kind, id);
            if (CoRows.FlagOf(snap, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能直接审核");
            }
            // 红字发票（bReturnFlag=1）只放行参照退货单生成的（先开票等其余红票 400），VT 换成红字的 1 / 3（SaleGenRedDel）。
            if (CoRows.FlagOf(snap, "red"))
            {
                vt = SaleGen.RedVerifyVt(ctx.Conn, id, vt);
            }
            else if (action == "unverify")
            {
                SaleGen.RefuseRedLinked(ctx.Conn, id);
            }
            RefuseBefore(action, CoRows.Col(snap, "verifier"));
            string msg = Run(ctx, vt, id, action);
            if (msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", msg);
            }
            return After(ctx, kind, id, action);
        }

        static string Run(WorkContext ctx, int vt, int id, string action)
        {
            object sys = null;
            object co = null;
            object dom = null;
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                dom = AdoXml.LoadDom(ctx.Conn, VerifySql, id);
                return Finish(ctx.Conn, co, ref dom, ctx.Item, action);
            }
            finally
            {
                SaSession.CloseSa(sys);
                ComUtil.Final(dom);
                ComUtil.Final(co);
                ComUtil.Final(sys);
            }
        }

        static string Finish(object conn, object co, ref object dom, WorkItem item, string action)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                object[] args = new object[] { dom, action == "verify" };
                object ret = ComUtil.CallRef(co, "VerifyVouch", args, new int[] { 0, 1 });
                KeepDom(ref dom, args[0]);
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                if (msg.Length > 0)
                {
                    CoRows.Note(item, msg);
                    CoTrans.Rollback(conn);
                    open = false;
                    return msg;
                }
                CoTrans.CommitSeen(conn);
                open = false;
                return "";
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, item, open);
                throw;
            }
        }

        static void KeepDom(ref object dom, object updated)
        {
            if (updated == null || updated is DBNull || object.ReferenceEquals(dom, updated))
            {
                return;
            }
            ComUtil.Final(dom);
            dom = updated;
        }

        static ApiResult After(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = Rows.One(conn, StateSql(kind), new object[] { id });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "审核状态与操作不一致");
                }
                string checker = CoRows.Col(row, "checker");
                string date = CoRows.Col(row, "verified_at");
                RefuseAfter(action, checker, date, ctx.Session.OperatorName);
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["acc"] = ctx.Item.Acc ?? "";
                body["id"] = id;
                body["action"] = action;
                body["verified_by"] = checker;
                body["verified_at"] = date;
                body["type"] = kind.Name;
                body["ar_verifier"] = CoRows.Col(row, "ar_verifier");
                return ApiResult.Ok(body);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static void RefuseBefore(string action, string checker)
        {
            if (action == "verify" && checker.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (action == "unverify" && checker.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
        }

        static void RefuseAfter(string action, string checker, string date, string operatorName)
        {
            if (action != "verify")
            {
                if (checker.Length > 0)
                {
                    throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
                }
                return;
            }
            if (checker.Length == 0 || date.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            string name = operatorName == null ? "" : operatorName.Trim();
            if (!string.Equals(checker, name, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        static Dictionary<string, object> TakeSnap(object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = null;
            bool fallback = false;
            try
            {
                snap = CoRows.HeadRow(conn, kind, id);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Message == "该单据类型不支持")
                {
                    fallback = true;
                }
                else
                {
                    throw;
                }
            }
            if (!fallback)
            {
                if (snap == null)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                return snap;
            }
            snap = Rows.One(conn, SnapSql(kind), new object[] { id });
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return snap;
        }

        static int VtOf(object conn, VoucherKind kind, int id)
        {
            string sql = "select cVouchType from " + Ident(kind.HeadTable) + " where " + Ident(kind.IdColumn) + "=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
            int vt = MapVt(row == null ? "" : CoRows.Col(row, "cVouchType"));
            if (vt < 0)
            {
                throw new BridgeException(400, "bad_request", "该发票类型不支持");
            }
            return vt;
        }

        static int MapVt(string text)
        {
            decimal n;
            if (!decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out n))
            {
                return -1;
            }
            if (n == 26m)
            {
                return 0;
            }
            if (n == 27m)
            {
                return 2;
            }
            return -1;
        }

        static string SnapSql(VoucherKind kind)
        {
            return "select " + Ident(kind.VerifierColumn) + " as verifier, "
                + Ident(kind.VerifyDateColumn) + " as verify_date, "
                + Ident(kind.CodeColumn) + " as code, cVouchType as vouch_type, cVerifier as ar_verifier, "
                + "iswfcontrolled as wf, bReturnFlag as red from "
                + Ident(kind.HeadTable) + " where " + Ident(kind.IdColumn) + "=?";
        }

        static string StateSql(VoucherKind kind)
        {
            return "select " + Ident(kind.VerifierColumn) + " as checker, "
                + Ident(kind.VerifyDateColumn) + " as verified_at, cVerifier as ar_verifier from "
                + Ident(kind.HeadTable) + " where " + Ident(kind.IdColumn) + "=?";
        }

        const string IdentChars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_";

        static string Ident(string name)
        {
            if (!IdentOk(name))
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            return name;
        }

        static bool IdentOk(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 40)
            {
                return false;
            }
            for (int i = 0; i < name.Length; i++)
            {
                if (IdentChars.IndexOf(name[i]) < 0)
                {
                    return false;
                }
            }
            return true;
        }

        static void RequireKind(VoucherKind kind, string message)
        {
            if (kind == null || kind.Name != "sale_invoice")
            {
                throw new BridgeException(400, "bad_request", message);
            }
        }
    }
}
