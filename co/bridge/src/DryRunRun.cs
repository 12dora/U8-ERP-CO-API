using System;
using System.Collections.Generic;

namespace U8Co
{
    // 预演的执行体和提交钩子的实际工作。
    // 回滚模式：处理函数照常跑到 CommitSeen / Commit，钩子先确认本连接 @@TRANCOUNT ≥ 1（U8 没有自行提交），在同一连接、
    // 同一事务里回读单据映像，然后 RollbackTrans，抛 DryRunDone。校验模式：处理函数在自己提交的组件之前调 DryRun.Stop。
    internal static class DryRunRun
    {
        const string RoutePrefix = "/u8co/v1/";
        const string SelfCommitText = "预演时 U8 组件已自行提交，写入可能已生效，请核对";
        const string NoCommitText = "预演没有走到提交点，无法确认没有写入，请核对";
        const string RolledBackText = "预演完成，已回滚，没有写入";
        const string ValidateRolledBackText = "预演完成（校验模式），已回滚，没有写入";
        const string StopText = "预演完成：只做了提交前的校验，没有调用 U8 组件，没有写入";
        const string NoChangeText = "预演完成：没有需要写入的改动";

        // 写线程的处理入口（StaExec.Write）。不是预演时就是 Dispatch.Handle。
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || !ctx.Item.DryRun)
            {
                return Dispatch.Handle(ctx);
            }
            DryRun.Begin(ctx);
            try
            {
                Invoke(ctx);
                return Outcome(ctx.Item, DryRun.Current);
            }
            finally
            {
                DryRun.End();
            }
        }

        // 处理函数抛什么、返回什么，只要预演已结束，都以预演结果为准（结果替换优先）。
        static void Invoke(WorkContext ctx)
        {
            try
            {
                Dispatch.Handle(ctx);
            }
            catch (Exception)
            {
                if (!DryRun.Finished)
                {
                    throw;
                }
            }
        }

        static ApiResult Outcome(WorkItem item, DryRunState s)
        {
            DryRunDone done = s.Final as DryRunDone;
            if (done != null)
            {
                ApiResult result = ApiResult.Ok(done.Body);
                result.Code = "dry_run";
                return result;
            }
            if (s.Final != null)
            {
                throw s.Final;
            }
            // 处理函数正常返回却没经过提交钩子也没 Stop：开过事务说明本该走到提交点；validate 模式可能是漏了 Stop 的
            // 自提交组件（它不经 CoTrans）。两种都不能声称没有写入。
            if (s.Begun || s.Mode != DryRunModes.Rollback)
            {
                CoRows.Note(item, "dry_run_no_commit");
                throw new BridgeException(504, "outcome_unknown", NoCommitText);
            }
            // rollback 模式且没开过事务（已锁定的再锁定等）：没有需要写入的改动，给当前映像。
            s.Detail["no_change"] = true;
            ApiResult same = ApiResult.Ok(Body(s, s.Mode, CurrentDocs(s, s.Ctx), NoChangeText));
            same.Code = "dry_run";
            return same;
        }

        // 回滚模式的提交钩子：返回 DryRunDone，或自行提交 / 回读失败时的 BridgeException。
        public static Exception Close(DryRunState s, object conn)
        {
            int count = AdoXml.QueryInt(conn, "SELECT @@TRANCOUNT");
            if (count < 1)
            {
                CoRows.Note(ItemOf(s), "dry_run_self_commit");
                return new BridgeException(504, "outcome_unknown", SelfCommitText);
            }
            List<object> docs;
            try
            {
                docs = DryRunPreview.Docs(conn, s);
            }
            catch (Exception ex)
            {
                QuietRollback(conn);
                CoRows.Note(ItemOf(s), "dry_run_preview_failed");
                return new BridgeException(500, "internal", "预演回读失败：" + ex.Message);
            }
            CoTrans.Rollback(conn);
            int left = AdoXml.QueryInt(conn, "SELECT @@TRANCOUNT");
            if (left != 0)
            {
                return new BridgeException(500, "internal", "预演回滚后 @@TRANCOUNT=" + left.ToString());
            }
            // 模式表是 validate 的路由（如 arap/voucher）停在自行提交的组件之前的那次提交：按 validate 报告。
            string mode = s.Mode == DryRunModes.Validate ? DryRunModes.Validate : DryRunModes.Rollback;
            string text = mode == DryRunModes.Rollback ? RolledBackText : ValidateRolledBackText;
            return new DryRunDone(mode, Body(s, mode, docs, text));
        }

        // 校验模式（DryRun.Stop）。
        public static Dictionary<string, object> ValidateBody(DryRunState s, WorkContext ctx)
        {
            return Body(s, DryRunModes.Validate, CurrentDocs(s, ctx), StopText);
        }

        // 已有单据的当前映像（可选，读不到就不给）；本连接若还开着事务，先读再回滚。
        static List<object> CurrentDocs(DryRunState s, WorkContext ctx)
        {
            List<object> docs = new List<object>();
            object conn = null;
            bool open = false;
            try
            {
                conn = ctx == null ? null : ctx.Conn;
                open = conn != null && AdoXml.QueryInt(conn, "SELECT @@TRANCOUNT") > 0;
                if (conn != null)
                {
                    docs = DryRunPreview.Docs(conn, s);
                }
            }
            catch (Exception)
            {
                docs = new List<object>();
                s.Detail["docs_unavailable"] = true;
            }
            finally
            {
                if (open)
                {
                    QuietRollback(conn);
                }
            }
            return docs;
        }

        static void QuietRollback(object conn)
        {
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
                // 连接随任务结束关闭，未提交的事务同样回滚。
            }
        }

        static WorkItem ItemOf(DryRunState s)
        {
            return s == null || s.Ctx == null ? null : s.Ctx.Item;
        }

        public static Dictionary<string, object> Body(DryRunState s, string mode, List<object> docs, string message)
        {
            if (docs != null && DryRunPreview.Fit(docs, s.Detail))
            {
                s.Detail["truncated"] = true;
            }
            WorkItem item = ItemOf(s);
            Dictionary<string, object> body = Head(item, mode);
            body["docs"] = docs ?? new List<object>();
            if (s.Detail.Count > 0)
            {
                body["detail"] = s.Detail;
            }
            body["warnings"] = Warnings(mode, item == null ? "" : item.Action);
            body["message"] = message;
            return body;
        }

        static Dictionary<string, object> Head(WorkItem item, string mode)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["dry_run"] = true;
            body["mode"] = mode;
            body["route"] = RouteOf(item == null ? null : item.Path);
            if (item != null && item.Type != null)
            {
                body["type"] = item.Type.Name;
            }
            body["action"] = item == null ? "" : item.Action ?? "";
            return body;
        }

        internal static List<object> Warnings(string mode, string action)
        {
            List<object> list = new List<object>();
            if (mode != DryRunModes.Rollback)
            {
                list.Add("validate_only");
                return list;
            }
            if (action == "create" || action == "generate")
            {
                list.Add("number_may_skip");
            }
            list.Add("locks_held");
            return list;
        }

        internal static string RouteOf(string path)
        {
            if (path == null)
            {
                return "";
            }
            return path.StartsWith(RoutePrefix, StringComparison.Ordinal) ? path.Substring(RoutePrefix.Length) : path;
        }
    }
}
