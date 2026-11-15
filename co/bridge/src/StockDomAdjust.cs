using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 货位调整单（19）的字段白名单和新增模板。空模板同形态转换单：视图 AdjustPM / AdjustPD where 1=2（MiscBlank）。
    // 表头只有一个仓库（同一仓库内调货位）；每行调出货位 cbposcode、调入货位 caposcode、数量 iquantity。
    // 批号、自由项照写，货位档案和结存的核对在 PositionAdjustBins。件数按换算率补（StockUnits），不收调用方的 inum。
    internal static partial class StockDom
    {
        static readonly HashSet<string> PaHeadNames = Names("cwhcode", "ddate", "cdepcode", "cpersoncode", "cmemo");
        static readonly HashSet<string> PaBodyNames = Names("cinvcode", "cbposcode", "caposcode", "iquantity", "cassunit",
            "cbatch", "cbmemo");

        // vt_id 取卡片 0313 的缺省显示模板 113。
        const string AdjustVt = "113";

        static bool AdjustField(bool head, string low)
        {
            if ((head ? PaHeadNames : PaBodyNames).Contains(low))
            {
                return true;
            }
            if (head)
            {
                return Span(low, "cdefine", 1, 16);
            }
            return Span(low, "cfree", 1, 10) || Span(low, "cdefine", 22, 37);
        }

        internal static IEnumerable<string> AdjustMetaNames()
        {
            List<string> all = new List<string>();
            all.AddRange(PaHeadNames);
            all.AddRange(PaBodyNames);
            return all;
        }

        internal static object AdjustHead(object conn, VoucherKind kind, Dictionary<string, object> fields,
            string maker, string billDate)
        {
            if (fields == null)
            {
                throw BridgeException.BadField("head", "缺少表头");
            }
            object dom = MiscBlank(conn, kind, true);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                FillAdjust(kind, names, row, maker);
                Overlay(names, row, fields, kind, "head");
                FillAdjust(kind, names, row, maker);
                PutMissing(names, row, "dDate", billDate ?? "");
                if (CellOf(row, "dDate").Length == 0)
                {
                    throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
                }
                if (CellOf(row, "cWhCode").Length == 0)
                {
                    throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
                }
                Stamp(dom, DomRows.AddRow(dom), row);
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static void FillAdjust(VoucherKind kind, List<string> names, Dictionary<string, string> row, string maker)
        {
            Put(names, row, "editprop", "A");
            Put(names, row, "vt_id", AdjustVt);
            Put(names, row, "iverifystate", "0");
            Put(names, row, "iswfcontrolled", "0");
            Put(names, row, "cmaker", maker ?? "");
            Put(names, row, "id", "");
            Put(names, row, kind.CodeColumn, "");
        }

        // rows：按请求顺序写进 DOM 的行（列名按模板大小写），交给 PositionAdjustBins 核对货位和结存。
        internal static object AdjustBody(object conn, VoucherKind kind, object[] lines,
            out List<Dictionary<string, string>> rows)
        {
            RequireLines(lines);
            rows = new List<Dictionary<string, string>>();
            object dom = MiscBlank(conn, kind, false);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                for (int i = 0; i < lines.Length; i++)
                {
                    rows.Add(AdjustLine(conn, names, kind, lines[i], i + 1));
                }
                for (int i = 0; i < rows.Count; i++)
                {
                    Stamp(dom, DomRows.AddRow(dom), rows[i]);
                }
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static Dictionary<string, string> AdjustLine(object conn, List<string> names, VoucherKind kind,
            object raw, int rowNo)
        {
            string at = FieldPath.Item("lines", rowNo - 1);
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField(at, "表体行不是对象");
            }
            Dictionary<string, string> row = RowMap();
            Put(names, row, "editprop", "A");
            Put(names, row, "autoid", "");
            Put(names, row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            Overlay(names, row, line, kind, at);
            Put(names, row, "editprop", "A");
            RequireAdjustCells(row, at);
            MiscQtyOk(CellOf(row, "iQuantity"), false);
            StockUnits.Apply(conn, names, row, "iQuantity", "iNum", true);
            return row;
        }

        static void RequireAdjustCells(Dictionary<string, string> row, string at)
        {
            if (CellOf(row, "cInvCode").Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cinvcode"), "表体行缺少存货");
            }
            string from = CellOf(row, "cBPosCode");
            string to = CellOf(row, "cAPosCode");
            if (from.Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbposcode"), "表体行缺少调出货位 cbposcode");
            }
            if (to.Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "caposcode"), "表体行缺少调入货位 caposcode");
            }
            if (string.Equals(from, to, StringComparison.OrdinalIgnoreCase))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "caposcode"), "调出货位和调入货位不能相同");
            }
        }
    }
}
