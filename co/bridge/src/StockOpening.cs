using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库存期初结存单（stock_opening，U8 单据类型 34，卡片 0319，表 rdrecord34 / rdrecords34）。
    // Dispatch.Handle 先问这里：测试账套闸门、存货核算闸门，再换登录日期，最后交给 StockOpeningAdd（新增，U8 官方 EAI 导入）
    // 或 StockCo（删除、审核、弃审）。不能修改（U8 的期初单一张一行，要改就删除后重新录入）。
    // 单据日期固定为库存启用日前一天（同 U8：如启用日为某月 1 日，期初单 dDate 是上月最后一天），请求里的 ddate 不用。
    // 审核后在同一事务里把 dVeriDate 改成单据日期（U8 写的是登录日期，U8 自己的期初单是 dDate，VerifyCheck）。
    // 读取照旧走 VoucherRead → StockCo.Load。没有记账步骤（V18 的期初结存只有审核、弃审）。
    internal static class StockOpening
    {
        internal const string KindName = "stock_opening";
        internal const string StType = "34";
        const string V = "/u8co/v1/vouchers/";
        const string StartSql = "SELECT TOP 1 LEFT(LTRIM(cValue), 10) v FROM AccInformation WHERE cSysID=N'ST' AND cName=N'dSTStartDate'";
        const string IaPostedSql = "SELECT TOP 1 'x' FROM GL_mend WHERE iyear=? AND iperiod=0 AND ISNULL(bflag_IA,0)=1";
        const string OwnWhere = " FROM rdrecord34 WHERE ID=? AND cVouchType=N'34' AND bIsSTQc=1";
        internal const string IaPostedText = "存货核算已期初记账，不能修改库存期初结存";
        internal const string TestOnlyText = "期初结存单只对配置为测试账套的账套开放";
        internal const string NoUpdateText = "期初结存单不能修改，请删除后重新录入";
        internal const string StampText = "U8 审核结果与预期不符，已回滚";

        internal static bool Handles(VoucherKind kind)
        {
            return kind != null && kind.Family == "st" && kind.StType == StType;
        }

        public static ApiResult Try(WorkContext ctx, string path)
        {
            if (ctx == null || ctx.Item == null || !Handles(ctx.Item.Type))
            {
                return null;
            }
            WorkItem item = ctx.Item;
            PreLogin(item, path);
            string op = OpOf(path);
            if (op == null)
            {
                return null;
            }
            DateTime start = StartDate(ctx.Conn);
            GuardIa(ctx.Conn, start, op == "verify" ? item.Action : op);
            Dictionary<string, object> head = WithoutDate(item.Head);
            string day = Day(start.AddDays(-1));
            string warning = DateWarning(item.Head, day);
            if (warning != null)
            {
                DryRun.Set("warnings", new List<object> { warning });
            }
            ApiResult result = WithLogin(ctx, LoginDay(ctx, start), delegate { return Run(ctx, op, head, day); });
            return Warn(result, warning);
        }

        static string OpOf(string path)
        {
            if (path == V + "create" || path == V + "delete" || path == V + "verify")
            {
                return path.Substring(V.Length);
            }
            return null;
        }

        static ApiResult Run(WorkContext ctx, string op, Dictionary<string, object> head, string day)
        {
            WorkItem item = ctx.Item;
            VoucherKind kind = item.Type;
            if (op == "create")
            {
                return PuAppRoutes.StampNewId(ctx, StockOpeningAdd.Create(ctx, kind, head, item.Lines, day));
            }
            if (op == "delete")
            {
                return StockCo.Delete(ctx, kind, item.Id);
            }
            return StockCo.Verify(ctx, kind, item.Id, item.Action);
        }

        // 登录前（Requests.ApplyType）和入队后（Try）各查一次：修改一律 400；新增、删除、审核、弃审只对测试账套开放（403）。读取不受限。
        internal static void PreLogin(WorkItem item, string path)
        {
            if (item == null || !Handles(item.Type))
            {
                return;
            }
            if (path == V + "update")
            {
                throw new BridgeException(400, "bad_request", NoUpdateText);
            }
            if (OpOf(path) != null)
            {
                TestAccountGate.Require(item, TestOnlyText);
            }
        }

        // StockCo.Verify 的提交前核对（审核才有，其余类型返回 null）：U8 审核后 dVeriDate 是登录日期，
        // 照 U8 自己的期初单改成单据日期；必须恰好改到本单一行，否则抛出、整笔回滚。
        internal static StockCheck VerifyCheck(VoucherKind kind, int id, bool undo)
        {
            if (undo || !Handles(kind))
            {
                return null;
            }
            return delegate(object conn)
            {
                object[] args = new object[] { id };
                if (Count(conn, "SELECT CONVERT(varchar(10), COUNT(*))" + OwnWhere, args) != 1)
                {
                    throw new BridgeException(409, "u8_rejected", StampText);
                }
                GlSql.Exec(conn, "UPDATE rdrecord34 SET dVeriDate=dDate WHERE ID=? AND cVouchType=N'34' AND bIsSTQc=1", args);
                if (Count(conn, "SELECT CONVERT(varchar(10), COUNT(*))" + OwnWhere + " AND dVeriDate=dDate", args) != 1)
                {
                    throw new BridgeException(409, "u8_rejected", StampText);
                }
            };
        }

        // 给 StockCo.Verify 的 StockAt 挂上 VerifyCheck（只有期初结存的审核才挂，其余原样返回）。
        internal static StockAt WithCheck(StockAt at, VoucherKind kind, int id, bool undo)
        {
            StockCheck check = VerifyCheck(kind, id, undo);
            if (check != null)
            {
                at.After = check;
            }
            return at;
        }

        static int Count(object conn, string sql, object[] args)
        {
            int n;
            string text = Rows.Scalar(conn, sql, args);
            return int.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? n : -1;
        }

        // 库存启用日期（AccInformation ST dSTStartDate）。没有这一项说明库存管理未启用。
        internal static DateTime StartDate(object conn)
        {
            string text = Rows.Scalar(conn, StartSql, new object[0]);
            DateTime day;
            text = text == null ? "" : text.Trim();
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw new BridgeException(409, "state_mismatch", "库存管理未启用，没有库存启用日期");
            }
            return day;
        }

        // 登录日期：一律用库存启用日（第一个库存期间的第一天）。期初日在启用日前一天，可能落在没有 U8 会计期间的年度
        // （启用 2024-01-01 → 2023-12-31）。联调实测后若要改成启用日前一天或沿用请求的 date，只改这里
        // （返回 ctx.Item.Date 即不换登录）。
        static string LoginDay(WorkContext ctx, DateTime start)
        {
            return Day(start);
        }

        // 存货核算在库存启用年度第 0 期已期初记账（GL_mend.bflag_IA=1）时，只放行审核；新增、删除、弃审 409。
        static void GuardIa(object conn, DateTime start, string action)
        {
            if (action == "verify")
            {
                return;
            }
            if (Rows.Scalar(conn, IaPostedSql, new object[] { start.Year }) != null)
            {
                throw new BridgeException(409, "state_mismatch", IaPostedText);
            }
        }

        // 请求登录日期与 LoginDay 不同时，本请求另登录一次（同账套、年度、操作员、子系统），处理完关掉，不放回登录缓存；
        // 请求连接 ctx.Conn 不变（同一个账套库），事务和预演照常挂在它上面。原登录在 finally 里换回，仍由 StaExec 释放。
        static ApiResult WithLogin(WorkContext ctx, string day, Func<ApiResult> run)
        {
            string current = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (day == null || day.Length == 0 || day == current)
            {
                return run();
            }
            WorkItem alt = AltItem(ctx.Item, day);
            string sub = WorkRun.SubOf(ctx.Item);
            U8Session own = ctx.Session;
            U8Session swap;
            // 嵌套登录算在本请求原登录的许可名额里（LicenseHold），maxConcurrentLogins 为 1 时也不会自己挡自己。
            LicenseHold.BeginNested();
            try
            {
                swap = LoginCache.Acquire(alt, sub);
            }
            finally
            {
                LicenseHold.EndNested();
            }
            try
            {
                ctx.Session = swap;
                CoRows.Note(ctx.Item, "login_date=" + day);
                return run();
            }
            finally
            {
                ctx.Session = own;
                LoginCache.Release(swap, alt, sub, false);
            }
        }

        static WorkItem AltItem(WorkItem item, string day)
        {
            WorkItem alt = new WorkItem();
            alt.Config = item.Config;
            alt.Kind = item.Kind;
            alt.Acc = item.Acc;
            alt.Year = item.Year;
            alt.Operator = item.Operator;
            alt.Password = item.Password;
            alt.Date = day;
            alt.ClientIp = item.ClientIp;
            alt.Path = item.Path;
            alt.Route = item.Route;
            alt.Started = item.Started;
            alt.SubId = item.SubId;
            alt.Type = item.Type;
            return alt;
        }

        // 表头去掉 ddate（不改请求本身）；单据日期由 StockOpeningAdd 按库存启用日前一天写进每个 entry。
        static Dictionary<string, object> WithoutDate(Dictionary<string, object> head)
        {
            if (head == null)
            {
                return null;
            }
            Dictionary<string, object> copy = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> kv in head)
            {
                if (!IsDate(kv.Key))
                {
                    copy[kv.Key] = kv.Value;
                }
            }
            return copy;
        }

        // 请求给了与固定日期不同的 ddate：照样忽略，响应带一条提示。
        static string DateWarning(Dictionary<string, object> head, string day)
        {
            if (head == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                if (!IsDate(kv.Key) || kv.Value == null || kv.Value is DBNull)
                {
                    continue;
                }
                string text = Convert.ToString(kv.Value, CultureInfo.InvariantCulture).Trim();
                if (text.Length > 0 && !text.StartsWith(day, StringComparison.Ordinal))
                {
                    return "期初结存单日期固定为库存启用日前一天（" + day + "）";
                }
            }
            return null;
        }

        static bool IsDate(string key)
        {
            return string.Equals(key, "ddate", StringComparison.OrdinalIgnoreCase);
        }

        static ApiResult Warn(ApiResult result, string warning)
        {
            if (warning != null && result != null && result.Body != null)
            {
                result.Body["warnings"] = new List<object> { warning };
            }
            return result;
        }

        static string Day(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }
}
