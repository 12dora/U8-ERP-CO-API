using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 形态转换单（15）、调拨申请单（62）、盘点单（18）的字段白名单和新增模板。
    // 空模板同调拨：表头、表体视图 where 1=2（AssemM/AssemD、transrequestm/transrequestd、checkm/checkd）。
    // 备注、批号用各表自己的列名（cavmemo / ctvmemo / ccvmemo，cavbatch / ctvbatch / ccvbatch），不做 cmemo 别名。
    internal static partial class StockDom
    {
        static readonly HashSet<string> AvHeadNames = Names("davdate", "cdepcode", "cpersoncode", "cirdcode", "cordcode", "cavmemo");
        static readonly HashSet<string> AvBodyNames = Names("cinvcode", "cwhcode", "bavtype", "igroupno", "iavquantity",
            "cassunit", "cavbatch", "cbmemo");
        static readonly HashSet<string> TrHeadNames = Names("dtvdate", "cowhcode", "ciwhcode", "codepcode", "cidepcode",
            "cordcode", "cirdcode", "cpersoncode", "ctvmemo");
        static readonly HashSet<string> TrBodyNames = Names("cinvcode", "itvquantity", "itvchkquantity", "cassunit",
            "ctvbatch", "cbmemo");
        static readonly HashSet<string> CvHeadNames = Names("cwhcode", "dcvdate", "dacdate", "cdepcode", "cpersoncode",
            "cirdcode", "cordcode", "ccvmemo");
        static readonly HashSet<string> CvBodyNames = Names("cinvcode", "icvquantity", "icvcquantity", "cassunit",
            "ccvbatch", "ccvreason", "cbmemo");

        internal const string AvBefore = "转换前";
        internal const string AvAfter = "转换后";

        static HashSet<string> Names(params string[] names)
        {
            return new HashSet<string>(names, StringComparer.Ordinal);
        }

        static bool MiscType(VoucherKind kind)
        {
            return StockMisc.Handles(kind);
        }

        // Allowed 的前段：调拨单、货位调整单和本文件的三类各走自己的名单；其他类型返回 null，交回 Allowed 往下判断。
        static bool? SpecialField(VoucherKind kind, bool head, string low)
        {
            // 货位调整单（StockDomAdjust.cs）。
            if (PositionAdjust.Handles(kind))
            {
                return AdjustField(head, low);
            }
            if (MiscType(kind))
            {
                return MiscField(kind, head, low);
            }
            if (kind == null || kind.StType != "12")
            {
                return null;
            }
            return head ? TransferHeadField(low) : TransferBodyField(low);
        }

        static bool MiscField(VoucherKind kind, bool head, string low)
        {
            HashSet<string> set = MiscSet(kind.StType, head);
            if (set.Contains(low))
            {
                return true;
            }
            if (head)
            {
                return Span(low, "cdefine", 1, 16);
            }
            return Span(low, "cfree", 1, 10) || Span(low, "cdefine", 22, 37);
        }

        static HashSet<string> MiscSet(string st, bool head)
        {
            if (st == "15")
            {
                return head ? AvHeadNames : AvBodyNames;
            }
            if (st == "62")
            {
                return head ? TrHeadNames : TrBodyNames;
            }
            return head ? CvHeadNames : CvBodyNames;
        }

        internal static IEnumerable<string> MiscMetaNames()
        {
            List<string> all = new List<string>();
            all.AddRange(AvHeadNames);
            all.AddRange(AvBodyNames);
            all.AddRange(TrHeadNames);
            all.AddRange(TrBodyNames);
            all.AddRange(CvHeadNames);
            all.AddRange(CvBodyNames);
            return all;
        }

        // 数量列：形态转换 iAVQuantity、调拨申请 iTVQuantity、盘点的实盘 iCVCQuantity（账面 iCVQuantity 另填）。
        internal static string MiscQty(VoucherKind kind)
        {
            if (kind.StType == "15")
            {
                return "iAVQuantity";
            }
            return kind.StType == "62" ? "iTVQuantity" : "iCVCQuantity";
        }

        internal static string MiscNum(VoucherKind kind)
        {
            if (kind.StType == "15")
            {
                return "iAVNum";
            }
            return kind.StType == "62" ? "iTVNum" : "iCVCNum";
        }

        internal static object MiscHead(object conn, VoucherKind kind, Dictionary<string, object> fields,
            string maker, string billDate)
        {
            if (fields == null)
            {
                throw new BridgeException(400, "bad_request", "缺少表头");
            }
            object dom = MiscBlank(conn, kind, true);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                FillMisc(kind, names, row, maker);
                Overlay(names, row, fields, kind, "head");
                FillMisc(kind, names, row, maker);
                MiscDefaults(conn, kind, names, row, billDate ?? "");
                RequireMiscHead(kind, row);
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

        // vt_id 取 voucheritems 的缺省模板：15 → 91、62 → 37、18 → 29。形态转换写 cVouchType=15，盘点写 CheckVouchType=0。
        static void FillMisc(VoucherKind kind, List<string> names, Dictionary<string, string> row, string maker)
        {
            Put(names, row, "editprop", "A");
            Put(names, row, "vt_id", kind.StType == "15" ? "91" : (kind.StType == "62" ? "37" : "29"));
            Put(names, row, "iverifystate", "0");
            Put(names, row, "iswfcontrolled", "0");
            Put(names, row, "cmaker", maker ?? "");
            Put(names, row, "id", "");
            Put(names, row, kind.CodeColumn, "");
            if (kind.StType == "15")
            {
                Put(names, row, "cvouchtype", "15");
            }
            else if (kind.StType == "18")
            {
                Put(names, row, "checkvouchtype", "0");
            }
        }

        // 日期缺省取登录日期；收发类别缺省按名称取末级类别（形态转换：转换出库 / 转换入库；盘点：盘盈入库 / 盘亏出库），
        // 账套里没有这个名称就留空（调用方可自己给 cordcode / cirdcode）。
        static void MiscDefaults(object conn, VoucherKind kind, List<string> names, Dictionary<string, string> row, string billDate)
        {
            if (kind.StType == "15")
            {
                PutMissing(names, row, "dAVDate", billDate);
                PutRd(conn, names, row, "cORdCode", "转换出库", false);
                PutRd(conn, names, row, "cIRdCode", "转换入库", true);
                return;
            }
            if (kind.StType == "62")
            {
                PutMissing(names, row, "dTVDate", billDate);
                return;
            }
            PutMissing(names, row, "dCVDate", billDate);
            PutMissing(names, row, "dACDate", CellOf(row, Canonical(names, "dCVDate")));
            PutRd(conn, names, row, "cIRdCode", "盘盈入库", true);
            PutRd(conn, names, row, "cORdCode", "盘亏出库", false);
        }

        static void PutRd(object conn, List<string> names, Dictionary<string, string> row, string key, string rdName, bool inward)
        {
            string canon = Canonical(names, key);
            if (canon == null || CellOf(row, canon).Length > 0)
            {
                return;
            }
            string code = Rows.Scalar(conn,
                "select top 1 cRdCode from Rd_Style where cRdName=? and bRdFlag=? and bRdEnd=1 order by cRdCode",
                new object[] { rdName, inward ? 1 : 0 });
            if (code != null && code.Trim().Length > 0)
            {
                row[canon] = code.Trim();
            }
        }

        static void RequireMiscHead(VoucherKind kind, Dictionary<string, string> row)
        {
            string date = kind.StType == "15" ? "dAVDate" : (kind.StType == "62" ? "dTVDate" : "dCVDate");
            if (CellOf(row, date).Length == 0)
            {
                throw new BridgeException(400, "bad_request", "单据日期必须是 yyyy-MM-dd");
            }
            if (kind.StType == "62" && CellOf(row, "cOWhCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定转出仓库");
            }
            if (kind.StType == "62" && CellOf(row, "cIWhCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定转入仓库");
            }
            if (kind.StType == "18" && CellOf(row, "cWhCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定盘点仓库");
            }
        }

        internal static object MiscBody(object conn, VoucherKind kind, object[] lines, string wh)
        {
            RequireLines(lines);
            object dom = MiscBlank(conn, kind, false);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                List<Dictionary<string, string>> rows = new List<Dictionary<string, string>>();
                for (int i = 0; i < lines.Length; i++)
                {
                    rows.Add(MiscLine(conn, names, kind, lines[i], i + 1, wh));
                }
                CheckMiscRows(kind, rows);
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

        static Dictionary<string, string> MiscLine(object conn, List<string> names, VoucherKind kind,
            object raw, int rowNo, string wh)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            Dictionary<string, string> row = RowMap();
            Put(names, row, "editprop", "A");
            Put(names, row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            if (kind.StType == "15")
            {
                Put(names, row, "bcosting", "1");
                Put(names, row, "igroupno", "1");
            }
            Overlay(names, row, line, kind, "lines");
            Put(names, row, "editprop", "A");
            if (CellOf(row, "cInvCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "表体行缺少存货");
            }
            if (kind.StType == "18")
            {
                if (CellOf(row, "iCVQuantity").Length > 0)
                {
                    MiscQtyOk(CellOf(row, "iCVQuantity"), true);
                }
                FillBook(conn, names, row, wh);
            }
            MiscQtyOk(CellOf(row, MiscQty(kind)), kind.StType == "18");
            StockUnits.Apply(conn, names, row, MiscQty(kind), MiscNum(kind), true);
            MiscSecondQty(conn, names, row, kind);
            if (kind.StType == "15" && CellOf(row, "cWhCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "形态转换单每行都要有仓库 cwhcode");
            }
            return row;
        }

        // 第二组数量：盘点的账面 iCVQuantity 补件数；调拨申请的核准数量 iTvChkQuantity 不给时取申请数量（参照生成调拨单按核准数量算），
        // 给了就按 0 或以上核对，再补核准件数 iTVChkNum。
        static void MiscSecondQty(object conn, List<string> names, Dictionary<string, string> row, VoucherKind kind)
        {
            if (kind.StType == "18")
            {
                StockUnits.Apply(conn, names, row, "iCVQuantity", "iCVNum", true);
                FillGainLoss(names, row, "iCVCQuantity", "iCVQuantity", "iAdInQuantity", "iAdOutQuantity");
                FillGainLoss(names, row, "iCVCNum", "iCVNum", "iAdInNum", "iAdOutNum");
                return;
            }
            if (kind.StType != "62")
            {
                return;
            }
            string chk = CellOf(row, "iTvChkQuantity");
            if (chk.Length == 0)
            {
                Put(names, row, "iTvChkQuantity", CellOf(row, "iTVQuantity"));
            }
            else
            {
                MiscQtyOk(chk, true);
            }
            ChkWithin(CellOf(row, "iTvChkQuantity"), CellOf(row, "iTVQuantity"));
            StockUnits.Apply(conn, names, row, "iTvChkQuantity", "iTVChkNum", true);
        }

        // 调拨申请：核准数量不能大于申请数量（新增、修改后整行复核都用）。读不出数的交给别处的数量校验。
        internal static void ChkWithin(string chkText, string qtyText)
        {
            decimal chk;
            decimal qty;
            if (StockUnits.Dec(chkText, out chk) && StockUnits.Dec(qtyText, out qty) && chk > qty + 0.000001m)
            {
                throw new BridgeException(400, "bad_request", "核准数量不能大于申请数量");
            }
        }

        // 数量是有限数、大于 0（盘点的数量可以是 0）、不超过 1000000000000、最多 6 位小数（API 层同一规则）。
        internal static void MiscQtyOk(string text, bool zeroOk)
        {
            decimal qty;
            if (!StockUnits.Dec(text, out qty) || qty < 0m || (qty == 0m && !zeroOk) || qty > 1000000000000m)
            {
                throw new BridgeException(400, "bad_request", zeroOk ? "盘点数量必须大于或等于 0" : "数量必须大于 0");
            }
            if (decimal.Round(qty, 6) != qty)
            {
                throw new BridgeException(400, "bad_request", "数量最多 6 位小数");
            }
        }

        static void CheckMiscRows(VoucherKind kind, List<Dictionary<string, string>> rows)
        {
            List<string[]> pairs = new List<string[]>();
            for (int i = 0; i < rows.Count; i++)
            {
                if (kind.StType == "15")
                {
                    pairs.Add(new string[] { CellOf(rows[i], "bAVType"), CellOf(rows[i], "iGroupNO") });
                }
                else if (kind.StType == "18")
                {
                    pairs.Add(new string[] { CheckKey(rows[i]) });
                }
            }
            if (kind.StType == "15")
            {
                CheckGroups(pairs);
            }
            else if (kind.StType == "18")
            {
                CheckNoDup(pairs);
            }
        }

        // 形态转换：bavtype 只能是「转换前」「转换后」，按 igroupno 成组，每组至少一行转换前、一行转换后。修改后也按整单复核。
        internal static void CheckGroups(List<string[]> pairs)
        {
            Dictionary<string, int> seen = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < pairs.Count; i++)
            {
                string type = pairs[i][0];
                if (type != AvBefore && type != AvAfter)
                {
                    throw new BridgeException(400, "bad_request", "bavtype 只能是「转换前」或「转换后」");
                }
                string group = pairs[i][1];
                int mask;
                seen.TryGetValue(group, out mask);
                seen[group] = mask | (type == AvBefore ? 1 : 2);
            }
            foreach (KeyValuePair<string, int> kv in seen)
            {
                if (kv.Value != 3)
                {
                    throw new BridgeException(400, "bad_request", "形态转换每组（igroupno）都要有转换前和转换后的行");
                }
            }
        }

        static object MiscBlank(object conn, VoucherKind kind, bool head)
        {
            string view = ViewName(kind.StType, head);
            string alias = head ? "m" : "d";
            string sql = "select 'A' as editprop, " + alias + ".* from dbo." + view
                + " " + alias + " with(nolock) where 1=2";
            object dom = DomRows.Blank(conn, sql);
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            return dom;
        }
    }
}
