using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 被核销单据的表名、列名（只来自这里）。发票按表体行核销（iBVid = SaleBillVouchs.AutoID / PurBillVouchs.ID）；
    // 应收单、应付单的原始往来明细 iBVid 为 0（已在测试账套核对），按整单核销。
    internal sealed class WriteoffKind
    {
        public string Name;
        public string Title;
        public string Flag;
        public string[] Types;
        // 事务里带锁读表头：id、code、vtype、vdate、cur、rate、auditor（应收 / 应付审核人）、wf（审批流）、locked（网络锁），
        // 以及数据权限用的 cDwCode、cDeptCode、cPerson（往来单位、部门、业务员，按收付款单的列名取别名）。
        public string HeadSql;
        // 同上，按单号和单据类型找（取消核销从往来明细回找单据）：参数 (单号, 类型)。
        public string CodeSql;
        // 行是否属于本单：参数 (行 id, 表头 id)；应收应付单为 null。
        public string LineSql;

        public static WriteoffKind Of(string name)
        {
            switch (name)
            {
                case "sale_invoice":
                    return Make(new string[] { name, "销售发票", "AR" }, new string[] { "26", "27" },
                        "select convert(varchar(20), h.SBVID) as id, h.cSBVCode as code, h.cVouchType as vtype, "
                        + "convert(varchar(10), h.dDate, 23) as vdate, h.cexch_name as cur, "
                        + WriteoffSql.Dec("h.iExchRate", 10) + " as rate, h.cVerifier as auditor, "
                        + "case when isnull(h.iswfcontrolled,0)<>0 then 1 else 0 end as wf, 0 as locked, "
                        + "h.cCusCode as cDwCode, h.cDepCode as cDeptCode, h.cPersonCode as cPerson "
                        + "from SaleBillVouch h with (UPDLOCK, HOLDLOCK) where ",
                        new string[] { "h.SBVID=?", "h.cSBVCode=? and h.cVouchType=?" },
                        "select convert(varchar(20), b.AutoID) from SaleBillVouchs b where b.AutoID=? and b.SBVID=?");
                case "purchase_invoice":
                    return Make(new string[] { name, "采购发票", "AP" }, new string[] { "01", "02" },
                        "select convert(varchar(20), h.PBVID) as id, h.cPBVCode as code, h.cPBVBillType as vtype, "
                        + "convert(varchar(10), h.dPBVDate, 23) as vdate, h.cexch_name as cur, "
                        + WriteoffSql.Dec("h.cExchRate", 10) + " as rate, h.cPBVVerifier as auditor, "
                        + "case when isnull(h.IsWfControlled,0)<>0 then 1 else 0 end as wf, "
                        + "case when isnull(h.iNetLock,0)<>0 then 1 else 0 end as locked, "
                        + "h.cVenCode as cDwCode, h.cDepCode as cDeptCode, h.cPersonCode as cPerson "
                        + "from PurBillVouch h with (UPDLOCK, HOLDLOCK) where ",
                        new string[] { "h.PBVID=?", "h.cPBVCode=? and h.cPBVBillType=?" },
                        "select convert(varchar(20), b.ID) from PurBillVouchs b where b.ID=? and b.PBVID=?");
                case "ar_bill":
                    return Bill(name, "应收单", "AR", "R0");
                case "ap_bill":
                    return Bill(name, "应付单", "AP", "P0");
            }
            throw new BridgeException(400, "bad_request", "不支持核销的单据类型");
        }

        // 往来明细上的单据类型 → 被核销单据（取消核销用）；不支持的返回 null。
        public static WriteoffKind OfType(string flag, string vtype)
        {
            string[] names = flag == "AP" ? new string[] { "purchase_invoice", "ap_bill" }
                : new string[] { "sale_invoice", "ar_bill" };
            foreach (string name in names)
            {
                WriteoffKind kind = Of(name);
                if (Array.IndexOf(kind.Types, vtype) >= 0)
                {
                    return kind;
                }
            }
            return null;
        }

        // Ap_Vouch 没有网络锁列。
        static WriteoffKind Bill(string name, string title, string flag, string type)
        {
            return Make(new string[] { name, title, flag }, new string[] { type },
                "select convert(varchar(20), h.Auto_ID) as id, h.cVouchID as code, h.cVouchType as vtype, h.cFlag as flag, "
                + "convert(varchar(10), h.dVouchDate, 23) as vdate, h.cexch_name as cur, "
                + WriteoffSql.Dec("h.iExchRate", 10) + " as rate, h.cCheckMan as auditor, "
                + "case when isnull(h.IsWfControlled,0)<>0 then 1 else 0 end as wf, 0 as locked, "
                + "h.cDwCode, h.cDeptCode, h.cPerson "
                + "from Ap_Vouch h with (UPDLOCK, HOLDLOCK) where ",
                new string[] { "h.Auto_ID=?", "h.cVouchID=? and h.cVouchType=?" }, null);
        }

        // names：类型名、中文名、AR|AP；keys：按主键、按（单号, 类型）的条件。
        static WriteoffKind Make(string[] names, string[] types, string head, string[] keys, string line)
        {
            WriteoffKind k = new WriteoffKind();
            k.Name = names[0];
            k.Title = names[1];
            k.Flag = names[2];
            k.Types = types;
            k.HeadSql = head + keys[0];
            k.CodeSql = head + keys[1];
            k.LineSql = line;
            return k;
        }
    }

    // 核销的查询。往来明细表 Ar_Detail / Ap_Detail 按 AR / AP 选定；调用方的值只进参数。
    // 单据某行的未核销余额与 U8 Save 里查的同一口径：cCoVouchType / cCoVouchID = 单据，cDwCode = 往来单位，
    // iBVid = 行，iFlag < 3（NULL 不算，同 U8）；应收取借方减贷方，应付取贷方减借方。
    internal static class WriteoffSql
    {
        const string ReceiptHead = "select convert(varchar(20), h.iID) as id, h.cVouchID as code, h.cVouchType as vtype, "
            + "h.cFlag as flag, h.cDwCode, h.cDeptCode, h.cPerson, convert(varchar(10), h.dVouchDate, 23) as vdate, "
            + "h.cexch_name as cur, {rate} as rate, h.cCheckMan as auditor, "
            + "case when isnull(h.IsWfControlled,0)<>0 then 1 else 0 end as wf, 0 as locked "
            + "from Ap_CloseBill h with (UPDLOCK, HOLDLOCK) where h.iID=?";
        const string ReceiptLines = "select convert(varchar(20), b.ID) as line, {remf} as rem_f, {rem} as rem, "
            + "case when isnull(b.bPrePay,0)<>0 then 1 else 0 end as prepay "
            + "from Ap_CloseBills b with (UPDLOCK, HOLDLOCK) where b.iID=? order by b.ID";

        public static string Detail(string flag)
        {
            return flag == "AP" ? "Ap_Detail" : "Ar_Detail";
        }

        public static string Dec(string column, int scale)
        {
            return "convert(varchar(40), convert(decimal(28," + scale.ToString(CultureInfo.InvariantCulture) + "), isnull("
                + column + ",0)))";
        }

        static string Open(string flag, string suffix)
        {
            string d = "isnull(d.iDAmount" + suffix + ",0)";
            string c = "isnull(d.iCAmount" + suffix + ",0)";
            return flag == "AP" ? c + "-" + d : d + "-" + c;
        }

        public static Dictionary<string, object> Receipt(object conn, int id)
        {
            string sql = ReceiptHead.Replace("{rate}", Dec("h.iExchRate", 10));
            return Rows.One(conn, sql, new object[] { id });
        }

        public static List<Dictionary<string, object>> ReceiptRows(object conn, int id)
        {
            string sql = ReceiptLines.Replace("{remf}", Dec("b.iRAmt_f", 2)).Replace("{rem}", Dec("b.iRAmt", 2));
            return Rows.Query(conn, sql, new object[] { id }, 501);
        }

        public static decimal ReceiptRemain(object conn, int line)
        {
            string sql = "select " + Dec("b.iRAmt_f", 2) + " as v from Ap_CloseBills b where b.ID=?";
            return Num(Rows.Scalar(conn, sql, new object[] { line }));
        }

        // 收付款单当前最大的行主键（Save 之前取）。
        public static int MaxCloseLine(object conn, int receiptId)
        {
            string sql = "select convert(varchar(20), isnull(max(b.ID),0)) from Ap_CloseBills b where b.iID=?";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { receiptId }));
        }

        // Save 新插的行（行主键大于 after）的未核销余额合计（原币）；没有新行为 0。
        public static decimal AddedRemain(object conn, int receiptId, int after)
        {
            string sql = "select " + Dec("sum(b.iRAmt_f)", 2) + " as v from Ap_CloseBills b where b.iID=? and b.ID>?";
            return Num(Rows.Scalar(conn, sql, new object[] { receiptId, after }));
        }

        public static Dictionary<string, object> Head(object conn, WriteoffKind kind, int id)
        {
            return Rows.One(conn, kind.HeadSql, new object[] { id });
        }

        public static bool LineOf(object conn, WriteoffKind kind, int line, int id)
        {
            return Rows.Scalar(conn, kind.LineSql, new object[] { line, id }) != null;
        }

        // 单据原始往来明细上的往来单位（cProcStyle = cVouchType 的行）；没有这类行（未应收 / 应付审核）返回 null。
        public static string Partner(object conn, string flag, string type, string code)
        {
            string sql = "select top 1 d.cDwCode from " + Detail(flag) + " d where d.cFlag=? and d.cVouchType=? "
                + "and d.cVouchID=? and d.cProcStyle=d.cVouchType";
            return Rows.Scalar(conn, sql, new object[] { flag, type, code });
        }

        static string BalanceFrom(string flag)
        {
            return " from " + Detail(flag) + " d where d.cFlag=? and d.cCoVouchType=? and d.cCoVouchID=? and d.cDwCode=? "
                + "and d.iFlag<3";
        }

        // 该行（iBVid）的未核销余额（原币）。
        public static decimal Balance(object conn, string flag, string type, string code, string dw, int line)
        {
            string sql = "select " + Dec("sum(" + Open(flag, "_f") + ")", 2) + " as v" + BalanceFrom(flag) + " and d.iBVid=?";
            return Num(Rows.Scalar(conn, sql, new object[] { flag, type, code, dw, line }));
        }

        // 余额大于 0 的行（iBVid → 余额）。
        public static Dictionary<int, decimal> OpenLines(object conn, string flag, string type, string code, string dw)
        {
            string sql = "select convert(varchar(20), d.iBVid) as line, " + Dec("sum(" + Open(flag, "_f") + ")", 2) + " as v"
                + BalanceFrom(flag) + " group by d.iBVid having sum(" + Open(flag, "_f") + ")>0";
            Dictionary<int, decimal> map = new Dictionary<int, decimal>();
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[] { flag, type, code, dw }, 1001))
            {
                map[CoRows.AsId(CoRows.Col(row, "line"))] = Num(CoRows.Col(row, "v"));
            }
            return map;
        }

        // 本位币，见 AccDefaults.Home。
        public static string LocalCurrency(object conn)
        {
            return AccDefaults.Home(conn);
        }

        // 日期所在的会计期间（年度、期间号），与 U8 Save 同一查询：UFSYSTEM..UA_Period 上 dBegin <= 日期 <= dEnd；
        // 会计期间不一定是自然月。找不到返回 null。
        public static int[] PeriodOf(object conn, string acc, string date)
        {
            string sql = "select top 1 convert(varchar(10), p.iYear) as y, convert(varchar(10), p.iId) as p "
                + "from UFSYSTEM..UA_Period p where p.cAcc_Id=? and p.dBegin<=cast(convert(date, ?, 23) as datetime) "
                + "and p.dEnd>=cast(convert(date, ?, 23) as datetime) and (p.bIsDelete=0 or p.bIsDelete is null) order by p.iYear, p.iId";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { acc ?? "", date, date });
            int year = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "y"));
            int period = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "p"));
            return year > 0 && period > 0 ? new int[] { year, period } : null;
        }

        // 该年度、期间在 GL_mend 上应收 / 应付已结账。
        public static bool Closed(object conn, string flag, int year, int period)
        {
            string col = flag == "AP" ? "bflag_AP" : "bflag_AR";
            string sql = "select top 1 convert(varchar(10), iperiod) from GL_mend where iyear=? and iperiod=? and isnull("
                + col + ",0)<>0";
            return Rows.Scalar(conn, sql, new object[] { year, period }) != null;
        }

        // 应收 / 应付系统启用日期（AccInformation 的 dARStartDate / dAPStartDate，同 ReportsOpening）；读不到返回 false。
        public static bool StartDate(object conn, string flag, out DateTime start)
        {
            string sql = "select top 1 left(ltrim(cValue), 10) as v from AccInformation where cSysID=? and cName=?";
            string text = Rows.Scalar(conn, sql, new object[] { flag, "d" + flag + "StartDate" });
            return DateTime.TryParseExact(text == null ? "" : text.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out start);
        }

        public static int MaxDetail(object conn, string flag)
        {
            string raw = Rows.Scalar(conn, "select convert(varchar(20), isnull(max(Auto_ID),0)) from " + Detail(flag), new object[0]);
            int n;
            return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? n : 0;
        }

        // Save 之后本收付款单上新写的核销行（cProcStyle = 9P）按核销号分组：核销号 → 行数。
        public static Dictionary<string, int> NewCancelNos(object conn, string flag, int after, string type, string code)
        {
            string sql = "select d.cCancelNo as no, convert(varchar(20), count(*)) as n from " + Detail(flag)
                + " d where d.Auto_ID>? and d.cProcStyle=N'9P' and d.cVouchType=? and d.cVouchID=? group by d.cCancelNo";
            Dictionary<string, int> map = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[] { after, type, code }, 20))
            {
                map[CoRows.Col(row, "no")] = CoRows.AsId(CoRows.Col(row, "n"));
            }
            return map;
        }

        // 回读：该核销号的行数。
        public static int CancelRows(object conn, string flag, string cancelNo)
        {
            string sql = "select convert(varchar(20), count(*)) from " + Detail(flag) + " d where d.cCancelNo=? and d.cProcStyle=N'9P'";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { cancelNo }));
        }

        // 回读：该核销号的核销行按（对方单据类型、单号、行）合计借贷之和（原币）：键 "类型|单号|行"。
        // 被核销单据一侧是本次金额（应收贷方、应付借方），收付款单自身的冲减行是负的合计。
        public static Dictionary<string, decimal> CancelAmounts(object conn, string flag, string cancelNo)
        {
            string sql = "select d.cCoVouchType as t, d.cCoVouchID as c, convert(varchar(20), isnull(d.iBVid,0)) as l, "
                + Dec("sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0))", 2) + " as v from " + Detail(flag)
                + " d where d.cCancelNo=? and d.cProcStyle=N'9P' group by d.cCoVouchType, d.cCoVouchID, d.iBVid";
            Dictionary<string, decimal> map = new Dictionary<string, decimal>(StringComparer.Ordinal);
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[] { cancelNo }, 1001))
            {
                string key = AmountKey(CoRows.Col(row, "t"), CoRows.Col(row, "c"), CoRows.AsId(CoRows.Col(row, "l")));
                map[key] = Num(CoRows.Col(row, "v"));
            }
            return map;
        }

        public static string AmountKey(string type, string code, int line)
        {
            return type.Trim() + "|" + code.Trim() + "|" + line.ToString(CultureInfo.InvariantCulture);
        }

        public static decimal Num(string text)
        {
            decimal value;
            if (text != null && decimal.TryParse(text.Trim(), NumberStyles.Number, CultureInfo.InvariantCulture, out value))
            {
                return value;
            }
            return 0m;
        }
    }
}
