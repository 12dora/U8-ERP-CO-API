using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 生产订单审核 / 弃审：U8 API 框架 U8ApiComBroker（MOrderAuditing / MOrderUnauditing，参数 mocode），登录子系统 MO。
    // API 自己开 TransactionScope 提交，不包 CoTrans；之后在新连接上回读 mom_orderdetail.Status（3 审核、1/2 未审、4 关闭）。
    internal static class MoApi
    {
        internal const string EnvProgId = "UFIDA.U8.U8APIFramework.U8EnvContext";
        internal const string BrokerProgId = "UFIDA.U8.U8APIFramework.U8ApiComBroker";
        const string AuditUrl = "U8API/MOrder/MOrderAuditing";
        const string UnauditUrl = "U8API/MOrder/MOrderUnauditing";
        const string RowsSql = "select o.MoCode, convert(varchar(10), d.Status) as Status,"
            + " convert(varchar(10), isnull(d.IsWFControlled,0)) as wf, d.RelsUser,"
            + " convert(varchar(10), d.RelsDate, 23) as RelsDate"
            + " from mom_order o join mom_orderdetail d on d.MoId=o.MoId where o.MoId=? order by d.SortSeq, d.MoDId";
        static readonly Regex ExPrefix = new Regex(@"^[A-Za-z_][\w.]*Exception:\s*", RegexOptions.CultureInvariant);
        // 堆栈帧：空白 +「在 」+ 至少两段、以 ASCII 标识符组成的带点类型名。
        static readonly Regex StackFrame = new Regex(@"\s+(?:在|at)\s+[A-Za-z_][A-Za-z0-9_`]*(?:\.[A-Za-z_][A-Za-z0-9_`<>]*)+",
            RegexOptions.CultureInvariant);

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            U8Resolve.Enter();
            try
            {
                return VerifyCore(ctx, kind, id, action);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult VerifyCore(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持");
            }
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, RowsSql, new object[] { id }, 500);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Gate(rows, action);
            string code = CoRows.Col(rows[0], "MoCode");
            // 预演（校验模式）：MOrderAuditing / MOrderUnauditing 自己提交，闸门查完就停。
            DryRun.Stop(ctx, action == "verify" ? "U8API MOrderAuditing" : "U8API MOrderUnauditing");
            Exception lost = Invoke(ctx, action == "verify" ? AuditUrl : UnauditUrl, code);
            List<Dictionary<string, object>> after = Reread(ctx, id, action, code);
            Confirm(after, action, code, lost);
            return Result(kind, id, code, action, after);
        }

        // InvokeApi 期间或之后出了异常或 IPC 错误（lost 非空）：回读已到目标状态就算成功；
        // 否则 IPC 错误报 503（生产制造服务未运行），其他异常结果未知（504），不能当成 U8 拒绝。
        static void Confirm(List<Dictionary<string, object>> after, string action, string code, Exception lost)
        {
            MoCount seen = Count(after);
            bool reached = action == "verify" ? seen.Open == 0 : seen.Released == 0;
            if (after.Count > 0 && reached)
            {
                return;
            }
            if (lost != null && IsIpc(lost.Message))
            {
                throw new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            if (lost != null)
            {
                string what = action == "verify" ? "审核" : "弃审";
                string text = FirstLine(lost.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "U8 " + what + "调用异常，结果未知，生产订单 " + code + (text.Length > 0 ? "：" + text : ""));
            }
            throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
        }

        // 先按行状态挡住，免得 U8 报「没有资料需要处理」。审批流控制的订单 API 会改走提交，暂不支持。
        static void Gate(List<Dictionary<string, object>> rows, string action)
        {
            MoCount n = Count(rows);
            if (n.Workflow > 0)
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
            if (action == "verify" && n.Open == 0)
            {
                string text = n.Closed > 0 && n.Released == 0 ? "单据已关闭" : "单据已审核";
                throw new BridgeException(409, "state_mismatch", text);
            }
            if (action == "unverify" && n.Released == 0)
            {
                string text = n.Closed > 0 && n.Open == 0 ? "单据已关闭" : "单据未审核";
                throw new BridgeException(409, "state_mismatch", text);
            }
        }

        // 调用前（建对象、Connect、赋参）出错照常报（IPC 直接 503）；InvokeApi 开始后的非桥异常和 IPC 错误返回给调用方去回读确认。
        internal static Exception Invoke(WorkContext ctx, string url, string moCode)
        {
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            object env = null;
            object bk = null;
            bool connected = false;
            bool invoked = false;
            try
            {
                env = Need(ComUtil.Create(EnvProgId));
                ComUtil.Set(env, "U8Login", ctx.Session.Login);
                bk = Need(ComUtil.Create(BrokerProgId));
                ComUtil.Call(bk, "Connect", new object[] { url, env });
                connected = true;
                ComUtil.Call(bk, "AssignNormalValue", new object[] { "mocode", moCode });
                invoked = true;
                bool ok = Values.Flag(ComUtil.Call(bk, "InvokeApi", new object[0]));
                string err = Values.Text(ComUtil.Call(bk, "GetLastError", new object[0])).Trim();
                if (err.Length > 0)
                {
                    CoRows.Note(ctx.Item, FirstLine(err));
                }
                if (!ok && IsIpc(err))
                {
                    return new InvalidOperationException(err);
                }
                if (!ok)
                {
                    throw Refused(err);
                }
                return null;
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, ex.GetType().Name + " " + FirstLine(ex.Message));
                if (!invoked)
                {
                    throw Failed(ex);
                }
                return ex;
            }
            finally
            {
                Disconnect(bk, connected);
                ComUtil.Final(bk);
                ComUtil.Final(env);
            }
        }

        internal static BridgeException Failed(Exception ex)
        {
            if (ex is FileNotFoundException || ex is FileLoadException || ex is TypeLoadException
                || ex is BadImageFormatException)
            {
                return new BridgeException(503, "com_unavailable", "U8 API 框架加载失败");
            }
            return Refused(ex.Message);
        }

        // U8MPool 没起时 U8 报「连接到 IPC 端口失败」。其他拒绝原文取第一行、去掉 .NET 异常类型名。
        internal static BridgeException Refused(string err)
        {
            string text = err ?? "";
            if (IsIpc(text))
            {
                return new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            string line = FirstLine(text);
            if (line.Length == 0)
            {
                line = "U8 拒绝了操作";
            }
            return new BridgeException(409, "u8_rejected", line);
        }

        internal static bool IsIpc(string text)
        {
            return text != null && text.IndexOf("IPC", StringComparison.Ordinal) >= 0;
        }

        internal static string FirstLine(string text)
        {
            string[] parts = (text ?? "").Split(new char[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            for (int i = 0; i < parts.Length; i++)
            {
                string line = ExPrefix.Replace(parts[i].Trim(), "").Trim();
                line = CutStack(line);
                if (line.Length > 0)
                {
                    return line.Length > 300 ? line.Substring(0, 300) : line;
                }
            }
            return "";
        }

        // U8 的原文有时把堆栈接在同一行：「审核或关闭的生产订单不可删除!   在 UFSoft.…」。
        // 只在内层异常（「 ---> 」）或堆栈帧（空白 +「在 」+ 带点的类型名，如「 在 UFSoft.U8.X.Y(…)」）处截断，
        // 消息里普通的「 在 」（如「存货 A 在 仓库 01 没有现存量」）不截。
        static string CutStack(string line)
        {
            int at = line.IndexOf(" ---> ", StringComparison.Ordinal);
            if (at > 0)
            {
                line = line.Substring(0, at).Trim();
            }
            Match m = StackFrame.Match(line);
            if (m.Success && m.Index > 0)
            {
                line = line.Substring(0, m.Index).Trim();
            }
            return line;
        }

        internal static void Disconnect(object bk, bool connected)
        {
            if (bk == null || !connected)
            {
                return;
            }
            try
            {
                ComUtil.Call(bk, "Disconnect", new object[0]);
            }
            catch (Exception)
            {
            }
        }

        internal static object Need(object com)
        {
            if (com == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 API 框架未注册");
            }
            return com;
        }

        static List<Dictionary<string, object>> Reread(WorkContext ctx, int id, string action, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return Rows.Query(conn, RowsSql, new object[] { id }, 500);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoApi " + FirstLine(ex.Message));
                string what = action == "verify" ? "审核" : "弃审";
                throw new BridgeException(504, "outcome_unknown", "已提交" + what + "但未能回读状态，生产订单 " + code);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult Result(VoucherKind kind, int id, string code, string action,
            List<Dictionary<string, object>> rows)
        {
            MoCount n = Count(rows);
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = rows.Count > 0 && n.Released == rows.Count;
            state["closed"] = rows.Count > 0 && n.Closed == rows.Count;
            state["verifier"] = CoRows.Col(rows[0], "RelsUser");
            state["verified_at"] = CoRows.Col(rows[0], "RelsDate");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = code;
            body["action"] = action;
            body["state"] = state;
            return ApiResult.Ok(body);
        }

        static MoCount Count(List<Dictionary<string, object>> rows)
        {
            MoCount n = new MoCount();
            for (int i = 0; i < rows.Count; i++)
            {
                string status = CoRows.Col(rows[i], "Status");
                if (status == "3")
                {
                    n.Released++;
                }
                else if (status == "4")
                {
                    n.Closed++;
                }
                else
                {
                    n.Open++;
                }
                if (CoRows.Col(rows[i], "wf") == "1")
                {
                    n.Workflow++;
                }
            }
            return n;
        }

        sealed class MoCount
        {
            public int Open;
            public int Released;
            public int Closed;
            public int Workflow;
        }
    }
}
