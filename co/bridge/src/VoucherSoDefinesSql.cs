using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单表头自定义项写入的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 SO_SOMain 表头，查权限、锁定人、列类型与长度、expect，
    // 预演到此回滚，否则只 UPDATE 有变化的请求列和修改人 / 修改日期 / 修改时间（列存在才写），条件 ufts 仍是读到的值，回读核对。
    // 只动 SO_SOMain 这一行；cascade=true 时订单写完后在同一事务里由 VoucherSoDefinesDown 改下游单据表头的请求列（预演只判断不写）。
    // 列名只来自已校验的列键（VoucherSoDefines.Column），调用方的值只进 ADO 参数。
    internal static class VoucherSoDefinesSql
    {
        // 列目录查询的公共片段：是否文本列（char / nchar / varchar / nvarchar），列宽（nvarchar 按字符，max 按 0 不限）。
        internal const string TextExpr = "CASE WHEN TYPE_NAME(c.system_type_id) IN ('char','nchar','varchar','nvarchar')"
            + " THEN 1 ELSE 0 END AS txt";
        internal const string LenExpr = "CASE WHEN c.max_length < 0 THEN 0 WHEN TYPE_NAME(c.system_type_id) IN ('nchar','nvarchar')"
            + " THEN c.max_length/2 ELSE c.max_length END AS col_len";
        // 'cDefine1', … 'cDefine16'。
        internal static readonly string DefineList = BuildDefineList();
        // SO_SOMain 的自定义项列（类型、列宽、表头自定义项设置的长度 UserDef_Base.iLength，cClass 单据头、cDicDbName 为列名），
        // 以及修改人类列是否存在。
        static readonly string ColSql = "SELECT c.name AS col, " + TextExpr + ", " + LenExpr + ", ISNULL(u.iLength, 0) AS def_len"
            + " FROM sys.columns c"
            + " OUTER APPLY (SELECT TOP 1 b.iLength FROM UserDef_Base b WHERE b.cClass=N'单据头' AND b.cDicDbName=c.name) u"
            + " WHERE c.object_id=OBJECT_ID(N'SO_SOMain')"
            + " AND c.name IN (" + DefineList + ",'cModifier','dModDate','dmodifysystime')";
        const string KeySql = " WHERE ID=? AND ufts=CONVERT(binary(8), CONVERT(bigint, ?))";

        static string BuildDefineList()
        {
            List<string> names = new List<string>();
            for (int i = 1; i <= VoucherSoDefines.MaxColumn; i++)
            {
                names.Add("'cDefine" + i.ToString(CultureInfo.InvariantCulture) + "'");
            }
            return string.Join(",", names.ToArray());
        }

        public static ApiResult Run(WorkContext ctx, SoDefinesAsk ask)
        {
            return ArcCustomerRow.Transact(ctx, VoucherSoDefines.Op + " sale_order " + ask.Id.ToString(CultureInfo.InvariantCulture),
                delegate(object conn) { return Apply(conn, ctx, ask); });
        }

        static ApiResult Apply(object conn, WorkContext ctx, SoDefinesAsk ask)
        {
            Dictionary<string, SoDefinesColumn> cols = Columns(conn);
            Dictionary<string, object> head = Rows.One(conn, HeadSql(ask.Keys, cols), new object[] { ask.Id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "销售订单不存在");
            }
            VoucherSoDefines.Permit(ctx, head);
            SoDefinesSnap snap = Snap(head, ask.Keys);
            VoucherSoDefines.CheckLocker(snap.Locker, User(ctx));
            CheckColumns(ask, cols);
            SoDefinesPlan plan = VoucherSoDefines.Decide(ask, snap);
            List<SoDownDoc> down = ask.Cascade ? VoucherSoDefinesDown.Plan(conn, ask, User(ctx)) : null;
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, VoucherSoDefinesCascade.WithCascade(VoucherSoDefines.DryBody(ask, snap, plan.Changed), ask, down));
            }
            SoDefinesSnap after = snap;
            if (plan.Changed)
            {
                Write(conn, ctx, ask, plan, cols, snap.Ts);
                after = Confirm(conn, ask, plan, snap, cols);
            }
            if (down != null)
            {
                VoucherSoDefinesDown.Write(conn, ask, down);
            }
            return ApiResult.Ok(VoucherSoDefinesCascade.WithCascade(VoucherSoDefines.Body(ask, after, plan.Changed), ask, down));
        }

        // 表头读句：请求列按列键取别名；表里没有的列取 NULL（随后由 CheckColumns 报 400，不引用不存在的列）。
        internal static string HeadSql(List<string> keys, Dictionary<string, SoDefinesColumn> cols)
        {
            return "SELECT LTRIM(RTRIM(ISNULL(cSOCode,''))) AS code, " + Select(keys, cols)
                + " LTRIM(RTRIM(ISNULL(cLocker,''))) AS locker,"
                + " LTRIM(RTRIM(ISNULL(cCusCode,''))) AS cCusCode, LTRIM(RTRIM(ISNULL(cDepCode,''))) AS cDepCode,"
                + " LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS cPersonCode, LTRIM(RTRIM(ISNULL(cSTCode,''))) AS cSTCode,"
                + " CONVERT(varchar(20), CONVERT(bigint, ufts)) AS ts FROM SO_SOMain WITH (UPDLOCK, ROWLOCK) WHERE ID=?";
        }

        // 「cDefineN AS cdefinen, …, 」；cols 为 null 时不查列是否存在（调用方已核对过）。
        internal static string Select(List<string> keys, Dictionary<string, SoDefinesColumn> cols)
        {
            List<string> parts = new List<string>();
            foreach (string key in keys)
            {
                string col = VoucherSoDefines.Column(key);
                bool present = cols == null || cols.ContainsKey(col);
                parts.Add((present ? col : "NULL") + " AS " + key + ",");
            }
            return string.Join(" ", parts.ToArray());
        }

        internal static SoDefinesSnap Snap(Dictionary<string, object> head, List<string> keys)
        {
            SoDefinesSnap snap = new SoDefinesSnap();
            snap.Code = GlSql.Col(head, "code");
            foreach (string key in keys)
            {
                snap.Values[key] = Raw(head, key);
            }
            snap.Locker = GlSql.Col(head, "locker");
            snap.Ts = GlSql.Col(head, "ts");
            return snap;
        }

        // 原值：NULL（行里缺席）为 null，其余原样（含空串、首尾空白）。
        internal static string Raw(Dictionary<string, object> row, string name)
        {
            object value;
            if (row == null || !row.TryGetValue(name, out value) || value == null)
            {
                return null;
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        // 锁定人比较用操作员姓名（cLocker 存姓名）。
        static string User(WorkContext ctx)
        {
            string user = ctx.OperatorName;
            return user == null ? "" : user.Trim();
        }

        static Dictionary<string, SoDefinesColumn> Columns(object conn)
        {
            Dictionary<string, SoDefinesColumn> map = new Dictionary<string, SoDefinesColumn>(StringComparer.OrdinalIgnoreCase);
            List<Dictionary<string, object>> rows = Rows.Query(conn, ColSql, new object[0], VoucherSoDefines.MaxColumn + 10);
            for (int i = 0; i < rows.Count; i++)
            {
                SoDefinesColumn c = new SoDefinesColumn();
                c.Name = GlSql.Col(rows[i], "col");
                c.Text = GlSql.Int(rows[i], "txt") == 1;
                c.Limit = Limit(GlSql.Int(rows[i], "col_len"), GlSql.Int(rows[i], "def_len"));
                map[c.Name] = c;
            }
            return map;
        }

        // 两个正数取小；0 是不限。
        internal static int Limit(int colLen, int defLen)
        {
            if (colLen <= 0)
            {
                return defLen > 0 ? defLen : 0;
            }
            return defLen > 0 && defLen < colLen ? defLen : colLen;
        }

        // 每个请求列须是 SO_SOMain 的文本列，值不超过它的长度上限：否则 400 bad_request，field 为 fields.cdefineN。
        internal static void CheckColumns(SoDefinesAsk ask, Dictionary<string, SoDefinesColumn> cols)
        {
            foreach (string key in ask.Keys)
            {
                string label = VoucherSoDefines.FieldsKey + "." + key;
                SoDefinesColumn c;
                if (!cols.TryGetValue(VoucherSoDefines.Column(key), out c) || !c.Text)
                {
                    throw BridgeException.BadField(label, label + "：SO_SOMain 没有文本列 " + VoucherSoDefines.Column(key)
                        + "（只能写 char / nchar / varchar / nvarchar 列）");
                }
                VoucherSoDefines.CheckLength(ask.Values[key], c.Limit, label);
            }
        }

        // UPDATE 片段：要写的列（按 keys），再加存在的修改人（操作员姓名）、修改日期（登录日期）、修改时间。
        internal static string SetClause(List<string> keys, ICollection<string> present, List<string> names)
        {
            List<string> parts = new List<string>();
            foreach (string key in keys)
            {
                parts.Add(VoucherSoDefines.Column(key) + "=?");
                names.Add(key);
            }
            if (Has(present, "cModifier"))
            {
                parts.Add("cModifier=?");
                names.Add("who");
            }
            if (Has(present, "dModDate"))
            {
                parts.Add("dModDate=CONVERT(datetime, CONVERT(date, ?, 23))");
                names.Add("date");
            }
            if (Has(present, "dmodifysystime"))
            {
                parts.Add("dmodifysystime=GETDATE()");
            }
            return string.Join(", ", parts.ToArray());
        }

        static bool Has(ICollection<string> present, string name)
        {
            foreach (string col in present)
            {
                if (string.Equals(col, name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static void Write(object conn, WorkContext ctx, SoDefinesAsk ask, SoDefinesPlan plan,
            Dictionary<string, SoDefinesColumn> cols, string ts)
        {
            List<string> names = new List<string>();
            string set = SetClause(plan.Set, cols.Keys, names);
            List<object> args = new List<object>();
            for (int i = 0; i < names.Count; i++)
            {
                args.Add(Arg(names[i], ctx, ask));
            }
            args.Add(ask.Id);
            args.Add(ts);
            GlSql.Exec(conn, "UPDATE SO_SOMain SET " + set + KeySql, args.ToArray());
        }

        static object Arg(string name, WorkContext ctx, SoDefinesAsk ask)
        {
            string value;
            if (ask.Values.TryGetValue(name, out value))
            {
                return value;
            }
            if (name == "who")
            {
                return ArcCustomerRow.Modifier(ctx);
            }
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            return date.Length > 0 ? date : DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // 同一事务内重读表头：写了的列必须等于请求值。不符时 ufts 没变是别人在中间改了（409，没写），变了是结果未知（504）。
        static SoDefinesSnap Confirm(object conn, SoDefinesAsk ask, SoDefinesPlan plan, SoDefinesSnap before,
            Dictionary<string, SoDefinesColumn> cols)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql(ask.Keys, cols), new object[] { ask.Id });
            SoDefinesSnap after = head == null ? null : Snap(head, ask.Keys);
            if (after != null && Written(ask, plan, after))
            {
                return after;
            }
            if (after != null && after.Ts == before.Ts)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单 " + before.Code + " 已被他人修改");
            }
            throw new BridgeException(504, "outcome_unknown", "销售订单 " + ask.Id.ToString(CultureInfo.InvariantCulture)
                + " 已写入表头自定义项，但回读与写入不一致，结果未知");
        }

        internal static bool Written(SoDefinesAsk ask, SoDefinesPlan plan, SoDefinesSnap after)
        {
            foreach (string key in plan.Set)
            {
                string got = VoucherSoDefines.Get(after.Values, key);
                if (!string.Equals((got ?? "").Trim(), ask.Values[key], StringComparison.Ordinal))
                {
                    return false;
                }
            }
            return true;
        }
    }

    // 列目录里的一列：列名、是否文本列、长度上限（0 不限）。
    internal sealed class SoDefinesColumn
    {
        public string Name;
        public bool Text;
        public int Limit;
    }
}
