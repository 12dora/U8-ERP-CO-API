using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class Workflow
    {
        public static ApiResult Run(WorkContext ctx, string action)
        {
            VoucherKind kind = QmKind(ctx);
            int id = ctx.Item.Id;
            RequireOpinion(ctx.Item, action);
            WfSnap before = Need(ctx, kind, id);
            if (Person(ctx).Length == 0)
            {
                throw new BridgeException(409, "not_current_approver", "操作员未关联人员");
            }
            RefuseDisabled(before);
            RefusePre(ctx, action, before);
            ctx.WfBefore = before;
            ctx.WfNote = null;
            // cleanOrphanTasks：在任何 U8 调用（含 IsFlowEnabled2）之前取串行锁，取不到就 503 busy_timeout、不调 U8；
            // 调用前快照本机 UA_TaskLog，U8 返回即记窗口终点，回读后（含 U8 拒绝）清掉 QM 终审插件留下的孤儿任务；
            // Finish 在每条路径上放锁。EnsureFlow 抛错时还没记窗口终点，不清理。
            OrphanScan scan = TaskOrphans.Begin(ctx, action);
            string message;
            WfSnap after;
            try
            {
                WfProxy.EnsureFlow(ctx, kind.BizObjectId);
                DryStop(ctx, action, before);
                try
                {
                    message = WfProxy.Run(ctx, kind, id, before.Code, action);
                }
                finally
                {
                    TaskOrphans.Mark(ctx, scan);
                }
                after = WfState.LoadFresh(ctx, kind, id);
                RefusePost(ctx, action, before, after);
            }
            catch (Exception ex)
            {
                TaskOrphans.Finish(ctx, scan, ex);
                throw;
            }
            int cleaned = TaskOrphans.Finish(ctx, scan, null);
            return TaskOrphans.Note(ActionOk(kind, id, after.Code, action, message, after.Wf), cleaned);
        }

        // 预演（validate）：RefusePre、IsFlowEnabled2 都过了，在调 UFLTMService / 审批代理之前停下。
        // 窗口终点没记（不调 TaskOrphans.Mark），Finish 只放锁、不清理。
        static void DryStop(WorkContext ctx, string action, WfSnap before)
        {
            if (!DryRun.Active) return;
            Dictionary<string, object> wf = new Dictionary<string, object>();
            wf["action"] = action;
            wf["code"] = before.Code ?? "";
            wf["state_before"] = before.Wf;
            DryRun.Set("workflow", wf);
            bool ltm = action == "submit" || action == "withdraw";
            DryRun.Stop(ctx, ltm ? "UFLTMService" : "AuditServiceProxy");
        }

        public static ApiResult State(WorkContext ctx)
        {
            VoucherKind kind = QmKind(ctx);
            WfSnap snap = Need(ctx, kind, ctx.Item.Id);
            PermHook.Voucher(ctx, kind, ctx.Item.Id);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = ctx.Item.Id;
            body["code"] = snap.Code;
            body["wf"] = snap.Wf;
            return ApiResult.Ok(body);
        }

        public static ApiResult History(WorkContext ctx)
        {
            VoucherKind kind = QmKind(ctx);
            int id = ctx.Item.Id;
            WfSnap snap = Need(ctx, kind, id);
            PermHook.Voucher(ctx, kind, id);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = snap.Code;
            body["history"] = WfState.History(ctx.Conn, kind, id);
            return ApiResult.Ok(body);
        }

        public static ApiResult Tasks(WorkContext ctx)
        {
            if (Person(ctx).Length == 0)
            {
                return TaskBody(ctx, new List<Dictionary<string, object>>(), 0);
            }
            string filter = TaskFilter(ctx);
            int other;
            List<Dictionary<string, object>> tasks = WfState.Tasks(ctx.Conn, Person(ctx), filter, out other);
            PermHook.Tasks(ctx, tasks);
            return TaskBody(ctx, tasks, other);
        }

        static ApiResult TaskBody(WorkContext ctx, List<Dictionary<string, object>> tasks, int other)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["operator"] = ctx.Item.Operator ?? "";
            body["person"] = Person(ctx);
            body["tasks"] = tasks;
            body["other_count"] = other;
            return ApiResult.Ok(body);
        }

        static VoucherKind QmKind(WorkContext ctx)
        {
            VoucherKind kind = ctx.Item.Type;
            if (kind == null || !string.Equals(kind.Family, "qm", StringComparison.OrdinalIgnoreCase)
                || !kind.Workflow || kind.BizObjectId == null || kind.BizObjectId.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "该单据类型未接入审批流");
            }
            return kind;
        }

        static string TaskFilter(WorkContext ctx)
        {
            if (ctx.Item.Type == null) return "";
            return QmKind(ctx).BizObjectId;
        }

        static void RequireOpinion(WorkItem item, string action)
        {
            string text = item.Opinion == null ? "" : item.Opinion;
            if (text.Length > 500)
            {
                throw new BridgeException(400, "bad_request", "意见不能超过 500 字");
            }
            if (action != "disagree" && action != "return") return;
            if (text.Trim().Length == 0)
            {
                throw new BridgeException(400, "bad_request", "不同意和退回必须填写意见");
            }
        }

        static WfSnap Need(WorkContext ctx, VoucherKind kind, int id)
        {
            WfSnap snap = WfState.Load(ctx, ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return snap;
        }

        static void RefuseDisabled(WfSnap snap)
        {
            if (snap.Controlled) return;
            throw new BridgeException(409, "workflow_disabled", "单据未启用审批流");
        }

        static void RefusePre(WorkContext ctx, string action, WfSnap snap)
        {
            if (action == "submit")
            {
                RefuseSubmit(snap);
                return;
            }
            if (action == "withdraw")
            {
                RefuseWithdraw(snap);
                return;
            }
            if (action == "approve" || action == "disagree" || action == "return")
            {
                RefuseApprover(ctx, snap);
                return;
            }
            if (action == "abandon")
            {
                RefuseAbandon(ctx);
                return;
            }
            if (action == "resubmit")
            {
                RefuseResubmit(ctx, snap);
                return;
            }
            throw new BridgeException(400, "bad_request", "未知审批操作");
        }

        static void RefuseSubmit(WfSnap snap)
        {
            bool open = snap.VerifyNew == 0 || snap.VerifyNew == -1;
            if (open && !snap.Running) return;
            throw new BridgeException(409, "already_submitted", "单据已提交，不能重复提交");
        }

        static void RefuseWithdraw(WfSnap snap)
        {
            if (snap.Running && !AnyType(snap, 5)) return;
            throw new BridgeException(409, "not_submitted", "单据未在审批中");
        }

        static void RefuseApprover(WorkContext ctx, WfSnap snap)
        {
            if (CountMine(snap, Person(ctx), 1, 4) > 0) return;
            throw new BridgeException(409, "not_current_approver", "当前操作员没有待审任务");
        }

        static void RefuseAbandon(WorkContext ctx)
        {
            if (WfState.CanAbandon(ctx.Conn, ctx.Item.Type, ctx.Item.Id, Person(ctx))) return;
            throw new BridgeException(409, "not_current_approver", "只能弃审本人最近一次同意");
        }

        static void RefuseResubmit(WorkContext ctx, WfSnap snap)
        {
            if (CountMine(snap, Person(ctx), 5, 5) > 0) return;
            throw new BridgeException(409, "not_current_approver", "当前操作员没有待重新提交的任务");
        }

        static void RefusePost(WorkContext ctx, string action, WfSnap before, WfSnap after)
        {
            if (after != null && Passed(ctx, action, before, after)) return;
            throw new BridgeException(504, "outcome_unknown", "U8 返回成功但回读审批状态不符，结果未知；请先用 workflow/state 核对，不要直接重试");
        }

        static bool Passed(WorkContext ctx, string action, WfSnap before, WfSnap after)
        {
            string person = Person(ctx);
            if (action == "submit") return after.Running || after.VerifyNew == 2;
            if (action == "withdraw") return !after.Running && after.VerifyNew == 0;
            if (action == "approve" || action == "disagree" || action == "return")
            {
                return TaskGone(before, after, person);
            }
            if (action == "abandon") return after.AbandonCount > before.AbandonCount;
            if (action == "resubmit") return CountMine(after, person, 5, 5) == 0;
            return false;
        }

        // 至少有一条操作前就属于我的待办（按 task_id）已经不在待办里。不是要求全部消失。
        static bool TaskGone(WfSnap before, WfSnap after, string person)
        {
            if (before.Pending == null) return false;
            for (int i = 0; i < before.Pending.Count; i++)
            {
                Dictionary<string, object> row = before.Pending[i];
                if (!Mine(row, person, 1, 4)) continue;
                if (!FindTask(after.Pending, WfText.Cell(row, "task_id"))) return true;
            }
            return false;
        }

        static bool AnyType(WfSnap snap, int type)
        {
            for (int i = 0; i < snap.Pending.Count; i++)
            {
                if (WfText.Num(snap.Pending[i], "task_type") == type) return true;
            }
            return false;
        }

        static int CountMine(WfSnap snap, string person, int a, int b)
        {
            int n = 0;
            for (int i = 0; i < snap.Pending.Count; i++)
            {
                if (Mine(snap.Pending[i], person, a, b)) n++;
            }
            return n;
        }

        static bool Mine(Dictionary<string, object> row, string person, int a, int b)
        {
            if (!Same(WfText.Cell(row, "person"), person)) return false;
            int type = WfText.Num(row, "task_type");
            return type == a || type == b;
        }

        static bool FindTask(List<Dictionary<string, object>> pending, string taskId)
        {
            if (pending == null) return false;
            string want = Norm(taskId);
            for (int i = 0; i < pending.Count; i++)
            {
                if (Norm(WfText.Cell(pending[i], "task_id")) == want) return true;
            }
            return false;
        }

        static string Norm(string text)
        {
            if (text == null) return "";
            return text.Trim().TrimStart('{').TrimEnd('}').ToUpperInvariant();
        }

        static bool Same(string left, string right)
        {
            string a = left == null ? "" : left.Trim();
            string b = right == null ? "" : right.Trim();
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        static string Person(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null || ctx.Session.EmployeeId == null) return "";
            return ctx.Session.EmployeeId.Trim();
        }

        internal static string Rescue(WorkContext ctx, Exception ex)
        {
            if (ctx == null || ctx.WfBefore == null || ctx.Item == null || ctx.Item.Type == null)
            {
                throw WfProxy.UnknownOutcome();
            }
            WfSnap after = Fresh(ctx, ex);
            if (after == null)
            {
                WfProxy.Remember(ctx, ex);
                throw new BridgeException(504, "outcome_unknown", "U8 调用异常，回读审批状态既不是原状态也不是目标状态，结果未知；请先用 workflow/state 核对，不要直接重试");
            }
            if (Transitioned(ctx, ctx.Item.Action, ctx.WfBefore, after))
            {
                return WfProxy.NoteOf(ctx, ex);
            }
            if (SameSnap(ctx.WfBefore, after))
            {
                throw WfProxy.Rejected(ctx, ex);
            }
            WfProxy.Remember(ctx, ex);
            throw new BridgeException(504, "outcome_unknown", "U8 调用异常，回读审批状态既不是原状态也不是目标状态，结果未知；请先用 workflow/state 核对，不要直接重试");
        }

        static WfSnap Fresh(WorkContext ctx, Exception cause)
        {
            try
            {
                return WfState.LoadFresh(ctx, ctx.Item.Type, ctx.Item.Id);
            }
            catch (Exception)
            {
                WfProxy.Remember(ctx, cause);
                throw WfProxy.UnknownOutcome();
            }
        }

        internal static bool Transitioned(WorkContext ctx, string action, WfSnap before, WfSnap after)
        {
            if (before == null || after == null) return false;
            return Passed(ctx, action ?? "", before, after);
        }

        internal static bool SameSnap(WfSnap before, WfSnap after)
        {
            if (before == null || after == null) return false;
            if (before.Running != after.Running) return false;
            if (before.VerifyNew != after.VerifyNew) return false;
            if (before.VerifyState != after.VerifyState) return false;
            if (before.AbandonCount != after.AbandonCount) return false;
            if (before.ReturnCount != after.ReturnCount) return false;
            return SamePending(before.Pending, after.Pending);
        }

        static bool SamePending(List<Dictionary<string, object>> left, List<Dictionary<string, object>> right)
        {
            if (left == null || right == null) return left == right;
            if (left.Count != right.Count) return false;
            for (int i = 0; i < left.Count; i++)
            {
                if (!HasPending(right, left[i])) return false;
            }
            return true;
        }

        static bool HasPending(List<Dictionary<string, object>> pending, Dictionary<string, object> row)
        {
            string id = Norm(WfText.Cell(row, "task_id"));
            int type = WfText.Num(row, "task_type");
            for (int i = 0; i < pending.Count; i++)
            {
                if (Norm(WfText.Cell(pending[i], "task_id")) != id) continue;
                if (WfText.Num(pending[i], "task_type") == type) return true;
            }
            return false;
        }

        static ApiResult ActionOk(VoucherKind kind, int id, string code, string action, string message, Dictionary<string, object> wf)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = code ?? "";
            body["action"] = action;
            body["u8_message"] = message ?? "";
            body["wf"] = wf;
            return ApiResult.Ok(body);
        }
    }
}
