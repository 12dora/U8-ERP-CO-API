using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 约定字段保存前的校验（只读 SQL，请求连接上、事务之外）：列存在、类型与长度按 INFORMATION_SCHEMA，
    // 编码按档案——收付款协议 AA_Agreement（停用日期不晚于登录日期为停用）、收货地址 CusDeliverAdd（须属于单据客户）、
    // 仓库 Warehouse、计量单位 ComputationUnit（须属于存货的计量单位组，单计量存货不收 cunitid / inum）。不符一律 400。
    internal sealed partial class SalePins
    {
        const string CatalogSql = "SELECT LOWER(TABLE_NAME) AS t, LOWER(COLUMN_NAME) AS c, DATA_TYPE AS ty,"
            + " CHARACTER_MAXIMUM_LENGTH AS len FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME IN ({0})";
        const string AgreementSql = "SELECT LTRIM(RTRIM(cCode)) AS code,"
            + " ISNULL(CONVERT(varchar(10), dEDate, 23),'') AS ended FROM AA_Agreement WHERE cCode=?";
        const string AddrSql = "SELECT TOP 1 LTRIM(RTRIM(cAddCode)) AS code FROM CusDeliverAdd WHERE cCusCode=? AND cAddCode=?";
        const string WhSql = "SELECT TOP 1 LTRIM(RTRIM(cWhCode)) AS code FROM Warehouse WHERE cWhCode=?";
        const string UnitSql = "SELECT LTRIM(RTRIM(ISNULL(cGroupCode,''))) AS grp FROM ComputationUnit WHERE cComunitCode=?";
        const string InvSql = "SELECT LTRIM(RTRIM(ISNULL(cGroupCode,''))) AS grp, ISNULL(iGroupType,0) AS gt"
            + " FROM Inventory WHERE cInvCode=?";
        const string OrderCusSql = "SELECT LTRIM(RTRIM(ISNULL(cCusCode,''))) AS cus FROM SO_SOMain WHERE ID=?";
        const string LineInvSql = "SELECT LTRIM(RTRIM(ISNULL(cInvCode,''))) AS inv FROM SO_SODetails WHERE ID=? AND iSOsID=?";
        const string LineIdsSql = "SELECT iSOsID FROM SO_SODetails WHERE ID=?";

        // 销售订单新增。
        internal static SalePins ForOrder(object conn, string date, Dictionary<string, object> head, object[] lines)
        {
            SalePins pins = Collect(Order, head, lines, false);
            pins.Date = date;
            pins.Check(conn);
            return pins;
        }

        // 销售订单修改：客户取请求表头（改客户时）或订单；op=update 行的存货取请求或订单行。
        internal static SalePins ForOrderEdit(object conn, string date, int id, Dictionary<string, object> head, object[] lines)
        {
            SalePins pins = Collect(Order, head, lines, true);
            pins.Date = date;
            pins.HeadId = id;
            if (pins.Customer.Length == 0)
            {
                pins.Customer = Rows.Scalar(conn, OrderCusSql, new object[] { id }) ?? "";
            }
            for (int i = 0; i < pins.Items.Count; i++)
            {
                SalePin pin = pins.Items[i];
                if (pin.Inv.Length == 0 && pin.Key > 0)
                {
                    pin.Inv = Rows.Scalar(conn, LineInvSql, new object[] { id, pin.Key }) ?? "";
                }
            }
            if (pins.Adds > 0)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, LineIdsSql, new object[] { id }, 5000);
                for (int i = 0; i < rows.Count; i++)
                {
                    pins.OldLines.Add(CoRows.AsId(CoRows.Col(rows[i], "iSOsID")));
                }
            }
            pins.Check(conn);
            return pins;
        }

        // 发货单参照销售订单：收货地址须属于订单客户。
        internal static SalePins ForDispatch(object conn, string date, int sourceId, Dictionary<string, object> head)
        {
            SalePins pins = Collect(Dispatch, head, null, false);
            pins.Date = date;
            pins.Customer = Rows.Scalar(conn, OrderCusSql, new object[] { sourceId }) ?? "";
            pins.Check(conn);
            return pins;
        }

        void Check(object conn)
        {
            CheckSupplied(conn);
            if (Items.Count == 0)
            {
                return;
            }
            Dictionary<string, SaleCol> cat = Catalog(conn, Items);
            for (int i = 0; i < Items.Count; i++)
            {
                SalePin pin = Items[i];
                SaleCol col;
                if (!cat.TryGetValue(pin.Table.ToLowerInvariant() + "." + pin.Column.ToLowerInvariant(), out col))
                {
                    throw BridgeException.BadField(pin.Path, Missing(pin));
                }
                pin.Col = col;
                CheckValue(pin);
                CheckCode(conn, pin);
            }
        }

        static string Missing(SalePin pin)
        {
            if (pin.Extra)
            {
                return "账套没有扩展自定义项 " + pin.Column + "（表 " + pin.Table + "）";
            }
            return "账套的 " + pin.Table + " 没有列 " + pin.Column;
        }

        static Dictionary<string, SaleCol> Catalog(object conn, List<SalePin> items)
        {
            List<object> args = new List<object>();
            List<string> marks = new List<string>();
            for (int i = 0; i < items.Count; i++)
            {
                if (!args.Contains(items[i].Table))
                {
                    args.Add(items[i].Table);
                    marks.Add("?");
                }
            }
            string sql = string.Format(CultureInfo.InvariantCulture, CatalogSql, string.Join(",", marks.ToArray()));
            Dictionary<string, SaleCol> cat = new Dictionary<string, SaleCol>(StringComparer.Ordinal);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args.ToArray(), 2000);
            for (int i = 0; i < rows.Count; i++)
            {
                SaleCol col = new SaleCol();
                col.Type = CoRows.Col(rows[i], "ty").ToLowerInvariant();
                col.Kind = KindOf(col.Type);
                int len;
                col.Len = int.TryParse(CoRows.Col(rows[i], "len"), NumberStyles.Integer, CultureInfo.InvariantCulture, out len) ? len : 0;
                cat[CoRows.Col(rows[i], "t") + "." + CoRows.Col(rows[i], "c")] = col;
            }
            return cat;
        }

        internal static string KindOf(string type)
        {
            string t = "," + (type ?? "").Trim().ToLowerInvariant() + ",";
            if (",char,varchar,nchar,nvarchar,text,ntext,".IndexOf(t, StringComparison.Ordinal) >= 0)
            {
                return "text";
            }
            if (",int,smallint,tinyint,bigint,decimal,numeric,float,real,money,smallmoney,".IndexOf(t, StringComparison.Ordinal) >= 0)
            {
                return "number";
            }
            if (",datetime,smalldatetime,date,datetime2,".IndexOf(t, StringComparison.Ordinal) >= 0)
            {
                return "date";
            }
            return "";
        }

        // 类型与长度（文本按字符；-1 / 0 不限）。空值是清空，不查。
        internal static void CheckValue(SalePin pin)
        {
            string v = pin.Value;
            if (v.Length == 0)
            {
                return;
            }
            if (pin.Col.Kind == "text")
            {
                if (pin.Col.Len > 0 && v.Length > pin.Col.Len)
                {
                    throw BridgeException.BadField(pin.Path,
                        pin.Field + " 超长：最多 " + pin.Col.Len.ToString(CultureInfo.InvariantCulture) + " 个字符");
                }
                return;
            }
            if (pin.Col.Kind == "number")
            {
                decimal n;
                // 与计算路径同一解析（SoUnits.Dec，认指数，如件数 "1E+01"）。
                if (!SoUnits.Dec(v, out n))
                {
                    throw BridgeException.BadField(pin.Path, pin.Field + " 必须是数值");
                }
                return;
            }
            if (pin.Col.Kind == "date")
            {
                CheckDate(pin);
                return;
            }
            throw BridgeException.BadField(pin.Path, pin.Field + " 的列类型不支持");
        }

        // 写入用 CONVERT(date, ?, 23) 再按列类型转换（ValueExpr），只收 yyyy-MM-dd（不带时间），且须在列类型的日期范围内。
        static void CheckDate(SalePin pin)
        {
            DateTime d;
            if (!DateTime.TryParseExact(pin.Value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
            {
                throw BridgeException.BadField(pin.Path, pin.Field + " 必须是 yyyy-MM-dd 日期");
            }
            DateTime min = DateTime.MinValue;
            DateTime max = DateTime.MaxValue;
            if (pin.Col.Type == "datetime")
            {
                min = new DateTime(1753, 1, 1);
            }
            else if (pin.Col.Type == "smalldatetime")
            {
                min = new DateTime(1900, 1, 1);
                max = new DateTime(2079, 6, 6);
            }
            if (d < min || d > max)
            {
                throw BridgeException.BadField(pin.Path, pin.Field + " 超出 " + pin.Col.Type + " 列的日期范围");
            }
        }

        internal static bool ParseDate(string text, out DateTime value)
        {
            return DateTime.TryParseExact(text.Trim(), new string[] { "yyyy-MM-dd", "yyyy-MM-dd HH:mm:ss" },
                CultureInfo.InvariantCulture, DateTimeStyles.None, out value);
        }

        void CheckCode(object conn, SalePin pin)
        {
            if (pin.Value.Length == 0)
            {
                return;
            }
            if (pin.Field == "cgatheringplan")
            {
                Agreement(conn, pin);
            }
            else if (pin.Field == "caddcode")
            {
                Address(conn, pin);
            }
            else if (pin.Field == "cwhcode" && Rows.Scalar(conn, WhSql, new object[] { pin.Value }) == null)
            {
                throw BridgeException.BadField(pin.Path, "仓库不存在：" + pin.Value);
            }
            else if (pin.Field == "cunitid" || pin.Field == "inum")
            {
                Unit(conn, pin);
            }
        }

        void Agreement(object conn, SalePin pin)
        {
            Dictionary<string, object> row = Rows.One(conn, AgreementSql, new object[] { pin.Value });
            if (row == null)
            {
                throw BridgeException.BadField(pin.Path, "收付款协议不存在：" + pin.Value);
            }
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && Date.Length > 0 && string.CompareOrdinal(ended, Date) <= 0)
            {
                throw BridgeException.BadField(pin.Path, "收付款协议已停用：" + pin.Value);
            }
        }

        void Address(object conn, SalePin pin)
        {
            if (Customer.Length == 0)
            {
                throw BridgeException.BadField(pin.Path, "设置收货地址编码需要同时给客户 ccuscode");
            }
            if (Rows.Scalar(conn, AddrSql, new object[] { Customer, pin.Value }) == null)
            {
                throw BridgeException.BadField(pin.Path, "收货地址编码 " + pin.Value + " 不是客户 " + Customer + " 的收货地址");
            }
        }

        static void Unit(object conn, SalePin pin)
        {
            Dictionary<string, object> inv = pin.Inv.Length == 0 ? null : Rows.One(conn, InvSql, new object[] { pin.Inv });
            if (inv != null && CoRows.Col(inv, "gt") == "0")
            {
                throw BridgeException.BadField(pin.Path, "存货 " + pin.Inv + " 是单计量，不能设置 " + pin.Field);
            }
            if (pin.Field != "cunitid")
            {
                return;
            }
            string grp = Rows.Scalar(conn, UnitSql, new object[] { pin.Value });
            if (grp == null)
            {
                throw BridgeException.BadField(pin.Path, "计量单位不存在：" + pin.Value);
            }
            if (inv != null && !string.Equals(grp, CoRows.Col(inv, "grp"), StringComparison.OrdinalIgnoreCase))
            {
                throw BridgeException.BadField(pin.Path, "计量单位 " + pin.Value + " 不属于存货 " + pin.Inv + " 的计量单位组");
            }
        }
    }
}
