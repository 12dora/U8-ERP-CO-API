using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 销售出库改批号时的批次属性（未经实测）：换了批号的行（含克隆行）先清掉跟批号走的列
    // （批次属性 cBatchProperty1..10、生产日期、失效日期、保质期、有效期推算），再按批次属性档案 AA_BatchProperty
    // （存货、批号、自由项）填新批号的属性；档案里没有该批号就留空。保质期管理的存货不能指定批号（预检已拒绝），
    // 所以日期类列清空即可。提交前核对：指定了批号的发货行，其出库行的批次属性与档案一致（propok）。
    internal static partial class StockGen
    {
        const int LotPropCount = 10;

        static readonly string[] LotCols = BuildLotCols();
        static readonly string LotPropSql = BuildLotPropSql();

        static string[] BuildLotCols()
        {
            List<string> cols = new List<string>();
            for (int i = 1; i <= LotPropCount; i++)
            {
                cols.Add("cBatchProperty" + i.ToString(CultureInfo.InvariantCulture));
            }
            cols.AddRange(new string[]
            {
                "dMadeDate", "dVDate", "iMassDate", "cMassUnit", "cExpirationdate", "dExpirationdate", "iExpiratDateCalcu"
            });
            return cols.ToArray();
        }

        // 1..5 是数，6..9 是文本，10 是日期（按 DOM 的 yyyy-mm-ddThh:mi:ss 写）。
        static string LotPropExpr(int n)
        {
            string col = "cBatchProperty" + n.ToString(CultureInfo.InvariantCulture);
            if (n == LotPropCount)
            {
                return "convert(varchar(19), " + col + ", 126) as p" + n.ToString(CultureInfo.InvariantCulture);
            }
            return "convert(nvarchar(120), " + col + ") as p" + n.ToString(CultureInfo.InvariantCulture);
        }

        static string BuildLotPropSql()
        {
            StringBuilder sql = new StringBuilder("select top 1 ");
            for (int i = 1; i <= LotPropCount; i++)
            {
                sql.Append(i > 1 ? ", " : "").Append(LotPropExpr(i));
            }
            sql.Append(" from AA_BatchProperty where cInvCode=? and cBatch=?").Append(FreeWhere());
            return sql.ToString();
        }

        // 行上的存货、自由项 + 新批号 → 属性列名 → 值（只含非空的）；档案里没有返回空表。
        static Dictionary<string, string> LotProps(object conn, object row, string batch)
        {
            List<object> args = new List<object>();
            args.Add(DomRows.Get(row, "cInvCode").Trim());
            args.Add(batch);
            for (int i = 1; i <= FreeCount; i++)
            {
                args.Add(DomRows.Get(row, "cFree" + i.ToString(CultureInfo.InvariantCulture)).Trim());
            }
            Dictionary<string, object> found = Rows.One(conn, LotPropSql, args.ToArray());
            Dictionary<string, string> props = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 1; found != null && i <= LotPropCount; i++)
            {
                string n = i.ToString(CultureInfo.InvariantCulture);
                string value = CoRows.Col(found, "p" + n);
                if (value.Length > 0)
                {
                    props["cBatchProperty" + n] = value;
                }
            }
            return props;
        }

        // OutRowsSql 的 propok 列：没有批号 1；与档案一致 1；档案没有该批号且行上属性全空 1；否则 0。
        static string PropOkExpr()
        {
            StringBuilder key = new StringBuilder("a.cInvCode=b.cInvCode and a.cBatch=b.cBatch");
            for (int i = 1; i <= FreeCount; i++)
            {
                string f = "cFree" + i.ToString(CultureInfo.InvariantCulture);
                key.Append(" and isnull(a." + f + ",'')=isnull(b." + f + ",'')");
            }
            StringBuilder eq = new StringBuilder();
            StringBuilder empty = new StringBuilder();
            for (int i = 1; i <= LotPropCount; i++)
            {
                string p = "cBatchProperty" + i.ToString(CultureInfo.InvariantCulture);
                bool text = i >= 6 && i <= 9;
                eq.Append(" and ").Append(text ? "isnull(a." + p + ",'')=isnull(b." + p + ",'')"
                    : "(a." + p + "=b." + p + " or (a." + p + " is null and b." + p + " is null))");
                empty.Append(" and ").Append(text ? "isnull(b." + p + ",'')=''" : "b." + p + " is null");
            }
            return "case when isnull(b.cBatch,'')='' then 1"
                + " when exists (select 1 from AA_BatchProperty a where " + key + eq + ") then 1"
                + " when not exists (select 1 from AA_BatchProperty a where " + key + ")" + empty + " then 1"
                + " else 0 end";
        }
    }
}
