using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生产订单关闭 / 打开（vouchers/close，type=production_order，id=MoId，line_ids=MoDId）。
    // U8 没有关闭 / 打开的 U8API（IB_AppTag 只有新增、审核、弃审、读取、修改、删除），照 U8 界面调用
    // Usp_MO_Close / Usp_MO_UnClose：在请求连接的 CoTrans 里建 #tmp_procmodid、填行、执行存储过程、读回 ErrFlag / Errno。
    // 存储过程见到 @@TRANCOUNT>0 不自己开事务，失败时整笔回滚。见 docs/u8-notes.md「生产」一章（生产订单关闭、打开）。
    internal static partial class MoClose
    {
        public const string CloseRule = "write:production_order:close";
        public const string OpenRule = "write:production_order:open";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, string action, int[] lineIds)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
            }
            bool closing = WantClose(action);
            CheckLineIds(lineIds);
            string user = Operator(ctx);
            PermRule rule = PermRegistry.ForKey(closing ? CloseRule : OpenRule);
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, rule);
            MoCloseAsk ask = new MoCloseAsk();
            ask.Id = id;
            ask.Closing = closing;
            ask.LineIds = lineIds;
            ask.User = user;
            ask.Date = LoginDate(ctx);
            ask.Perm = perm;
            ask.Rule = rule;
            List<Dictionary<string, object>> after = Execute(ctx, ask);
            return Body(kind, id, action, ask.Chosen.Count, after);
        }

        static bool WantClose(string action)
        {
            if (action == "close")
            {
                return true;
            }
            if (action == "open")
            {
                return false;
            }
            throw new BridgeException(400, "bad_request", "action 必须是 close 或 open");
        }

        static void CheckLineIds(int[] lineIds)
        {
            if (lineIds != null && lineIds.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要关闭的明细");
            }
            if (lineIds != null && lineIds.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
        }

        // 存储过程把 @ModifyUser 拼进动态 SQL 的字符串常量（varchar(20)），含单引号会破坏语句，先拒绝。
        // U8 在 mom_orderdetail.CloseUser 里记操作员编码（与 RelsUser 相同），不是姓名。
        static string Operator(WorkContext ctx)
        {
            string user = ctx.Item.Operator == null ? "" : ctx.Item.Operator.Trim();
            if (user.Length == 0)
            {
                throw new BridgeException(500, "internal", "缺少操作员");
            }
            if (user.IndexOf('\'') >= 0 || user.Length > 20)
            {
                throw new BridgeException(400, "bad_request", "操作员编码不能含单引号，且不超过 20 个字符");
            }
            return user;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length < 10)
            {
                throw new BridgeException(400, "bad_request", "登录日期无效");
            }
            return date.Substring(0, 10);
        }

        static List<Dictionary<string, object>> Execute(WorkContext ctx, MoCloseAsk ask)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                List<Dictionary<string, object>> rows = LockRows(ctx.Conn, ask.Id);
                ask.Chosen = Pick(rows, ask.LineIds, ask.Closing);
                GateRows(ask);
                CallProc(ctx, ask);
                List<Dictionary<string, object>> after = Rows.Query(ctx.Conn, RowsSql, new object[] { ask.Id }, 5000);
                Confirm(rows, after, ask);
                DropTemp(ctx.Conn);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return after;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                DropQuietly(ctx.Conn);
                throw;
            }
        }

        static List<Dictionary<string, object>> LockRows(object conn, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, LockSql, new object[] { id }, 5000);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return rows;
        }

        // 不带 line_ids：取状态对的全部行（关闭取已审核 Status=3，打开取已关闭 Status=4），一行都没有就 409。
        static Dictionary<int, Dictionary<string, object>> Pick(List<Dictionary<string, object>> rows, int[] lineIds,
            bool closing)
        {
            Dictionary<int, Dictionary<string, object>> byId = ById(rows);
            Dictionary<int, Dictionary<string, object>> chosen = new Dictionary<int, Dictionary<string, object>>();
            if (lineIds == null)
            {
                foreach (KeyValuePair<int, Dictionary<string, object>> pair in byId)
                {
                    if (StatusOf(pair.Value) == (closing ? "3" : "4"))
                    {
                        chosen[pair.Key] = pair.Value;
                    }
                }
                if (chosen.Count == 0)
                {
                    throw WholeMismatch(rows, closing);
                }
                return chosen;
            }
            for (int i = 0; i < lineIds.Length; i++)
            {
                PickLine(byId, chosen, lineIds[i], closing);
            }
            return chosen;
        }

        static void PickLine(Dictionary<int, Dictionary<string, object>> byId,
            Dictionary<int, Dictionary<string, object>> chosen, int lineId, bool closing)
        {
            if (chosen.ContainsKey(lineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            Dictionary<string, object> row;
            if (lineId <= 0 || !byId.TryGetValue(lineId, out row))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            string status = StatusOf(row);
            if (closing && status != "3")
            {
                throw new BridgeException(409, "state_mismatch", status == "4" ? "单据已关闭" : "单据未审核");
            }
            if (!closing && status != "4")
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
            chosen[lineId] = row;
        }

        static BridgeException WholeMismatch(List<Dictionary<string, object>> rows, bool closing)
        {
            if (!closing)
            {
                return new BridgeException(409, "state_mismatch", "单据未关闭");
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (StatusOf(rows[i]) != "4")
                {
                    return new BridgeException(409, "state_mismatch", "单据未审核");
                }
            }
            return new BridgeException(409, "state_mismatch", "单据已关闭");
        }

        // 审批流控制的行不在这里关；选中的行都要在操作员的数据权限内（存货、部门、仓库；按查询权限判断）。
        static void GateRows(MoCloseAsk ask)
        {
            foreach (Dictionary<string, object> row in ask.Chosen.Values)
            {
                if (CoRows.Col(row, "wf") == "1")
                {
                    throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
                }
            }
            if (ask.Perm == null || ask.Perm.Supervisor || ask.Rule == null)
            {
                return;
            }
            foreach (Dictionary<string, object> row in ask.Chosen.Values)
            {
                for (int i = 0; i < ask.Rule.Objs.Length; i++)
                {
                    PermObj o = ask.Rule.Objs[i];
                    if (ask.Perm.Controls(o.Obj) && !PermCheck.RowOk(ask.Perm, o, row))
                    {
                        throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                    }
                }
            }
        }

        // 选中的行到了目标状态，其余行不变；否则回滚。
        static void Confirm(List<Dictionary<string, object>> before, List<Dictionary<string, object>> after,
            MoCloseAsk ask)
        {
            Dictionary<int, Dictionary<string, object>> was = ById(before);
            Dictionary<int, Dictionary<string, object>> now = ById(after);
            string target = ask.Closing ? "4" : "3";
            foreach (KeyValuePair<int, Dictionary<string, object>> pair in was)
            {
                Dictionary<string, object> row;
                if (!now.TryGetValue(pair.Key, out row))
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                string expect = ask.Chosen.ContainsKey(pair.Key) ? target : StatusOf(pair.Value);
                if (StatusOf(row) != expect)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
            }
        }

        static Dictionary<int, Dictionary<string, object>> ById(List<Dictionary<string, object>> rows)
        {
            Dictionary<int, Dictionary<string, object>> map = new Dictionary<int, Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], "MoDId"));
                if (lineId > 0)
                {
                    map[lineId] = rows[i];
                }
            }
            return map;
        }

        static string StatusOf(Dictionary<string, object> row)
        {
            return CoRows.Col(row, "Status").Trim();
        }

        sealed class MoCloseAsk
        {
            internal int Id;
            internal bool Closing;
            internal int[] LineIds;
            internal string User;
            internal string Date;
            internal PermContext Perm;
            internal PermRule Rule;
            internal Dictionary<int, Dictionary<string, object>> Chosen;
        }
    }
}
