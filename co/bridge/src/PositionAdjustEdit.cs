using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 货位调整单修改的核对（StockEdit.MiscSave 在 Update 之前调 Guard，这时请求的增改删已经叠在 Load 回来的表头表体上）。
    // 按改完的整张单据做与新增相同的闸：仓库、每行存货、调出 / 调入货位（不同、都是本仓库的末级货位）、数量、存货管理方式、
    // 调出货位结存（PositionAdjustBins.CheckNew），都在 Update 之前；再挂上 Update 前后的核对（PositionAdjustSaved）：
    // 保存后回读整张单据与改完的一致、没有货位台账、涉及的货位结存没变，不符回滚 409。
    // 400 里 lines.N 的 N 是改完之后第 N+1 行（按行号排序），不是请求 lines 的下标。
    internal static class PositionAdjustEdit
    {
        static readonly string[] Cols = LineCols();

        internal static void Guard(WorkContext ctx, VoucherKind kind, int id, StockLoaded loaded, StockAt at)
        {
            if (!PositionAdjust.Handles(kind))
            {
                return;
            }
            string wh;
            string code;
            HeadOf(loaded.Head, out wh, out code);
            List<Dictionary<string, string>> rows = LinesOf(loaded.Body);
            Require(wh, rows);
            List<BinLine> want = PositionAdjustBins.FromDom(rows);
            PositionAdjustBins.CheckNew(ctx.Conn, wh, want);
            PositionAdjustSaved saved = new PositionAdjustSaved(ctx, null, wh, code, want, id);
            at.Before = saved.Before;
            at.After = saved.After;
        }

        static void HeadOf(object domH, out string wh, out string code)
        {
            List<object> heads = DomRows.RowsOf(domH);
            try
            {
                if (heads.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                wh = DomRows.Get(heads[0], "cWhCode").Trim();
                code = DomRows.Get(heads[0], "cVouchCode").Trim();
            }
            finally
            {
                Release(heads);
            }
        }

        static void Require(string wh, List<Dictionary<string, string>> rows)
        {
            if (wh.Length == 0)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            if (rows.Count == 0)
            {
                throw BridgeException.BadField("lines", "修改后没有表体行");
            }
            for (int i = 0; i < rows.Count; i++)
            {
                StockDom.RequireAdjustCells(rows[i], FieldPath.Item("lines", i));
                StockDom.MiscQtyOk(rows[i]["iQuantity"], false);
            }
        }

        // 改完的表体行（没标 D 的），按行号排序，与库里 order by irowno, autoID 的回读对得上。
        static List<Dictionary<string, string>> LinesOf(object domB)
        {
            List<object> nodes = DomRows.RowsOf(domB);
            try
            {
                List<Dictionary<string, string>> rows = new List<Dictionary<string, string>>();
                for (int i = 0; i < nodes.Count; i++)
                {
                    if (DomRows.Get(nodes[i], "editprop").Trim() != "D")
                    {
                        rows.Add(Cells(nodes[i], i));
                    }
                }
                rows.Sort(ByRowNo);
                return rows;
            }
            finally
            {
                Release(nodes);
            }
        }

        static Dictionary<string, string> Cells(object node, int seq)
        {
            Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < Cols.Length; i++)
            {
                row[Cols[i]] = DomRows.Get(node, Cols[i]).Trim();
            }
            row["irowno"] = DomRows.Get(node, "irowno").Trim();
            row["seq"] = seq.ToString(CultureInfo.InvariantCulture);
            return row;
        }

        static int ByRowNo(Dictionary<string, string> a, Dictionary<string, string> b)
        {
            int byNo = Num(a["irowno"]).CompareTo(Num(b["irowno"]));
            return byNo != 0 ? byNo : Num(a["seq"]).CompareTo(Num(b["seq"]));
        }

        static int Num(string text)
        {
            int n;
            return int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? n : int.MaxValue;
        }

        // PositionAdjustBins.FromDom 读的列：存货、调出货位、调入货位、数量、批号、自由项 1–10。
        static string[] LineCols()
        {
            List<string> cols = new List<string>(new string[] { "cInvCode", "cBPosCode", "cAPosCode", "iQuantity", "cBatch" });
            for (int i = 1; i <= 10; i++)
            {
                cols.Add("cFree" + i.ToString(CultureInfo.InvariantCulture));
            }
            return cols.ToArray();
        }

        static void Release(List<object> nodes)
        {
            for (int i = 0; i < nodes.Count; i++)
            {
                ComUtil.ReleaseOne(nodes[i]);
            }
        }
    }
}
