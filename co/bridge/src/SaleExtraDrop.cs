using System.Collections.Generic;

namespace U8Co
{
    // 删除销售单据时一并删掉它的扩展自定义项行（<表>_extradefine，表头按单据主键、表体按行主键）。
    // U8 组件的 Delete 是否连带删除扩展表未经验证；桥在同一事务里：Delete 之前记下表体行主键（行这时还在），
    // Delete 成功之后按记下的主键删扩展表行，之后照常提交。U8 已经删过时是 0 行，不报错；U8 拒绝时整个事务回滚；
    // 预演时随事务回滚。账套没有扩展表时什么也不做。表名、列名只来自 SaleExtraRead.Tables 并加方括号，id 走参数。
    // 现用于发货单删除和蓝字销售发票删除（SaleEdit.RunGone）。
    internal sealed class SaleExtraDrop
    {
        internal string[] Table;
        internal int Id;
        internal List<int> Lines = new List<int>();

        // Delete 之前（事务里）。类型不在 SaleExtraRead.Tables 里返回 null。
        internal static SaleExtraDrop Capture(object conn, string kindName, int id)
        {
            string[] t = SaleExtraRead.FindName(kindName);
            if (t == null || id <= 0)
            {
                return null;
            }
            SaleExtraDrop drop = new SaleExtraDrop();
            drop.Table = t;
            drop.Id = id;
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql(t), new object[] { id }, 5000);
            for (int i = 0; i < rows.Count; i++)
            {
                int line = CoRows.AsId(CoRows.Col(rows[i], t[5]));
                if (line > 0)
                {
                    drop.Lines.Add(line);
                }
            }
            return drop;
        }

        // Delete 成功之后、提交之前（同一事务）。
        internal void Run(object conn)
        {
            if (SaleExtraRead.Exists(conn, Table[1]))
            {
                GlSql.Exec(conn, HeadSql(Table), new object[] { Id });
            }
            if (Lines.Count == 0 || !SaleExtraRead.Exists(conn, Table[4]))
            {
                return;
            }
            for (int i = 0; i < Lines.Count; i++)
            {
                GlSql.Exec(conn, LineSql(Table), new object[] { Lines[i] });
            }
        }

        // 纯拼接（--selftest 用）。t 是 SaleExtraRead.Tables 的一行 {类型, 表头扩展表, 表头主键, 表体表, 表体扩展表, 表体主键, 表体外键}。
        internal static string LinesSql(string[] t)
        {
            return "SELECT " + Q(t[5]) + " FROM " + Q(t[3]) + " WHERE " + Q(t[6]) + "=?";
        }

        internal static string HeadSql(string[] t)
        {
            return "DELETE FROM " + Q(t[1]) + " WHERE " + Q(t[2]) + "=?";
        }

        internal static string LineSql(string[] t)
        {
            return "DELETE FROM " + Q(t[4]) + " WHERE " + Q(t[5]) + "=?";
        }

        static string Q(string name)
        {
            return "[" + name + "]";
        }
    }
}
