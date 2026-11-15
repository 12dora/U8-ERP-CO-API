using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单新增：调用前在请求连接上查完能查的（存货、订单类别、部门、仓库、单号），调用后在新连接上回读。
    // 表名、列名都是固定的 U8 表；调用方的值只进 ADO 参数。
    internal static class MoCreateSql
    {
        const string InvSql = "select convert(varchar(10), isnull(bSelf,0)) as self, convert(varchar(10), dEDate, 23) as ended"
            + " from Inventory where cInvCode=?";
        const string MoTypeSql = "select count(*) as n from mom_motype where MotypeCode=?";
        const string DeptSql = "select convert(varchar(10), isnull(bDepEnd,0)) as leaf from Department where cDepCode=?";
        const string WhSql = "select convert(varchar(10), dWhEndDate, 23) as ended from Warehouse where cWhCode=?";
        const string CodeSql = "select count(*) as n from mom_order where MoCode=?";
        // 调用前的最大 MoId 和数据库时间在同一个查询里取，拿不到新 MoId 时 MoCreateLost 按两者找。
        const string MarkSql = "select convert(varchar(20), isnull(max(MoId),0)) as n, convert(varchar(23), getdate(), 121) as t"
            + " from mom_order";
        // U8 返回的新 MoId 要在新连接上确认：订单存在、制单人是本操作员。
        const string OwnSql = "select count(*) as n from mom_order where MoId=? and CreateUser=?";
        const string DigitsSql = "select cValue from AccInformation where cSysID='AA' and cName='iStrsQuanDecDgt'";
        // mom_orderdetail.Qty 是 6 位小数；账套设置读不到或超出时按 6。
        const int DigitsMax = 6;
        // 回读：每行的状态和 U8 展开的子件行数（没有有效标准 BOM 时为 0）。
        internal const string LinesSql = "select o.MoCode, convert(varchar(20), d.MoDId) as MoDId,"
            + " convert(varchar(10), d.SortSeq) as SortSeq, d.InvCode, convert(varchar(40), d.Qty) as Qty,"
            + " convert(varchar(10), d.Status) as Status,"
            + " convert(varchar(10), (select count(*) from mom_moallocate a where a.MoDId=d.MoDId)) as allocates"
            + " from mom_order o join mom_orderdetail d on d.MoId=o.MoId where o.MoId=? order by d.SortSeq, d.MoDId";
        public static void Validate(object conn, MoCreateAsk ask)
        {
            if (ask.Code.Length > 0 && Count(conn, CodeSql, ask.Code) > 0)
            {
                throw new BridgeException(409, "state_mismatch", "生产订单号已存在：" + ask.Code);
            }
            ask.QtyDigits = Digits(conn);
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoLine line = ask.Lines[i];
                CheckQty(line, ask.QtyDigits);
                CheckInv(conn, line);
                if (Count(conn, MoTypeSql, line.MoType) == 0)
                {
                    throw Bad(line, "生产订单类别不存在：" + line.MoType);
                }
                CheckDept(conn, line);
                CheckWh(conn, line);
            }
        }

        // 存货数量小数位：U8 界面按它限制录入，多出的小数不送给 U8，400。
        static int Digits(object conn)
        {
            string text = Rows.Scalar(conn, DigitsSql, new object[0]);
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n)
                && n >= 0 && n <= DigitsMax)
            {
                return n;
            }
            return DigitsMax;
        }

        internal static void CheckQty(MoLine line, int digits)
        {
            if (decimal.Round(line.Qty, digits) != line.Qty)
            {
                throw Bad(line, "qty 最多 " + digits.ToString(CultureInfo.InvariantCulture) + " 位小数（U8 存货数量小数位）");
            }
        }

        static void CheckInv(object conn, MoLine line)
        {
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { line.InvCode });
            if (row == null)
            {
                throw Bad(line, "存货不存在：" + line.InvCode);
            }
            if (CoRows.Col(row, "self") != "1")
            {
                throw Bad(line, "存货 " + line.InvCode + " 不是自制件");
            }
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, line.Start) <= 0)
            {
                throw Bad(line, "存货 " + line.InvCode + " 已停用");
            }
        }

        static void CheckDept(object conn, MoLine line)
        {
            Dictionary<string, object> row = Rows.One(conn, DeptSql, new object[] { line.Dept });
            if (row == null)
            {
                throw Bad(line, "部门不存在：" + line.Dept);
            }
            if (CoRows.Col(row, "leaf") != "1")
            {
                throw Bad(line, "部门 " + line.Dept + " 不是末级部门");
            }
        }

        static void CheckWh(object conn, MoLine line)
        {
            if (line.Wh.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, WhSql, new object[] { line.Wh });
            if (row == null)
            {
                throw Bad(line, "仓库不存在：" + line.Wh);
            }
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, line.Start) <= 0)
            {
                throw Bad(line, "仓库 " + line.Wh + " 已停用");
            }
        }

        public static void Mark(object conn, MoCreateAsk ask)
        {
            Dictionary<string, object> row = Rows.One(conn, MarkSql, new object[0]);
            ask.MaxBefore = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "n"));
            ask.Since = row == null ? "" : CoRows.Col(row, "t");
            if (ask.Since.Length == 0)
            {
                throw new BridgeException(500, "internal", "读不到数据库时间");
            }
        }

        public static bool Owned(object conn, int moId, string user)
        {
            if (moId <= 0)
            {
                return false;
            }
            string n = Rows.Scalar(conn, OwnSql, new object[] { moId, user });
            return n != null && n.Trim() == "1";
        }

        public static List<Dictionary<string, object>> Lines(object conn, int moId)
        {
            return Rows.Query(conn, LinesSql, new object[] { moId }, MoCreateReq.LinesMax + 1);
        }

        static int Count(object conn, string sql, string value)
        {
            string text = Rows.Scalar(conn, sql, new object[] { value });
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }

        static BridgeException Bad(MoLine line, string text)
        {
            return new BridgeException(400, "bad_request",
                "第 " + line.Seq.ToString(CultureInfo.InvariantCulture) + " 行：" + text);
        }

        internal static decimal Dec(string text)
        {
            decimal value;
            if (decimal.TryParse((text ?? "").Trim(), NumberStyles.Number, CultureInfo.InvariantCulture, out value))
            {
                // 去掉 Udt_QTY 的尾零，JSON 里写 2 而不是 2.000000。
                return decimal.Parse(value.ToString("0.######", CultureInfo.InvariantCulture), CultureInfo.InvariantCulture);
            }
            return 0m;
        }
    }
}
