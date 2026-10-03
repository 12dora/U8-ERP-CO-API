using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库存月末结账 / 取消结账的快照：与 U8 库存月末结账的结果一致（实测核对），写、删 ST_MonthAccount、ST_MonthAccounts、ST_MonthAccountV、
    // ST_MonthAccountVs、ST_MonthAccountCheck 该年该月的行。在请求连接、PeriodClose 的事务里执行，结账标志由调用方改。
    // 返回各表行数 { account, accounts, v, vs, check }：结账是写入的行数，取消结账是删掉的行数。
    internal static class PeriodStock
    {
        const int QtyDigits = 2;
        const int NumDigits = 4;
        const int DigitsMax = 10;
        static readonly string[] Keys = new string[] { "account", "accounts", "v", "vs", "check" };

        public static Dictionary<string, object> Close(object conn, int year, int period)
        {
            int[] prev = PeriodCloseChecks.Shift(year, period, -1);
            int[] digits = Digits(conn);
            UnwriteoffSql.Run(conn, PeriodStockSql.ArgsTable);
            GlSql.Exec(conn, PeriodStockSql.ArgsFill, new object[]
            {
                year, period, prev[0], prev[1], PeriodCloseChecks.Day(year, period), LastDay(year, period), digits[0], digits[1]
            });
            UnwriteoffSql.Run(conn, PeriodStockSql.SrcTable);
            foreach (string type in PeriodStockSql.Types)
            {
                UnwriteoffSql.Run(conn, PeriodStockSql.SrcFill(type));
            }
            UnwriteoffSql.Run(conn, PeriodStockSql.MxTable);
            UnwriteoffSql.Run(conn, PeriodStockSql.Pass(false));
            UnwriteoffSql.Run(conn, PeriodStockSql.Pass(true));
            UnwriteoffSql.Run(conn, PeriodStockSql.CheckPass);
            Dictionary<string, object> expected = Counts(conn, PeriodStockSql.ExpCountsSql, new object[0]);
            UnwriteoffSql.Run(conn, PeriodStockSql.Write);
            Dictionary<string, object> written = Counts(conn, PeriodStockSql.TableCountsSql, PeriodStockSql.CountArgs(year, period));
            UnwriteoffSql.Run(conn, PeriodStockSql.DropTemps());
            if (!Same(expected, written))
            {
                throw new BridgeException(500, "internal", "库存月结快照写入的行数与算出的不符（算出 " + Text(expected)
                    + "，写入 " + Text(written) + "），已回滚");
            }
            return written;
        }

        // 取消结账：先数再删，删完核对已经没有行。
        public static Dictionary<string, object> Reopen(object conn, int year, int period)
        {
            object[] args = PeriodStockSql.CountArgs(year, period);
            Dictionary<string, object> before = Counts(conn, PeriodStockSql.TableCountsSql, args);
            foreach (string table in PeriodStockSql.Tables)
            {
                GlSql.Exec(conn, PeriodStockSql.DeleteOf(table), new object[] { year, period });
            }
            Dictionary<string, object> after = Counts(conn, PeriodStockSql.TableCountsSql, args);
            if (Total(after) != 0)
            {
                throw new BridgeException(500, "internal", "库存月结快照没有删干净（剩 " + Text(after) + "），已回滚");
            }
            return before;
        }

        // { 数量小数位, 件数小数位 }，读不到或不合理按 2 / 4（与 U8 的缺省相同）。
        static int[] Digits(object conn)
        {
            int[] digits = new int[] { QtyDigits, NumDigits };
            List<Dictionary<string, object>> rows = Rows.Query(conn, PeriodStockSql.DigitsSql, new object[0], 10);
            foreach (Dictionary<string, object> row in rows)
            {
                int n;
                string text = CoRows.Col(row, "cValue").Trim();
                if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) || n < 0 || n > DigitsMax)
                {
                    continue;
                }
                digits[CoRows.Col(row, "cName") == "iNumDecDgt" ? 1 : 0] = n;
            }
            return digits;
        }

        static Dictionary<string, object> Counts(object conn, string sql, object[] args)
        {
            Dictionary<string, object> row = Rows.One(conn, sql, args);
            Dictionary<string, object> counts = new Dictionary<string, object>();
            foreach (string key in Keys)
            {
                counts[key] = row == null ? 0 : GlSql.Int(row, "n_" + key);
            }
            return counts;
        }

        // 纯函数，--selftest 用。
        internal static bool Same(Dictionary<string, object> a, Dictionary<string, object> b)
        {
            foreach (string key in Keys)
            {
                if (!a.ContainsKey(key) || !b.ContainsKey(key) || (int)a[key] != (int)b[key])
                {
                    return false;
                }
            }
            return true;
        }

        static int Total(Dictionary<string, object> counts)
        {
            int total = 0;
            foreach (string key in Keys)
            {
                total += (int)counts[key];
            }
            return total;
        }

        static string Text(Dictionary<string, object> counts)
        {
            List<string> parts = new List<string>();
            foreach (string key in Keys)
            {
                parts.Add(key + "=" + PeriodGate.N((int)counts[key]));
            }
            return string.Join(", ", parts.ToArray());
        }

        // 月末那一天（yyyy-MM-dd）：U8 的 @end，与 BETWEEN @start AND @end 配用。纯函数，--selftest 用。
        internal static string LastDay(int year, int period)
        {
            return new DateTime(year, period, 1).AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }
}
