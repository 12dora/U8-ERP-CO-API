using System.Collections.Generic;

namespace U8Co
{
    // 货位调整单（position_adjust，U8 单据类型 19，卡片 0313，表 AdjustPVouch / AdjustPVouchs）的分派。
    // 读取、列表都是 SQL（Load、ListKindsStMisc）；新增、审核、弃审、删除走 USERPCO.VoucherCO（StockCo.Adjust*），
    // 只对测试账套开放，测试账套整轮实测通过后升为第一级写入（与其他库存单据一样受写入策略约束）；
    // 货位、存货、结存与审核后台账的核对都保留（PositionAdjustBins、PositionAdjustInv、PositionAdjustCheck）。
    // 不开修改（行就是货位移动本身，删除后重新录入）。Dispatch.Handle 先问这里，返回 null 再走原来的路由表。
    internal static class PositionAdjust
    {
        internal const string KindName = "position_adjust";
        internal const string StType = "19";
        internal const string NoUpdateText = "货位调整单不能修改，请删除后重新录入";
        const string V = "/u8co/v1/vouchers/";

        internal static bool Handles(VoucherKind kind)
        {
            return kind != null && kind.Name == KindName;
        }

        public static ApiResult Try(WorkContext ctx, string path)
        {
            if (ctx == null || ctx.Item == null || !Handles(ctx.Item.Type))
            {
                return null;
            }
            string op = OpOf(path);
            if (op == null)
            {
                return null;
            }
            WorkItem item = ctx.Item;
            RequireAuth(ctx, op, item.Action);
            if (op == "create")
            {
                return PuAppRoutes.StampNewId(ctx, StockCo.AdjustCreate(ctx, item.Type, item.Head, item.Lines));
            }
            if (op == "delete")
            {
                return StockCo.AdjustDelete(ctx, item.Type, item.Id);
            }
            return StockCo.AdjustVerify(ctx, item.Type, item.Id, item.Action);
        }

        // 登录前（Requests.GuardItem）：修改 400。
        internal static void PreLogin(WorkItem item, string path)
        {
            if (item == null || !Handles(item.Type))
            {
                return;
            }
            if (path == V + "update")
            {
                throw new BridgeException(400, "bad_request", NoUpdateText);
            }
        }

        // 功能权限（U8 授权目录 UA_Auth）：录入 ST010806（删除没有单独的 id，同录入）、审核 ST010802、弃审 ST010803。
        // 写路由每次现读权限（PermCheck.Of），任何 COM 调用之前。
        static void RequireAuth(WorkContext ctx, string op, string action)
        {
            if (op != "verify")
            {
                PermCheck.Require(ctx, "ST010806", op == "create" ? "货位调整单录入" : "货位调整单删除");
                return;
            }
            if (action == "unverify")
            {
                PermCheck.Require(ctx, "ST010803", "货位调整单弃审");
                return;
            }
            PermCheck.Require(ctx, "ST010802", "货位调整单审核");
        }

        static string OpOf(string path)
        {
            if (path == V + "create" || path == V + "delete" || path == V + "verify")
            {
                return path.Substring(V.Length);
            }
            return null;
        }

        // 读取：表头、表体照表（表体按行号），另带本单的货位台账 positions（审核后每行一出一入，未审核为空；
        // 超过 PositionAdjustBins.LedgerCap 行时截断并给 positions_truncated）。
        // 只用 ctx.Conn（读线程池，RouteClass 已登记）。
        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> head = Rows.One(ctx.Conn, "select * from AdjustPVouch where Id=?", new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn,
                "select * from AdjustPVouchs where ID=? order by irowno, autoID", new object[] { id }, 501);
            ApiResult result = StockMsg.Loaded(kind, id, head, lines ?? new List<Dictionary<string, object>>());
            PositionAdjustBins.PutLedger(ctx.Conn, id, result.Body);
            return result;
        }
    }

    // 审核、弃审的提交前核对（StockAt.Before / After，都在请求连接的事务里）：Before 读表体、按净变动查结存、记下涉及的结存；
    // After 核对审核人（审核后是登录操作员、有审核日期，弃审后都为空）、货位台账行数（审核 2×行数，弃审 0）和每个结存键的
    // 变动等于调整单，不符就抛出、整笔回滚。响应的 ledger_rows、bin_moves 由 Describe 写入。
    internal sealed class PositionAdjustCheck
    {
        readonly int id;
        readonly bool undo;
        readonly string operatorName;
        string wh = "";
        List<BinLine> lines = new List<BinLine>();
        Dictionary<string, decimal> before = new Dictionary<string, decimal>();
        Dictionary<string, decimal> after = new Dictionary<string, decimal>();
        int ledger;

        internal PositionAdjustCheck(int id, bool undo, string operatorName)
        {
            this.id = id;
            this.undo = undo;
            this.operatorName = operatorName == null ? "" : operatorName.Trim();
        }

        public void Before(object conn)
        {
            wh = (Rows.Scalar(conn, "select cWhCode from AdjustPVouch where Id=?", new object[] { id }) ?? "").Trim();
            lines = PositionAdjustBins.FromDb(conn, id);
            if (lines.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据没有表体行");
            }
            PositionAdjustBins.RequireStock(conn, wh, lines, undo);
            before = PositionAdjustBins.Snapshot(conn, wh, PositionAdjustBins.Deltas(lines, undo).Keys);
        }

        public void After(object conn)
        {
            CheckHead(conn);
            ledger = PositionAdjustBins.LedgerCount(conn, id);
            int want = undo ? 0 : 2 * lines.Count;
            if (ledger != want)
            {
                throw new BridgeException(409, "u8_rejected", (undo ? "弃审后" : "审核后") + "货位台账有 " + ledger
                    + " 行，应为 " + want + " 行");
            }
            after = PositionAdjustBins.Snapshot(conn, wh, before.Keys);
            foreach (KeyValuePair<string, decimal> kv in PositionAdjustBins.Deltas(lines, undo))
            {
                decimal moved = after[kv.Key] - before[kv.Key];
                if (System.Math.Abs(moved - kv.Value) > 0.000001m)
                {
                    string[] part = kv.Key.Split(PositionAdjustBins.Sep);
                    throw new BridgeException(409, "u8_rejected", "货位 " + part[0] + " 存货 " + part[1] + " 的结存变动 "
                        + StockUnits.Price(moved) + "，与调整单的 " + StockUnits.Price(kv.Value) + " 不符");
                }
            }
        }

        // 同 StockCo.RefuseAfter，但在提交前、本连接上读，不符时整笔回滚（提交后在新连接上还会再核一次）。
        void CheckHead(object conn)
        {
            Dictionary<string, object> head = Rows.One(conn, "select chandler, dVeriDate from AdjustPVouch where Id=?",
                new object[] { id });
            string verifier = StockMsg.Col(head, "chandler");
            string date = StockMsg.Col(head, "dVeriDate");
            if (undo && (verifier.Length > 0 || date.Length > 0))
            {
                throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
            }
            if (!undo && (verifier.Length == 0 || date.Length == 0))
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            if (!undo && !string.Equals(verifier, operatorName, System.StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        // 响应另加：ledger_rows（核对后的货位台账行数）、bin_moves（每个结存键的前后结存）。
        public void Describe(Dictionary<string, object> body)
        {
            List<object> moves = new List<object>();
            foreach (KeyValuePair<string, decimal> kv in before)
            {
                string[] part = kv.Key.Split(PositionAdjustBins.Sep);
                Dictionary<string, object> move = new Dictionary<string, object>();
                move["wh"] = wh;
                move["pos"] = part[0];
                move["inv"] = part[1];
                move["batch"] = part[2];
                move["before"] = StockUnits.Price(kv.Value);
                decimal now;
                move["after"] = after.TryGetValue(kv.Key, out now) ? StockUnits.Price(now) : "";
                moves.Add(move);
            }
            body["ledger_rows"] = ledger;
            body["bin_moves"] = moves;
        }
    }
}
