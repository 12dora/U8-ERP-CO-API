using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 只读档案 fa_card（固定资产卡片，U8 表 fa_Cards）：纯 SQL，只用 ctx.Conn。表名列名都是常量，调用方的文本只进 ? 参数。
    // 编码是卡片编号 sCardNum（资产编号 sAssetNum 在 asset_num）。U8 按变动保存卡片版本：每次变动、减少都新增一行
    // （sCardID 递增），变动行的 dTransDate、减少行的 dDisposeDate 是所在期间的月末。
    // 取数口径（同 U8 视图 fa_Q_YXKP_NEW）：截至日 = 登录日期所在月的月末，取 dInputDate、dTransDate、dDisposeDate
    // 都不晚于截至日（后两者可空）的 MAX(sCardID)；截至日还没录入的卡片不存在（404）。该版本有 dDisposeDate 即已减少。
    // 累计折旧取 fa_DeprTransactions（按卡片、年度一行）：已计提期间 P = fa_DeprList 在登录年度、不晚于登录月的最大期间，
    // 取 dblDeprT<P>（P 期末累计）；本年还没计提（P 为 0）或卡片在 P 期及以后才录入、dblDeprT<P> 为 0 时，
    // 取期初：年初结转的卡片（iInputPeriod 0）是 dblDeprT0，本年录入的是 dblInputDeprTotal。已减少的卡片取减少行的
    // dblDecDeprT（减少时的累计折旧）。登录年度没有折旧行（固定资产还没建这一年）时为 null。
    // 净值 = 原值 - 累计折旧 - 减值准备（dblDecPreValueT）。各期的原值、累计折旧合计与 U8 的 fa_Total 一致（已在测试账套核对）。
    // 未覆盖：会计期间不按自然月划分的账套；多部门卡片的 ratio（fa_DeptScale.dblScale）是小数还是百分数。
    // fa_Cards 没有 rowversion：列表不支持 changed_since，watermark 为 null。数据权限：功能权限之外，部门开了数据权限
    // 控制时按卡片使用部门过滤（同固定资产报表，ReportsFaPerm：卡片全部版本的全部使用部门都在授权内才给）。
    internal static class ArcFa
    {
        internal const string Name = "fa_card";
        // 卡片编号 fa_Cards.sCardNum nvarchar(20)；类别编码 sTypeNum nvarchar(40) 按 20 个字符；部门 cDepCode nvarchar(24) 按 12。
        internal const int CodeMax = 20;
        internal const int TypeMax = 20;
        internal const int DeptMax = 12;

        internal const string DeprPeriodSql = "SELECT MAX(iPeriod) AS p FROM fa_DeprList WHERE iyear=? AND iPeriod<=?";
        const string Cols = "h.sCardID AS card_id, h.sCardNum AS code, h.sAssetNum AS asset_num, h.sAssetName AS name,"
            + " h.sStyle AS spec, h.sTypeNum AS type_code, t.sName AS type_name, h.sStatusID AS status_code, s.sName AS status,"
            + " h.sOrgAddID AS origin_code, o.sName AS origin, h.sDeprMethodID AS depreciation_method_code,"
            + " m.sName AS depreciation_method, h.dStartdate AS start_date, h.dInputDate AS entry_date,"
            + " h.lLife AS useful_life_months, h.lUsedMonths AS used_months, h.dDisposeDate AS disposed_date,"
            + " h.sOrgDisposeID AS disposal_code, x.sName AS disposal_way, h.sKeeper AS keeper, h.sSite AS location";
        // 截至日取当时的版本：三个 ? 都是截至日。
        const string Versions = "(SELECT v.sCardNum AS num, MAX(v.sCardID) AS id FROM fa_Cards v"
            + " WHERE v.dInputDate <= CONVERT(date, ?, 23)"
            + " AND (v.dTransDate IS NULL OR v.dTransDate <= CONVERT(date, ?, 23))"
            + " AND (v.dDisposeDate IS NULL OR v.dDisposeDate <= CONVERT(date, ?, 23)) GROUP BY v.sCardNum) cur";
        const string Lookups = " LEFT JOIN fa_AssetTypes t ON t.sNum = h.sTypeNum LEFT JOIN fa_Status s ON s.sID = h.sStatusID"
            + " LEFT JOIN fa_Origins o ON o.sID = h.sOrgAddID LEFT JOIN fa_Origins x ON x.sID = h.sOrgDisposeID"
            + " LEFT JOIN fa_Depreciations m ON m.sID = h.sDeprMethodID";
        // 第一个 ? 是已计提期间 P（选 dblDeprT<P>），第二个也是 P（录入期间比较）。
        const string AccApply = " CROSS APPLY (SELECT CASE WHEN h.dDisposeDate IS NOT NULL THEN h.dblDecDeprT"
            + " WHEN d.sCardNum IS NULL THEN NULL"
            + " WHEN d.iInputPeriod >= ? AND e.tp = 0 THEN CASE WHEN d.iInputPeriod = 0 THEN d.dblDeprT0 ELSE d.dblInputDeprTotal END"
            + " ELSE e.tp END AS acc) a";
        const string DeptSql = "SELECT h.sCardID AS card_id, sc.sDeptNum AS code, dep.cDepName AS name, sc.dblScale AS ratio"
            + " FROM fa_Cards h JOIN fa_DeptScale sc ON sc.sCardNum = h.sCardNum AND sc.lOptID = h.lOptID"
            + " LEFT JOIN Department dep ON dep.cDepCode = sc.sDeptNum WHERE h.sCardID IN (";

        internal static bool Is(ArcKind kind)
        {
            return kind != null && kind.Name == Name;
        }

        // 列表条件（ArcReq.ParseList 调用）：type_code 含下级类别（前缀），dept_code 是使用部门之一，include_disposed 缺省 false。
        internal static void ParseList(ArcReq req, string type, string dept, object disposed)
        {
            if (!Is(req.Kind))
            {
                if (type != null || dept != null || disposed != null)
                {
                    throw ArcReq.Bad("只有 fa_card 支持 type_code、dept_code、include_disposed");
                }
                return;
            }
            if (disposed != null && !(disposed is bool))
            {
                throw ArcReq.Bad("include_disposed 必须是布尔");
            }
            req.TypeCode = type == null ? null : ArcReq.CheckCode(type, "type_code", TypeMax);
            req.DeptCode = dept == null ? null : ArcReq.CheckCode(dept, "dept_code", DeptMax);
            req.IncludeDisposed = disposed != null && (bool)disposed;
        }

        // get 按卡片编号读截至日的版本，已减少的卡片照常返回（disposed 为 true）。
        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            FaScope scope = FaScope.Of(ctx);
            List<object> args = new List<object>();
            StringBuilder sql = Select(scope, args, 1);
            sql.Append(" AND h.sCardNum=?");
            args.Add(req.Code);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), 1);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            PermHook.Archive(ctx, req.Code);
            ReportsFaPerm.CheckCard(ctx, req.Code);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["fields"] = Items(ctx.Conn, rows, 1)[0];
            scope.Put(body);
            return ApiResult.Ok(body);
        }

        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            FaScope scope = FaScope.Of(ctx);
            List<object> args = new List<object>();
            StringBuilder sql = Select(scope, args, req.Limit + 1);
            if (!req.IncludeDisposed)
            {
                sql.Append(" AND h.dDisposeDate IS NULL");
            }
            ArcRead.AddFilter(sql, args, " AND h.sCardNum > ?", req.After);
            ArcRead.AddFilter(sql, args, " AND h.sCardNum LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND h.sAssetName LIKE ? ESCAPE '\\'", ArcRead.Like(req.NameLike, true));
            ArcRead.AddFilter(sql, args, " AND h.sTypeNum LIKE ? ESCAPE '\\'", ArcRead.Like(req.TypeCode, false));
            ArcRead.AddFilter(sql, args, " AND EXISTS (SELECT 1 FROM fa_DeptScale f WHERE f.sCardNum = h.sCardNum"
                + " AND f.lOptID = h.lOptID AND f.sDeptNum = ?)", req.DeptCode);
            PermHook.Where(sql, args, ctx, "h");
            ReportsFaPerm.AppendCard(sql, args, ReportsFaPerm.Of(ctx), "h.sCardNum");
            sql.Append(" ORDER BY h.sCardNum");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), req.Limit + 1);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["items"] = Items(ctx.Conn, rows, req.Limit);
            body["next"] = rows.Count > req.Limit ? ArcRead.Cell(rows[req.Limit - 1], "code") : null;
            body["watermark"] = null;
            scope.Put(body);
            return ApiResult.Ok(body);
        }

        // SELECT TOP (?) … WHERE 1=1；? 的顺序：TOP、三个截至日、年度、两个 P。
        static StringBuilder Select(FaScope scope, List<object> args, int top)
        {
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(Cols);
            sql.Append(", ").Append(Money("h.dblValue")).Append(" AS original_value, ").Append(Money("a.acc"));
            sql.Append(" AS accumulated_depreciation, ").Append(Money("ISNULL(h.dblDecPreValueT, 0)")).Append(" AS impairment, ");
            sql.Append(Money("h.dblValue - a.acc - ISNULL(h.dblDecPreValueT, 0)")).Append(" AS net_value, ");
            sql.Append(Money("h.dblBV")).Append(" AS net_salvage FROM ").Append(Versions);
            sql.Append(" JOIN fa_Cards h ON h.sCardID = cur.id");
            sql.Append(" LEFT JOIN fa_DeprTransactions d ON d.sCardNum = h.sCardNum AND d.iyear = ?");
            sql.Append(" CROSS APPLY (SELECT ").Append(PeriodCase()).Append(" AS tp) e").Append(AccApply);
            sql.Append(Lookups).Append(" WHERE 1=1");
            args.Add(top);
            args.Add(scope.AsOf);
            args.Add(scope.AsOf);
            args.Add(scope.AsOf);
            args.Add(scope.Year);
            args.Add(scope.Period);
            args.Add(scope.Period);
            return sql;
        }

        // CASE ? WHEN 0 THEN d.dblDeprT0 … WHEN 12 THEN d.dblDeprT12 END：列名来自常量 0–12，期间只进参数。
        static string PeriodCase()
        {
            StringBuilder sb = new StringBuilder("CASE ?");
            for (int i = 0; i <= 12; i++)
            {
                string n = i.ToString(CultureInfo.InvariantCulture);
                sb.Append(" WHEN ").Append(n).Append(" THEN d.dblDeprT").Append(n);
            }
            return sb.Append(" END").ToString();
        }

        static string Money(string expr)
        {
            return "CONVERT(decimal(20,2), ROUND(" + expr + ", 2))";
        }

        // 一页卡片加上各自的使用部门（fa_DeptScale 按卡片版本 lOptID）。
        static List<object> Items(object conn, List<Dictionary<string, object>> rows, int limit)
        {
            List<Dictionary<string, object>> page = rows.GetRange(0, Math.Min(rows.Count, limit));
            Dictionary<string, List<object>> depts = Depts(conn, page);
            List<object> items = new List<object>();
            for (int i = 0; i < page.Count; i++)
            {
                Dictionary<string, object> item = Item(page[i]);
                List<object> list;
                item["depts"] = depts.TryGetValue(ArcRead.Cell(page[i], "card_id"), out list) ? list : new List<object>();
                items.Add(item);
            }
            return items;
        }

        static Dictionary<string, List<object>> Depts(object conn, List<Dictionary<string, object>> page)
        {
            Dictionary<string, List<object>> map = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            if (page.Count == 0)
            {
                return map;
            }
            StringBuilder sql = new StringBuilder(DeptSql);
            object[] args = new object[page.Count];
            for (int i = 0; i < page.Count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
                args[i] = Int(ArcRead.Cell(page[i], "card_id"));
            }
            sql.Append(") ORDER BY h.sCardID, sc.sDeptNum");
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql.ToString(), args, page.Count * 50))
            {
                string id = ArcRead.Cell(row, "card_id");
                if (!map.ContainsKey(id))
                {
                    map[id] = new List<object>();
                }
                Dictionary<string, object> dept = new Dictionary<string, object>();
                dept["code"] = ArcRead.Cell(row, "code");
                dept["name"] = ArcRead.Cell(row, "name");
                dept["ratio"] = Number(ArcRead.Cell(row, "ratio"));
                map[id].Add(dept);
            }
            return map;
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            string[] texts = new string[]
            {
                "code", "name", "asset_num", "spec", "type_code", "type_name", "status_code", "status", "origin_code", "origin",
                "depreciation_method_code", "depreciation_method", "start_date", "entry_date"
            };
            for (int i = 0; i < texts.Length; i++)
            {
                item[texts[i]] = ArcRead.Cell(row, texts[i]);
            }
            item["useful_life_months"] = Int(ArcRead.Cell(row, "useful_life_months"));
            item["used_months"] = Int(ArcRead.Cell(row, "used_months"));
            string[] money = new string[] { "original_value", "accumulated_depreciation", "impairment", "net_value", "net_salvage" };
            for (int i = 0; i < money.Length; i++)
            {
                item[money[i]] = Number(ArcRead.Cell(row, money[i]));
            }
            string disposedDate = ArcRead.Cell(row, "disposed_date");
            item["disposed"] = disposedDate != null;
            item["disposed_date"] = disposedDate;
            item["disposal_code"] = ArcRead.Cell(row, "disposal_code");
            item["disposal_way"] = ArcRead.Cell(row, "disposal_way");
            item["keeper"] = Blank(ArcRead.Cell(row, "keeper"));
            item["location"] = Blank(ArcRead.Cell(row, "location"));
            return item;
        }

        static string Blank(string text)
        {
            return text == null || text.Trim().Length == 0 ? null : text;
        }

        static object Int(string text)
        {
            int value;
            if (text == null || !int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }

        static object Number(string text)
        {
            decimal value;
            if (text == null || !decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }
    }

    // 截至日（登录日期所在月的月末）、登录年度和月份、该年度不晚于登录月的已计提折旧期间（没有为 0）。
    internal sealed class FaScope
    {
        public string AsOf;
        public int Year;
        public int Month;
        public int Period;

        internal static FaScope Of(WorkContext ctx)
        {
            GlState.LoginYear(ctx);
            DateTime day = DateTime.ParseExact(ctx.Item.Date.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
            FaScope scope = new FaScope();
            scope.Year = day.Year;
            scope.Month = day.Month;
            DateTime end = new DateTime(day.Year, day.Month, 1).AddMonths(1).AddDays(-1);
            scope.AsOf = end.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            string period = Rows.Scalar(ctx.Conn, ArcFa.DeprPeriodSql, new object[] { scope.Year, scope.Month });
            int value;
            scope.Period = period != null && int.TryParse(period, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : 0;
            return scope;
        }

        // 响应里的口径：fiscal_year 登录年度，as_of 截至日，depr_period 累计折旧所取的期间（本年还没计提为 null）。
        internal void Put(Dictionary<string, object> body)
        {
            body["fiscal_year"] = Year;
            body["as_of"] = AsOf;
            body["depr_period"] = Period > 0 ? (object)Period : null;
        }
    }
}
