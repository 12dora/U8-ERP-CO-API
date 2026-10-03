using System;
using System.Globalization;
using System.Reflection;
using System.Text;

namespace U8Co
{
    // 代理自己开连接。不能再套本进程的 ADO 事务。
    internal static class WfProxy
    {
        const string ProxyId = "UFIDA.U8.Audit.ServiceProxy.AuditServiceProxy";

        public static void EnsureFlow(WorkContext ctx, string biz)
        {
            object[] args = new object[] { biz, biz + ".Submit", Token(ctx), "" };
            object ret = CallProxy(ctx, "IsFlowEnabled2", args, new int[] { 3 });
            string err = Text(args[3]);
            if (!Values.Flag(ret))
            {
                if (err.Length == 0) err = "单据未启用审批流";
                ctx.Item.Detail = err;
                throw new BridgeException(409, "workflow_disabled", err);
            }
        }

        public static string Run(WorkContext ctx, VoucherKind kind, int id, string code, string action)
        {
            ctx.WfNote = null;
            if (action == "submit") return WfLtm.Submit(ctx, kind, id);
            if (action == "withdraw") return WfLtm.Withdraw(ctx, kind, id, code);
            if (action == "approve") return Audit(ctx, kind, id, code, 1, 0);
            if (action == "disagree") return Audit(ctx, kind, id, code, 2, 2);
            if (action == "return") return Audit(ctx, kind, id, code, 2, 0);
            if (action == "abandon") return Abandon(ctx, kind, id, code);
            if (action == "resubmit") return Resubmit(ctx, kind, id, code);
            throw new BridgeException(400, "bad_request", "未知审批操作");
        }

        static string Audit(WorkContext ctx, VoucherKind kind, int id, string code, short action, short state)
        {
            string opinion = Opinion(ctx);
            string keySet = KeySet(id, kind.BizObjectId, code, opinion, false);
            object[] args = new object[] { keySet, action, state, opinion, Token(ctx), "" };
            object ret = CallProxy(ctx, "Audit2", args, new int[] { 5 });
            if (ctx.WfNote != null) return ctx.WfNote;
            string raw = Text(args[5]);
            return Done(ctx, Values.Flag(ret), ResultText(raw), ResultFailed(raw));
        }

        static string Abandon(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            string opinion = Opinion(ctx);
            string keySet = KeySet(id, kind.BizObjectId, code, opinion, false);
            object[] args = new object[] { keySet, opinion, (short)0, Token(ctx), "" };
            object ret = CallProxy(ctx, "Abandon2", args, new int[] { 4 });
            if (ctx.WfNote != null) return ctx.WfNote;
            string raw = Text(args[4]);
            return Done(ctx, Values.Flag(ret), ResultText(raw), ResultFailed(raw));
        }

        static string Resubmit(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            string keySet = KeySet(id, kind.BizObjectId, code, "", true);
            object[] args = new object[] { keySet, Token(ctx), "" };
            object ret = CallProxy(ctx, "SubmitResubmitMessage2", args, new int[] { 2 });
            if (ctx.WfNote != null) return ctx.WfNote;
            return Done(ctx, Values.Flag(ret), Text(args[2]), false);
        }

        static object CallProxy(WorkContext ctx, string method, object[] args, int[] refs)
        {
            object target = null;
            try
            {
                target = ComUtil.Create(ProxyId);
                if (target == null)
                {
                    throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
                }
                return CallProxyMethod(ctx, target, method, args, refs);
            }
            finally
            {
                ComUtil.Final(target);
            }
        }

        static object CallProxyMethod(WorkContext ctx, object target, string method, object[] args, int[] refs)
        {
            try
            {
                return ComUtil.CallRef(target, method, args, refs);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                if (method == "IsFlowEnabled2")
                {
                    throw Rejected(ctx, ex);
                }
                ctx.WfNote = Workflow.Rescue(ctx, ex);
                return null;
            }
        }

        static string KeySet(int id, string biz, string code, string opinion, bool resubmit)
        {
            StringBuilder buf = new StringBuilder();
            buf.Append("<KeySet>");
            Key(buf, "VoucherId", IdText(id));
            Key(buf, "VoucherType", biz);
            Key(buf, "VoucherCode", code);
            if (opinion != null && opinion.Length > 0)
            {
                Key(buf, "Opinion", opinion);
            }
            if (resubmit)
            {
                Key(buf, "ReSubmit", "1");
            }
            buf.Append("</KeySet>");
            return buf.ToString();
        }

        static void Key(StringBuilder buf, string name, string value)
        {
            buf.Append("<Key name=\"");
            buf.Append(name);
            buf.Append("\" value=\"");
            buf.Append(Esc(value));
            buf.Append("\"/>");
        }

        static string Esc(string text)
        {
            if (text == null || text.Length == 0) return "";
            return text.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
                .Replace("\"", "&quot;").Replace("'", "&apos;");
        }

        static string ResultText(string raw)
        {
            if (raw == null || raw.Length == 0) return "";
            string err = Attr(raw, "errMsg");
            if (err.Length > 0) return err;
            if (raw.IndexOf("<", StringComparison.Ordinal) >= 0) return "";
            return raw.Trim();
        }

        static bool ResultFailed(string raw)
        {
            string flag = Attr(raw, "AuditResult");
            return flag.Equals("False", StringComparison.OrdinalIgnoreCase);
        }

        static string Attr(string xml, string name)
        {
            if (xml == null || name == null) return "";
            string key = name + "=\"";
            int at = xml.IndexOf(key, StringComparison.Ordinal);
            if (at < 0) return "";
            int start = at + key.Length;
            int end = xml.IndexOf('"', start);
            if (end < 0) return "";
            return Unesc(xml.Substring(start, end - start));
        }

        static string Unesc(string text)
        {
            return text.Replace("&quot;", "\"").Replace("&apos;", "'")
                .Replace("&lt;", "<").Replace("&gt;", ">").Replace("&amp;", "&");
        }

        internal static string Done(WorkContext ctx, bool ok, string message, bool failed)
        {
            string text = Scrub(message ?? "");
            if ((!ok || failed) && text.Length == 0)
            {
                text = "U8 拒绝了该操作";
            }
            ctx.Item.Detail = text;
            if (!ok || failed)
            {
                throw new BridgeException(409, "u8_rejected", text);
            }
            return text;
        }

        static string Opinion(WorkContext ctx)
        {
            if (ctx.Item.Opinion == null) return "";
            return ctx.Item.Opinion;
        }

        static string Token(WorkContext ctx)
        {
            if (ctx.Session.Token == null) return "";
            return ctx.Session.Token;
        }

        internal static string IdText(int id)
        {
            return id.ToString(CultureInfo.InvariantCulture);
        }

        internal static string Text(object value)
        {
            if (value == null || value is DBNull) return "";
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        internal static BridgeException Rejected(WorkContext ctx, Exception ex)
        {
            string text = Scrub(Root(ex));
            if (text.Length == 0) text = "U8 拒绝了该操作";
            ctx.Item.Detail = text;
            return new BridgeException(409, "u8_rejected", text);
        }

        // 异常文本里若带出令牌或连接串，不回给调用方。
        static string Scrub(string text)
        {
            if (text == null || text.Length == 0) return "";
            if (text.IndexOf("Password=", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("Pwd=", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("SignedToken", StringComparison.Ordinal) >= 0
                || text.IndexOf("<ufsoft", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "U8 拒绝了该操作";
            }
            return text;
        }

        static string Root(Exception ex)
        {
            Exception cur = ex;
            while (cur is TargetInvocationException && cur.InnerException != null)
            {
                cur = cur.InnerException;
            }
            if (cur.Message == null) return "";
            return cur.Message;
        }

        internal static string NoteOf(WorkContext ctx, Exception ex)
        {
            string text = Scrub(Root(ex));
            if (text.Length == 0) text = "U8 调用异常";
            string note = "U8 调用异常，回读已是目标状态：" + text;
            RememberText(ctx, note);
            return note;
        }

        internal static void Remember(WorkContext ctx, Exception ex)
        {
            RememberText(ctx, Scrub(Root(ex)));
        }

        static void RememberText(WorkContext ctx, string text)
        {
            if (ctx != null && ctx.Item != null) ctx.Item.Detail = text ?? "";
        }

        internal static BridgeException UnknownOutcome()
        {
            return new BridgeException(504, "outcome_unknown", "审批调用异常，结果未知");
        }
    }
}
