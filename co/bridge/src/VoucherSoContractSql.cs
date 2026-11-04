using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单合同号回写的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 SO_SOMain 表头，查权限、锁定人、长度、expect，
    // 预演到此回滚，否则 UPDATE cDefine1（及 cDefine2）和修改人 / 修改日期（列存在才写），条件 ufts 仍是读到的值，回读核对。
    // 只动 SO_SOMain 这一行：不碰 SO_SODetails、DispatchList、SaleBillVouch、各 History 表、金额、审核、关闭。
    internal static class VoucherSoContractSql
    {
        const string HeadSql = "SELECT LTRIM(RTRIM(ISNULL(cSOCode,''))) AS code, cDefine1 AS c1, cDefine2 AS c2,"
            + " LTRIM(RTRIM(ISNULL(cLocker,''))) AS locker,"
            + " LTRIM(RTRIM(ISNULL(cCusCode,''))) AS cCusCode, LTRIM(RTRIM(ISNULL(cDepCode,''))) AS cDepCode,"
            + " LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS cPersonCode, LTRIM(RTRIM(ISNULL(cSTCode,''))) AS cSTCode,"
            + " CONVERT(varchar(20), CONVERT(bigint, ufts)) AS ts FROM SO_SOMain WITH (UPDLOCK, ROWLOCK) WHERE ID=?";
        // 列宽（nvarchar 按字符）与表头自定义项设置的长度（UserDef_Base.iLength，cClass 单据头、cDicDbName 为列名），
        // 以及修改人类列是否存在。max_length=-1（max）按不限。
        const string ColSql = "SELECT c.name AS col,"
            + " CASE WHEN c.max_length < 0 THEN 0 WHEN TYPE_NAME(c.system_type_id) IN ('nchar','nvarchar') THEN c.max_length/2"
            + " ELSE c.max_length END AS col_len, ISNULL(u.iLength, 0) AS def_len FROM sys.columns c"
            + " OUTER APPLY (SELECT TOP 1 b.iLength FROM UserDef_Base b WHERE b.cClass=N'单据头' AND b.cDicDbName=c.name) u"
            + " WHERE c.object_id=OBJECT_ID(N'SO_SOMain')"
            + " AND c.name IN ('cDefine1','cDefine2','cModifier','dModDate','dmodifysystime')";
        const string KeySql = " WHERE ID=? AND ufts=CONVERT(binary(8), CONVERT(bigint, ?))";

        public static ApiResult Run(WorkContext ctx, SoContractAsk ask)
        {
            return ArcCustomerRow.Transact(ctx, "so_contract sale_order " + ask.Id.ToString(CultureInfo.InvariantCulture),
                delegate(object conn) { return Apply(conn, ctx, ask); });
        }

        static ApiResult Apply(object conn, WorkContext ctx, SoContractAsk ask)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { ask.Id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "销售订单不存在");
            }
            VoucherSoContract.Permit(ctx, head);
            SoContractSnap snap = Snap(head);
            VoucherSoContract.CheckLocker(snap.Locker, User(ctx));
            Dictionary<string, SoColumn> cols = Columns(conn);
            CheckLengths(ask, cols);
            SoContractPlan plan = VoucherSoContract.Decide(ask, snap);
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, VoucherSoContract.DryBody(ask, snap, plan.Changed));
            }
            if (!plan.Changed)
            {
                return ApiResult.Ok(VoucherSoContract.Body(ask, snap, false));
            }
            Write(conn, ctx, ask, plan, cols, snap.Ts);
            SoContractSnap after = Confirm(conn, ask, plan, snap);
            return ApiResult.Ok(VoucherSoContract.Body(ask, after, true));
        }

        internal static SoContractSnap Snap(Dictionary<string, object> head)
        {
            SoContractSnap snap = new SoContractSnap();
            snap.Code = GlSql.Col(head, "code");
            snap.Contract = Raw(head, "c1");
            snap.Customer = Raw(head, "c2");
            snap.Locker = GlSql.Col(head, "locker");
            snap.Ts = GlSql.Col(head, "ts");
            return snap;
        }

        // 原值：NULL（行里缺席）为 null，其余原样（含空串、首尾空白）。
        static string Raw(Dictionary<string, object> row, string name)
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

        static Dictionary<string, SoColumn> Columns(object conn)
        {
            Dictionary<string, SoColumn> map = new Dictionary<string, SoColumn>(StringComparer.OrdinalIgnoreCase);
            List<Dictionary<string, object>> rows = Rows.Query(conn, ColSql, new object[0], 10);
            for (int i = 0; i < rows.Count; i++)
            {
                SoColumn c = new SoColumn();
                c.Name = GlSql.Col(rows[i], "col");
                c.Limit = Limit(GlSql.Int(rows[i], "col_len"), GlSql.Int(rows[i], "def_len"));
                map[c.Name] = c;
            }
            if (!map.ContainsKey("cDefine1") || !map.ContainsKey("cDefine2"))
            {
                throw new BridgeException(500, "internal", "SO_SOMain 缺少 cDefine1 / cDefine2 列");
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

        static void CheckLengths(SoContractAsk ask, Dictionary<string, SoColumn> cols)
        {
            VoucherSoContract.CheckLength(ask.ContractNo, cols["cDefine1"].Limit, VoucherSoContract.ContractKey);
            VoucherSoContract.CheckLength(ask.CustomerNo, cols["cDefine2"].Limit, VoucherSoContract.CustomerKey);
        }

        // UPDATE 片段：要写的自定义项，再加存在的修改人（操作员姓名）、修改日期（登录日期）、修改时间。
        internal static string SetClause(SoContractPlan plan, ICollection<string> present, List<string> names)
        {
            List<string> parts = new List<string>();
            if (plan.SetContract)
            {
                parts.Add("cDefine1=?");
                names.Add("c1");
            }
            if (plan.SetCustomer)
            {
                parts.Add("cDefine2=?");
                names.Add("c2");
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

        static void Write(object conn, WorkContext ctx, SoContractAsk ask, SoContractPlan plan,
            Dictionary<string, SoColumn> cols, string ts)
        {
            List<string> names = new List<string>();
            string set = SetClause(plan, cols.Keys, names);
            List<object> args = new List<object>();
            for (int i = 0; i < names.Count; i++)
            {
                args.Add(Arg(names[i], ctx, ask));
            }
            args.Add(ask.Id);
            args.Add(ts);
            GlSql.Exec(conn, "UPDATE SO_SOMain SET " + set + KeySql, args.ToArray());
        }

        static object Arg(string name, WorkContext ctx, SoContractAsk ask)
        {
            if (name == "c1")
            {
                return ask.ContractNo;
            }
            if (name == "c2")
            {
                return ask.CustomerNo;
            }
            if (name == "who")
            {
                return ArcCustomerRow.Modifier(ctx);
            }
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            return date.Length > 0 ? date : DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // 同一事务内重读表头：写了的列必须等于请求值。不符时 ufts 没变是别人在中间改了（409，没写），变了是结果未知（504）。
        static SoContractSnap Confirm(object conn, SoContractAsk ask, SoContractPlan plan, SoContractSnap before)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { ask.Id });
            SoContractSnap after = head == null ? null : Snap(head);
            if (after != null && Written(ask, plan, after))
            {
                return after;
            }
            if (after != null && after.Ts == before.Ts)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单 " + before.Code + " 已被他人修改");
            }
            throw new BridgeException(504, "outcome_unknown",
                "销售订单 " + ask.Id.ToString(CultureInfo.InvariantCulture) + " 已写入合同号，但回读与写入不一致，结果未知");
        }

        internal static bool Written(SoContractAsk ask, SoContractPlan plan, SoContractSnap after)
        {
            bool ok1 = !plan.SetContract || string.Equals((after.Contract ?? "").Trim(), ask.ContractNo, StringComparison.Ordinal);
            bool ok2 = !plan.SetCustomer || string.Equals((after.Customer ?? "").Trim(), ask.CustomerNo, StringComparison.Ordinal);
            return ok1 && ok2;
        }
    }

    internal sealed class SoColumn
    {
        public string Name;
        public int Limit;
    }
}
