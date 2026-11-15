using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 生产订单修改的库读取：调用前在请求连接上读快照（行、子件、账套小数位）并过闸门，调用后在新连接上用同一套 SQL 回读。
    // 表名、列名都是固定的 U8 表；调用方的值只进 ADO 参数。开工、完工日期在 mom_morder 上，不在 mom_orderdetail。
    internal static class MoUpdateSql
    {
        const int RowsMax = 5000;
        const string LinesSql = "select o.MoCode, convert(varchar(20), d.MoDId) as MoDId, convert(varchar(10), d.SortSeq) as SortSeq,"
            + " d.InvCode, convert(varchar(40), d.Qty) as Qty, convert(varchar(40), d.MrpQty) as MrpQty,"
            + " convert(varchar(10), d.MoClass) as MoClass, d.AuxUnitCode, convert(varchar(40), d.ChangeRate) as ChangeRate,"
            + " convert(varchar(40), d.AuxQty) as AuxQty,"
            + " convert(varchar(10), d.Status) as Status, convert(varchar(10), isnull(d.IsWFControlled,0)) as wf,"
            + " convert(varchar(10), isnull(d.CollectiveFlag,0)) as cf, convert(varchar(40), isnull(d.DeclaredQty,0)) as declared,"
            + " d.Remark, d.MDeptCode, d.WhCode, d.RelsUser, convert(varchar(10), d.RelsDate, 23) as RelsDate,"
            + " convert(varchar(10), m.StartDate, 23) as StartDate, convert(varchar(10), m.DueDate, 23) as DueDate,"
            + " d.Define22, d.Define23, d.Define24, d.Define25, d.Define28, d.Define29, d.Define30, d.Define31, d.Define32, d.Define33"
            + " from mom_order o join mom_orderdetail d on d.MoId=o.MoId left join mom_morder m on m.MoDId=d.MoDId"
            + " where o.MoId=? order by d.SortSeq, d.MoDId";
        // 子件：主键、定位、数量类，加 MoUpdateCols 名单里要守住的列和替代料行数。
        static readonly string AllocSql = "select convert(varchar(20), a.AllocateId) as AllocateId,"
            + " convert(varchar(20), a.MoDId) as MoDId, convert(varchar(10), a.SortSeq) as SortSeq, a.InvCode,"
            + " convert(varchar(40), a.Qty) as Qty, convert(varchar(40), a.AuxQty) as AuxQty,"
            + " convert(varchar(40), isnull(a.IssQty,0)) as IssQty, convert(varchar(40), a.UpperMoQty) as UpperMoQty"
            + MoUpdateCols.SelectList()
            + " from mom_moallocate a join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=?"
            + " order by a.MoDId, a.SortSeq, a.AllocateId";
        // 产成品入库（iMPoIds = 行 MoDId）引用的不改。材料出库（iMPoIds = 子件 AllocateId）由 MoUpdateRemap 在修改后改写到新子件。
        const string DownSql = "select top 1 'product_in' as kind from rdrecords10 b join mom_orderdetail d on d.MoDId=b.iMPoIds"
            + " where d.MoId=?";
        // 存货数量小数位（数量）和件数小数位（辅计量数量）；读不到按 6 / 6。
        const string DigitsSql = "select cName, cValue from AccInformation where cSysID='AA'"
            + " and cName in ('iStrsQuanDecDgt','iNumDecDgt')";
        const int DigitsMax = 6;

        public static MoSnap Load(object conn, int id)
        {
            MoSnap snap = new MoSnap();
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id }, RowsMax);
            for (int i = 0; i < rows.Count; i++)
            {
                snap.Lines.Add(MoLineRow.Of(rows[i]));
            }
            snap.Code = rows.Count > 0 ? CoRows.Col(rows[0], "MoCode") : "";
            List<Dictionary<string, object>> allocs = Rows.Query(conn, AllocSql, new object[] { id }, RowsMax * 20);
            for (int i = 0; i < allocs.Count; i++)
            {
                snap.Allocs.Add(MoAllocRow.Of(allocs[i]));
            }
            return snap;
        }

        public static void Digits(object conn, MoSnap snap)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, DigitsSql, new object[0], 10);
            for (int i = 0; i < rows.Count; i++)
            {
                string name = CoRows.Col(rows[i], "cName");
                int n;
                if (!int.TryParse(CoRows.Col(rows[i], "cValue"), NumberStyles.Integer, CultureInfo.InvariantCulture, out n)
                    || n < 0 || n > DigitsMax)
                {
                    continue;
                }
                if (name == "iStrsQuanDecDgt")
                {
                    snap.QtyDigits = n;
                }
                else if (name == "iNumDecDgt")
                {
                    snap.AuxDigits = n;
                }
            }
        }

        // 只改未关闭（全部行 Status 1 / 2 / 3）、非集合、不受审批流控制、没报检、没被产成品入库引用的订单。
        // 已审核的照 U8 客户端「变更」直接改（不弃审）；已领料的由 MoUpdateRemap 抓取材料出库引用、修改后改写到新子件。
        public static void Gate(object conn, int id, MoSnap snap)
        {
            for (int i = 0; i < snap.Lines.Count; i++)
            {
                LineGate(snap.Lines[i]);
            }
            HashSet<string> keys = new HashSet<string>();
            for (int i = 0; i < snap.Allocs.Count; i++)
            {
                MoAllocRow a = snap.Allocs[i];
                AllocGate(a);
                // MoUpdateRestore 按（行、行号、存货）对上 U8 重插的子件，这三项重复的订单对不准，不改。
                if (!keys.Add(a.MoDId.ToString(CultureInfo.InvariantCulture) + "|" + a.SortSeq.ToString(CultureInfo.InvariantCulture)
                    + "|" + MoAllocRow.Norm(a.InvCode)))
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单有行号、存货都相同的子件（" + a.InvCode + "），请在 U8 客户端修改");
                }
            }
            MoUpdateRefs.Gate(conn, id);
            string down = Rows.Scalar(conn, DownSql, new object[] { id });
            if (down == "product_in")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已被产成品入库单引用，不能修改");
            }
        }

        // 带替代料（mom_moallocatesub）的不改：扩展实体不带替代料，MOrderUpdate 重插子件后替代料会丢。
        // 已领料（IssQty > 0）不在这里拒绝：有改动时任何已领料子件改后需求不能少于已领（MoUpdateRemap.CheckIssued，含超额领料），引用由 MoUpdateRemap 改写。
        static void AllocGate(MoAllocRow a)
        {
            if (a.Subs > 0)
            {
                throw new BridgeException(409, "state_mismatch", "生产订单的子件 " + a.InvCode + " 有替代料，请在 U8 客户端修改");
            }
            List<string> cols = MoUpdateCols.NotDefault(a.Keep);
            if (cols.Count > 0)
            {
                throw new BridgeException(409, "state_mismatch", string.Format(CultureInfo.InvariantCulture, MoUpdateCols.GuardText,
                    a.InvCode + "：" + string.Join("、", cols.ToArray())));
            }
        }

        // Status：1 / 2 未审核，3 已审核（实测 MOrderUpdate 可改，状态保持 3），4 关闭（先打开再改，同 U8 客户端）。
        internal static void LineGate(MoLineRow line)
        {
            if (line.Cf != "0")
            {
                throw new BridgeException(409, "state_mismatch", "集合生产订单请在 U8 客户端修改");
            }
            if (line.Status == "4")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已关闭，请先打开再修改");
            }
            if (line.Status != "1" && line.Status != "2" && line.Status != "3")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单状态不允许修改");
            }
            if (line.Wf == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "生产订单受审批流控制，本期不支持修改");
            }
            if (line.Declared > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已报检，不能修改");
            }
        }

        internal static decimal Dec(string text)
        {
            decimal value;
            if (decimal.TryParse((text ?? "").Trim(), NumberStyles.Number, CultureInfo.InvariantCulture, out value))
            {
                return value;
            }
            return 0m;
        }

        internal static int Int(string text)
        {
            int n;
            if (int.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }
    }

    // 一张生产订单的快照（调用前）或回读（调用后）。
    internal sealed class MoSnap
    {
        public string Code = "";
        public List<MoLineRow> Lines = new List<MoLineRow>();
        public List<MoAllocRow> Allocs = new List<MoAllocRow>();
        public int QtyDigits = 6;
        public int AuxDigits = 6;

        public List<MoAllocRow> AllocsOf(int moDId)
        {
            List<MoAllocRow> list = new List<MoAllocRow>();
            for (int i = 0; i < Allocs.Count; i++)
            {
                if (Allocs[i].MoDId == moDId)
                {
                    list.Add(Allocs[i]);
                }
            }
            return list;
        }

        public MoLineRow LineById(int moDId)
        {
            for (int i = 0; i < Lines.Count; i++)
            {
                if (Lines[i].MoDId == moDId)
                {
                    return Lines[i];
                }
            }
            return null;
        }
    }

    internal sealed class MoLineRow
    {
        public Dictionary<string, object> Row;
        public int MoDId;
        public int SortSeq;
        public string InvCode;
        public decimal Qty;
        public decimal MrpQty;
        public string MoClass;
        public string AuxUnit;
        public decimal ChangeRate;
        // 行件数的原文（null 时为空串）。
        public string AuxQty = "";
        public string Status;
        // 审核人（操作员编码）、审核日期；未审核为空。
        public string Verifier = "";
        public string VerifiedAt = "";
        public string Wf;
        public string Cf;
        public decimal Declared;
        public string Remark;
        public string Start;
        public string Due;
        public Dictionary<string, string> Defines = new Dictionary<string, string>(StringComparer.Ordinal);

        public static MoLineRow Of(Dictionary<string, object> row)
        {
            MoLineRow line = new MoLineRow();
            line.Row = row;
            line.MoDId = CoRows.AsId(CoRows.Col(row, "MoDId"));
            line.SortSeq = MoUpdateSql.Int(CoRows.Col(row, "SortSeq"));
            line.InvCode = CoRows.Col(row, "InvCode");
            line.Qty = MoUpdateSql.Dec(CoRows.Col(row, "Qty"));
            line.MrpQty = MoUpdateSql.Dec(CoRows.Col(row, "MrpQty"));
            line.MoClass = CoRows.Col(row, "MoClass");
            line.AuxUnit = CoRows.Col(row, "AuxUnitCode");
            line.ChangeRate = MoUpdateSql.Dec(CoRows.Col(row, "ChangeRate"));
            line.AuxQty = CoRows.Col(row, "AuxQty");
            line.Status = CoRows.Col(row, "Status");
            line.Verifier = CoRows.Col(row, "RelsUser");
            line.VerifiedAt = CoRows.Col(row, "RelsDate");
            line.Wf = CoRows.Col(row, "wf");
            line.Cf = CoRows.Col(row, "cf");
            line.Declared = MoUpdateSql.Dec(CoRows.Col(row, "declared"));
            line.Remark = CoRows.Col(row, "Remark");
            line.Start = CoRows.Col(row, "StartDate");
            line.Due = CoRows.Col(row, "DueDate");
            for (int i = 0; i < MoUpdateReq.Defines.Length; i++)
            {
                string key = MoUpdateReq.Defines[i];
                line.Defines[key] = CoRows.Col(row, key);
            }
            return line;
        }
    }

    internal sealed class MoAllocRow
    {
        public int AllocateId;
        public int MoDId;
        public int SortSeq;
        public string InvCode;
        public decimal Qty;
        // 件数的原文（null 时为空串）。
        public string AuxQty = "";
        // UpperMoQty 的原文（null 时为空串）；U8 重插后变 0，桥按新旧行数量比例写回。
        public string UpperMoQty = "";
        public decimal IssQty;
        public int Subs;
        // MoUpdateCols 名单里各列的原文；下面的类型化字段取自其中。
        public Dictionary<string, string> Keep = new Dictionary<string, string>();
        public decimal BaseN;
        public decimal BaseD;
        public decimal AuxBaseN;
        public decimal CompScrap;
        public decimal ParentScrap;
        // FVFlag：1 变动用量（Variety），其余按固定用量。
        public string FVFlag = "1";
        public string Wh = "";
        public string AuxUnit = "";
        public decimal ChangeRate;
        public string Remark = "";
        public string ProductType = "";

        public static MoAllocRow Of(Dictionary<string, object> row)
        {
            MoAllocRow a = new MoAllocRow();
            a.AllocateId = CoRows.AsId(CoRows.Col(row, "AllocateId"));
            a.MoDId = CoRows.AsId(CoRows.Col(row, "MoDId"));
            a.SortSeq = MoUpdateSql.Int(CoRows.Col(row, "SortSeq"));
            a.InvCode = CoRows.Col(row, "InvCode");
            a.Qty = MoUpdateSql.Dec(CoRows.Col(row, "Qty"));
            a.AuxQty = CoRows.Col(row, "AuxQty");
            a.UpperMoQty = CoRows.Col(row, "UpperMoQty");
            a.IssQty = MoUpdateSql.Dec(CoRows.Col(row, "IssQty"));
            a.Subs = MoUpdateSql.Int(CoRows.Col(row, MoUpdateCols.Subs));
            a.Keep = MoUpdateCols.Read(row);
            a.BaseN = MoUpdateSql.Dec(a.Get("BaseQtyN"));
            a.BaseD = MoUpdateSql.Dec(a.Get("BaseQtyD"));
            a.AuxBaseN = MoUpdateSql.Dec(a.Get("AuxBaseQtyN"));
            a.CompScrap = MoUpdateSql.Dec(a.Get("CompScrap"));
            a.ParentScrap = MoUpdateSql.Dec(a.Get("ParentScrap"));
            a.FVFlag = a.Get("FVFlag");
            a.Wh = a.Get("WhCode");
            a.AuxUnit = a.Get("AuxUnitCode");
            a.ChangeRate = MoUpdateSql.Dec(a.Get("ChangeRate"));
            a.Remark = a.Get("Remark");
            a.ProductType = a.Get("ProductType");
            return a;
        }

        public string Get(string col)
        {
            string value;
            if (!Keep.TryGetValue(col, out value) || value == null || value == MoUpdateCols.NullMark)
            {
                return "";
            }
            return value;
        }

        // 子件按（行号、存货、用量分子分母、仓库、自由项）对应；仍相同的多行按 AllocateId 顺序对应。
        // 回读（库对库）用 6 位；对 U8 加载出的实体用 MoUpdateExt 的小数位（Load 把用量按数量精度 Math.Round 过）。
        public string Key
        {
            get { return KeyAt(6); }
        }

        public string KeyAt(int digits)
        {
            string[] free = new string[10];
            for (int i = 0; i < 10; i++)
            {
                free[i] = Get("Free" + (i + 1).ToString(CultureInfo.InvariantCulture));
            }
            return MakeKey(SortSeq, InvCode, new decimal[] { BaseN, BaseD }, Wh, free, digits);
        }

        // nd：基本用量分子、分母。
        internal static string MakeKey(int seq, string inv, decimal[] nd, string wh, string[] free, int digits)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(seq.ToString(CultureInfo.InvariantCulture)).Append('|').Append(Norm(inv))
                .Append('|').Append(MoUpdateAlloc.Num(Math.Round(nd[0], digits))).Append('|').Append(MoUpdateAlloc.Num(Math.Round(nd[1], digits)))
                .Append('|').Append(Norm(wh));
            for (int i = 0; i < free.Length; i++)
            {
                sb.Append('|').Append(Norm(free[i]));
            }
            return sb.ToString();
        }

        internal static string Norm(string text)
        {
            return (text ?? "").Trim().ToUpperInvariant();
        }
    }
}
