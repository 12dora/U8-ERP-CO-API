using System;
using System.Collections.Generic;

namespace U8Co
{
    // 货位调整单新增、修改的提交前回读（StockAt.Before / After，在请求连接的事务里）。Insert("19") 的 DOM 写法是
    // 推断的，所以保存后、提交前核对：单据能读到（新增读 Insert 回的 vouchid，读不到按单号找；修改按原主键）、表头仓库是请求的仓库、
    // 表体行数和逐行（不看行号，按行配对）的存货、调出货位、调入货位、批号、自由项、数量都与请求（修改为改完的整张单据）一致、还没有货位台账
    // （U8 审核时才写）、涉及的货位结存没有变。任何一项不符就抛出、整笔回滚（409 u8_rejected），不留下与请求不一致的单据。
    internal sealed class PositionAdjustSaved
    {
        const string HeadSql = "select convert(varchar(20), Id) as id, cWhCode as wh from AdjustPVouch where Id=?";
        const string CodeSql = "select convert(varchar(20), Id) as id from AdjustPVouch where cVouchCode=?";

        readonly WorkContext ctx;
        readonly StockAt at;
        readonly string wh;
        readonly string code;
        readonly List<BinLine> want;
        // 修改时的原主键；新增为 0（主键取自 Insert 的返回）。
        readonly int known;
        Dictionary<string, decimal> before = new Dictionary<string, decimal>();

        internal PositionAdjustSaved(WorkContext ctx, StockAt at, string wh, string code, List<BinLine> want)
            : this(ctx, at, wh, code, want, 0)
        {
        }

        internal PositionAdjustSaved(WorkContext ctx, StockAt at, string wh, string code, List<BinLine> want, int known)
        {
            this.ctx = ctx;
            this.at = at;
            this.wh = wh;
            this.code = code;
            this.want = want;
            this.known = known;
        }

        public void Before(object conn)
        {
            List<string> keys = new List<string>(PositionAdjustBins.Deltas(want, false).Keys);
            // 修改：改前的行涉及的结存键也要在，改掉的行不能让结存动。
            if (known > 0)
            {
                foreach (BinLine old in PositionAdjustBins.FromDb(conn, known))
                {
                    keys.Add(PositionAdjustBins.Key(old, old.From));
                    keys.Add(PositionAdjustBins.Key(old, old.To));
                }
            }
            before = PositionAdjustBins.Snapshot(conn, wh, keys);
        }

        public void After(object conn)
        {
            int id = SavedId(conn);
            CompareLines(PositionAdjustBins.FromDb(conn, id));
            if (PositionAdjustBins.LedgerCount(conn, id) != 0)
            {
                throw Mismatch("保存后货位台账已有本单的记录（应在审核时才写）");
            }
            Dictionary<string, decimal> after = PositionAdjustBins.Snapshot(conn, wh, before.Keys);
            foreach (KeyValuePair<string, decimal> kv in before)
            {
                if (Math.Abs(after[kv.Key] - kv.Value) > 0.000001m)
                {
                    string[] part = kv.Key.Split(PositionAdjustBins.Sep);
                    throw Mismatch("保存后货位 " + part[0] + " 存货 " + part[1] + " 的结存从 " + StockUnits.Price(kv.Value)
                        + " 变成 " + StockUnits.Price(after[kv.Key]));
                }
            }
        }

        // 新增：Insert 引用槽 6 回的新主键，读不到时按单号找（同 StockCo.Inserted 的退路）；修改：原主键。
        int SavedId(object conn)
        {
            int id = known > 0 ? known : StockCall.NewId(ctx, at.Args[6]);
            Dictionary<string, object> head = id > 0 ? Rows.One(conn, HeadSql, new object[] { id }) : null;
            if (head == null && known == 0)
            {
                Dictionary<string, object> byCode = Rows.One(conn, CodeSql, new object[] { code });
                id = byCode == null ? 0 : CoRows.AsId(CoRows.Col(byCode, "id"));
                head = id > 0 ? Rows.One(conn, HeadSql, new object[] { id }) : null;
            }
            if (head == null)
            {
                throw Mismatch("保存后读不到单据（单号 " + code + "）");
            }
            if (!Same(StockMsg.Col(head, "wh"), wh))
            {
                throw Mismatch("保存后表头仓库是 " + StockMsg.Col(head, "wh") + "，请求是 " + wh);
            }
            return id;
        }

        // 行数相同，且库里每一行都能在请求的行里找到一个还没配过的相同行（不依赖行号：U8 可能重排或不写新行的行号）。
        void CompareLines(List<BinLine> saved)
        {
            if (saved.Count != want.Count)
            {
                throw Mismatch("保存后表体有 " + saved.Count + " 行，请求是 " + want.Count + " 行");
            }
            bool[] used = new bool[want.Count];
            for (int i = 0; i < saved.Count; i++)
            {
                int hit = Match(saved[i], used);
                if (hit < 0)
                {
                    throw Mismatch("保存后第 " + (i + 1) + " 行（" + saved[i].Inv + " " + saved[i].From + "→" + saved[i].To + " "
                        + StockUnits.Price(saved[i].Qty) + "）与请求不一致");
                }
                used[hit] = true;
            }
        }

        int Match(BinLine line, bool[] used)
        {
            for (int j = 0; j < want.Count; j++)
            {
                if (!used[j] && SameLine(line, want[j]))
                {
                    return j;
                }
            }
            return -1;
        }

        // 存货、调出货位、调入货位、批号、自由项 1–10、数量（数量差 1e-6 以内算相同）。
        internal static bool SameLine(BinLine a, BinLine b)
        {
            if (!(Same(a.Inv, b.Inv) && Same(a.From, b.From) && Same(a.To, b.To) && Same(a.Batch, b.Batch)
                && Math.Abs(a.Qty - b.Qty) <= 0.000001m))
            {
                return false;
            }
            for (int i = 0; i < a.Free.Length; i++)
            {
                if (!Same(a.Free[i], b.Free[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static BridgeException Mismatch(string text)
        {
            return new BridgeException(409, "u8_rejected", text + "，已回滚");
        }
    }
}
