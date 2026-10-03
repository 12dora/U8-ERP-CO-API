using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售出库参照发货单生成（整单或按行部分，u8-notes §6）。只用实测过的操作，分两步、不是一个事务：
    // 1. 先读发货行 iQuantity / fOutQuantity 作基线，MakeOutVouch 整单生成：在 RunAt 的桥事务里调用（生产审计 @@TRANCOUNT
    //    调用后仍为 1，U8 不自行提交），由 RunAt 的 CommitSeen 提交。
    // 2. 按发货行比较 U8 生成的数量（rdrecords32）与应出数量：带 lines 为请求数量，不带为 iQuantity − 基线 fOutQuantity。
    //    一致就照旧返回；多出的（部分出库，或先前部分出库后 U8 按 bOverDispOut 把原剩余整份再出）在新事务里改回（OutPart）。
    // 改回失败时删掉第 1 步生成的全部出库单补偿，核对 fOutQuantity 回到基线后 409；补偿也失败则 504 并列出生成的 id。
    internal static partial class StockGen
    {
        const string OpenLinesSql = "select convert(varchar(20), iDLsID) as line, convert(varchar(40), isnull(iQuantity,0)) as qty,"
            + " convert(varchar(40), isnull(fOutQuantity,0)) as outq,"
            + " case when isnull(cSCloser,'')='' then '0' else '1' end as closed from DispatchLists where DLID=?";
        const string MadeSql = "select convert(varchar(20), iDLsID) as line, convert(varchar(40), sum(isnull(iQuantity,0))) as qty"
            + " from rdrecords32 where ID=? group by iDLsID";

        // lines 为空时整单生成：应出数量取每行剩余。
        public static ApiResult SaleOutLines(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            RefuseHead(head);
            RefuseDispatch(ctx.Conn, sourceId);
            OutPart job = new OutPart();
            job.Ctx = ctx;
            job.Kind = kind;
            job.DlId = sourceId;
            job.Whole = lines == null || lines.Length == 0;
            job.Before = OpenLines(ctx.Conn, sourceId);
            // 行可带批号 cbatch、货位 cposition，同一发货行可拆成多次（StockGenSaleBatch*），预检在生成之前。
            OutAsk ask = job.Whole ? null : ParseOut(job.Before, lines);
            job.Want = job.Whole ? Remaining(job.Before) : ask.Want;
            if (ask != null && ask.Any)
            {
                CheckOutStock(ctx.Conn, sourceId, ask);
                job.Batch = new OutBatch();
                job.Batch.Ask = ask;
            }
            job.Floor = MaxOut(ctx.Conn, kind, sourceId);
            // 预演（回滚模式）只到第 1 步：MakeOutVouch 之后的第一次 CommitSeen 就回滚，第 2、3 步不执行。
            if (!job.Whole)
            {
                DryRun.Set("not_previewed", "按行改回数量、批号货位（MakeOutVouch 整单生成之后的第 2、3 步）");
            }
            job.Made = MakeAll(ctx, kind, sourceId, job.Floor);
            List<int> kept = job.Settle();
            if (kept.Count == 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有生成可保留的销售出库单");
            }
            ApiResult saved = AfterSaved(ctx, kind, kept[0], "", Kinds.Find("dispatch"), sourceId);
            if (kept.Count > 1)
            {
                saved.Body["ids"] = CopyIds(kept);
            }
            return saved;
        }

        // MakeOutVouch 整单生成并提交（原有行为）。返回比调用前 max(ID) 更大的新出库单 id，升序。
        static List<int> MakeAll(WorkContext ctx, VoucherKind kind, int sourceId, int floor)
        {
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                StockCall.RunMake(ctx, co, sourceId, ctx.Conn, msg, DrySaleOut(kind, sourceId, floor));
                List<int> ids = Newer(ctx, kind, sourceId, floor);
                if (ids.Count == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有生成销售出库单");
                }
                return ids;
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        // 发货行主键 → {数量, 累计出库, 已关闭(1/0)}。
        static Dictionary<int, decimal[]> OpenLines(object conn, int dlid)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, OpenLinesSql, new object[] { dlid }, 5000);
            Dictionary<int, decimal[]> map = new Dictionary<int, decimal[]>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                map[CoRows.AsId(CoRows.Col(rows[i], "line"))] = new decimal[]
                {
                    DecOf(CoRows.Col(rows[i], "qty")), DecOf(CoRows.Col(rows[i], "outq")),
                    CoRows.Col(rows[i], "closed") == "1" ? 1m : 0m
                };
            }
            return map;
        }

        // 整单：每行 iQuantity − fOutQuantity，不大于 0 的行和已关闭的行不出。
        static Dictionary<int, decimal> Remaining(Dictionary<int, decimal[]> open)
        {
            Dictionary<int, decimal> want = new Dictionary<int, decimal>();
            foreach (KeyValuePair<int, decimal[]> kv in open)
            {
                decimal rest = kv.Value[0] - kv.Value[1];
                if (rest > 0m && kv.Value[2] == 0m)
                {
                    want[kv.Key] = rest;
                }
            }
            return want;
        }

        // 带 lines 时的解析与校验（source_line_id、quantity，另可带 cbatch、cposition、拆行）在 StockGenSaleBatch.ParseOut。

        // 新出库单按发货行汇总的数量（请求连接，提交后读）。
        static Dictionary<int, decimal> MadeOf(object conn, List<int> ids)
        {
            Dictionary<int, decimal> made = new Dictionary<int, decimal>();
            for (int i = 0; i < ids.Count; i++)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, MadeSql, new object[] { ids[i] }, 5000);
                for (int j = 0; rows != null && j < rows.Count; j++)
                {
                    int line = CoRows.AsId(CoRows.Col(rows[j], "line"));
                    decimal have;
                    made.TryGetValue(line, out have);
                    made[line] = have + DecOf(CoRows.Col(rows[j], "qty"));
                }
            }
            return made;
        }

        static decimal DecOf(string text)
        {
            decimal value;
            return StockUnits.Dec(text, out value) ? value : 0m;
        }

        static string IdList(List<int> ids)
        {
            List<string> parts = new List<string>();
            for (int i = 0; ids != null && i < ids.Count; i++)
            {
                parts.Add(ids[i].ToString(CultureInfo.InvariantCulture));
            }
            return string.Join(",", parts.ToArray());
        }

        // 自己开的事务：work 在事务里执行，成功 CommitSeen，异常回滚后原样抛出。
        static void InTx(WorkContext ctx, StockCheck work)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                work(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                if (open)
                {
                    try
                    {
                        CoTrans.Rollback(conn);
                    }
                    catch (Exception)
                    {
                    }
                }
                throw;
            }
        }

        // 事务里直接调 CO（Update、Delete），不再开事务；失败照 RunAt 的口径抛出，由外层回滚。
        static void CallIn(WorkContext ctx, object co, StockAt at)
        {
            object msg0 = at.Args[at.MsgAt];
            try
            {
                object ret = ComUtil.CallRef(co, at.Method, at.Args, at.Refs);
                string err = Values.Text(at.Args[at.ErrAt]).Trim();
                object use = at.Args[at.MsgAt] == null ? msg0 : at.Args[at.MsgAt];
                if (!Values.Flag(ret))
                {
                    StockCall.Fail(ctx, err, StockMsg.Shortage(use), use);
                }
                if (err.Length > 0)
                {
                    CoRows.Note(ctx.Item, at.Method + " " + err);
                }
            }
            finally
            {
                StockCall.ReleaseMsg(at.Args, at.MsgAt, msg0);
                StockCall.SeenConn(ctx, ctx.Conn, at.Args, at.ConnAt);
            }
        }

        // 事务里删一张出库单，走普通删除的口径（StockCo.Delete）：9 个参数的 Delete；有货位记录时先 ClearPosition、
        // bList=true，没有时 bList=false（StockPosGuard.ClearOnDelete 的 Before / After）。
        static void DropIn(WorkContext ctx, object co, VoucherKind kind, int id)
        {
            object msg = Rows.NewDom();
            try
            {
                string ufts = StockCall.Ufts(ctx.Conn, kind, id);
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockAt at = StockCall.AtFor("Delete", args, refs, ufts);
                StockPosGuard.ClearOnDelete(ctx, co, at, kind, id);
                at.Before(ctx.Conn);
                CallIn(ctx, co, at);
                at.After(ctx.Conn);
            }
            finally
            {
                ComUtil.Final(msg);
            }
        }

        static void ReleaseAll(List<object> nodes)
        {
            for (int i = 0; nodes != null && i < nodes.Count; i++)
            {
                ComUtil.ReleaseOne(nodes[i]);
            }
        }
    }
}
