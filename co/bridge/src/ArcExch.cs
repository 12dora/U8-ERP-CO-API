using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 档案 exchange_rate（汇率，U8 表 exch）的读取：纯 SQL，只用 ctx.Conn（的写入见 ArcExchWrite）。表名只来自 ArcKind，调用方的文本只进 ? 参数。
    // exch 一行一个汇率：cexch_name 币种名称、iYear 年度、iperiod 期间（自然月，同 GlSave）、itype 类型、nflat 汇率。
    // itype：2 记账汇率（固定汇率，按月），3 调整汇率（按月，期末调汇用），1 浮动汇率（按日，cdate 是日期，U8 存 yyyy-mm-dd，已在测试账套核对）。
    // 列表把同一 (币种, 年度, 期间[, 日]) 的记账汇率和调整汇率并成一行，编码写成 "<币种>:<年度>:<期间>[:<日>]"。
    // get 另收 "<币种>:<yyyy-mm-dd>"：按 U8 的取数规则返回该日期单据会用的汇率（见 Get）。
    // exch 有 pubufts：一行的 ufts 取并入各行的最大值，changed_since 按它过滤（删除的汇率靠整轮重读发现）。
    internal static class ArcExch
    {
        internal const string Name = "exchange_rate";
        // 币种名称最长 8（exch.cexch_name nvarchar(8)），日最长 10（exch.cdate nvarchar(10)）。
        internal const int MaxCurrency = 8;
        internal const int MaxDay = 10;
        internal const int CodeMax = MaxCurrency + 1 + 4 + 1 + 2 + 1 + MaxDay;
        internal const string Fixed = "fixed";
        internal const string Floating = "floating";

        const string Cols = "g.currency, g.y, g.p, g.d, g.rate, g.adjust_rate, CONVERT(varchar(20), CONVERT(bigint, g.mts)) AS ufts";
        const string CodeExpr = "(g.currency + ':' + CONVERT(varchar(11), g.y) + ':' + CONVERT(varchar(3), g.p)"
            + " + CASE WHEN g.d='' THEN '' ELSE ':' + g.d END)";
        // 汇率方式（外币设置里的固定汇率 / 浮动汇率）。缺省值是 True，按 True 为固定汇率。
        const string ModeSql = "SELECT TOP 1 CONVERT(varchar(10), cValue) v FROM accinformation WHERE cSysID='AA' AND cName='iXchgRateStl'";

        internal static bool Is(ArcKind kind)
        {
            return kind != null && kind.Name == Name;
        }

        // 取数规则：固定汇率取单据日期所在年度、月份的记账汇率（itype 2）；浮动汇率取当日的浮动汇率（itype 1，
        // cdate 按 "yyyy-mm-dd" 或日数匹配；U8 存 yyyy-mm-dd，已在测试账套核对），当日没有就 404，不向前找。
        // 收款单、应收单、销售发票的 cexch_name / iExchRate 应与单据月份的记账汇率一致。
        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            ExchKey key = ExchKey.Parse(req.Code, "code");
            object conn = ctx.Conn;
            string mode = key.Date == null ? null : Mode(conn);
            List<object> args = new List<object>();
            StringBuilder sql = new StringBuilder("SELECT TOP (1) ").Append(Cols).Append(" FROM (");
            Grouped(sql, args, ctx, req.Kind, key);
            sql.Append(") g WHERE 1=1");
            if (mode == Floating)
            {
                sql.Append(" AND g.d IN (?, ?, ?)");
                args.Add(key.Date);
                args.Add(key.DayOfDate(false));
                args.Add(key.DayOfDate(true));
            }
            else
            {
                sql.Append(" AND g.d=?");
                args.Add(key.Day ?? "");
            }
            Dictionary<string, object> row = Rows.One(conn, sql.ToString(), args.ToArray());
            if (row == null || (key.Date != null && ArcRead.Cell(row, "rate") == null))
            {
                throw new BridgeException(404, "not_found", key.Date == null ? "档案不存在" : "该日期没有汇率");
            }
            PermHook.Archive(ctx, req.Code);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["fields"] = Item(row);
            body["ufts"] = ArcRead.Cell(row, "ufts");
            if (mode != null)
            {
                body["rate_mode"] = mode;
            }
            return ApiResult.Ok(body);
        }

        // 列表：fiscal_year 缺省取登录日期的年份（请求的 year 是账套库年度，不用）；currency 只列一个币种；
        // name_like 按币种名称，code_prefix 按整串编码。
        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            object conn = ctx.Conn;
            string watermark = Rows.Scalar(conn, ArcRead.WatermarkSql, null);
            ExchKey scope = new ExchKey();
            scope.Currency = req.Currency;
            scope.Year = req.Year > 0 ? req.Year : GlState.LoginYear(ctx);
            List<object> args = new List<object>();
            args.Add(req.Limit + 1);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(Cols).Append(" FROM (");
            Grouped(sql, args, ctx, req.Kind, scope);
            sql.Append(") g WHERE 1=1");
            AddAfter(sql, args, req.After, scope.Year);
            ArcRead.AddFilter(sql, args, " AND " + CodeExpr + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND g.currency LIKE ? ESCAPE '\\'", ArcRead.Like(req.NameLike, true));
            ArcRead.AddFilter(sql, args, " AND g.mts > CONVERT(binary(8), CONVERT(bigint, ?))", req.Since);
            sql.Append(" ORDER BY g.currency, g.p, g.d");
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql.ToString(), args.ToArray(), req.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < req.Limit; i++)
            {
                items.Add(Item(rows[i]));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = req.Kind.Name;
            body["items"] = items;
            body["next"] = rows.Count > req.Limit ? CodeOf(rows[req.Limit - 1]) : null;
            body["watermark"] = watermark;
            body["fiscal_year"] = scope.Year;
            body["rate_mode"] = Mode(conn);
            return ApiResult.Ok(body);
        }

        // 按 (币种, 年度, 期间, 日) 分组：记账汇率 / 浮动汇率进 rate，调整汇率进 adjust_rate；固定汇率的 d 是空串。
        // 数据权限：汇率不受记录级控制，PermHook.Where 照例放在内层 WHERE 最后。
        static void Grouped(StringBuilder sql, List<object> args, WorkContext ctx, ArcKind k, ExchKey key)
        {
            sql.Append("SELECT r.currency, r.y, r.p, r.d, MAX(CASE WHEN r.itype IN (1,2) THEN r.nflat END) AS rate,");
            sql.Append(" MAX(CASE WHEN r.itype=3 THEN r.nflat END) AS adjust_rate, MAX(r.pubufts) AS mts FROM (");
            sql.Append("SELECT h.cexch_name AS currency, h.iYear AS y, h.iperiod AS p,");
            sql.Append(" CASE WHEN h.itype=1 THEN ISNULL(h.cdate,'') ELSE '' END AS d, h.itype, h.nflat, h.pubufts");
            sql.Append(" FROM ").Append(k.Table).Append(" h WHERE h.iYear=?");
            args.Add(key.Year);
            ArcRead.AddFilter(sql, args, " AND h.cexch_name=?", key.Currency);
            if (key.Period > 0)
            {
                sql.Append(" AND h.iperiod=?");
                args.Add(key.Period);
            }
            PermHook.Where(sql, args, ctx, "h");
            sql.Append(") r GROUP BY r.currency, r.y, r.p, r.d");
        }

        // after 与 ORDER BY (币种, 期间, 日) 同序；年度必须与本次列表的年度相同。
        static void AddAfter(StringBuilder sql, List<object> args, string after, int year)
        {
            if (string.IsNullOrEmpty(after))
            {
                return;
            }
            ExchKey key = ExchKey.Parse(after, "after");
            if (key.Date != null || key.Year != year)
            {
                throw ArcReq.Bad("after 必须是本年度列表返回的 next", "after");
            }
            sql.Append(" AND (g.currency > ? OR (g.currency = ? AND (g.p > ? OR (g.p = ? AND g.d > ?))))");
            args.Add(key.Currency);
            args.Add(key.Currency);
            args.Add(key.Period);
            args.Add(key.Period);
            args.Add(key.Day ?? "");
        }

        internal static string Mode(object conn)
        {
            string value = Rows.Scalar(conn, ModeSql, null);
            if (value == null)
            {
                return Fixed;
            }
            string text = value.Trim();
            bool fixedRate = text.Length == 0 || text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase);
            return fixedRate ? Fixed : Floating;
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            string day = ArcRead.Cell(row, "d");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = CodeOf(row);
            item["name"] = ArcRead.Cell(row, "currency");
            item["currency"] = ArcRead.Cell(row, "currency");
            item["year"] = Int(ArcRead.Cell(row, "y"));
            item["period"] = Int(ArcRead.Cell(row, "p"));
            item["day"] = string.IsNullOrEmpty(day) ? null : day;
            item["rate"] = Number(ArcRead.Cell(row, "rate"));
            item["adjust_rate"] = Number(ArcRead.Cell(row, "adjust_rate"));
            item["mode"] = string.IsNullOrEmpty(day) ? Fixed : Floating;
            item["ufts"] = ArcRead.Cell(row, "ufts");
            return item;
        }

        static string CodeOf(Dictionary<string, object> row)
        {
            string day = ArcRead.Cell(row, "d");
            string code = ArcRead.Cell(row, "currency") + ":" + ArcRead.Cell(row, "y") + ":" + ArcRead.Cell(row, "p");
            return string.IsNullOrEmpty(day) ? code : code + ":" + day;
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
            // FormatCell 已按 15 位有效数字转成文本（7.1235），转成 decimal 输出，不带二进制尾数。
            decimal value;
            if (text == null || !decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }
    }

    // 汇率编码："<币种>:<年度>:<期间>[:<日>]"，或 get 专用的 "<币种>:<yyyy-mm-dd>"。按冒号拆开（币种名称不含冒号）。
    internal sealed class ExchKey
    {
        const string Format = "<币种>:<年度>:<期间>[:<日>] 或 <币种>:<yyyy-mm-dd>";

        public string Currency;
        public int Year;
        public int Period;
        public string Day;
        public string Date;

        internal static ExchKey Parse(string code, string label)
        {
            string[] parts = code == null ? new string[0] : code.Split(':');
            if (parts.Length < 2 || parts.Length > 4)
            {
                throw ArcReq.Bad(label + " 必须写成 " + Format, FieldPath.Clean(label));
            }
            ExchKey key = new ExchKey();
            key.Currency = CurrencyText(parts[0], label);
            if (parts.Length == 2)
            {
                key.ParseDate(parts[1], label);
                return key;
            }
            key.Year = Digits(parts[1], 4, 1900, 9999, label);
            key.Period = Digits(parts[2], 2, 1, 12, label);
            if (parts.Length == 4)
            {
                if (!Part(parts[3], ArcExch.MaxDay))
                {
                    throw ArcReq.Bad(label + " 的日最长 10 个字符，前后不能有空格", FieldPath.Clean(label));
                }
                key.Day = parts[3];
            }
            return key;
        }

        // 币种名称 1 到 8 个字符，前后没有空格。
        internal static string CurrencyText(string text, string label)
        {
            if (!Part(text, ArcExch.MaxCurrency))
            {
                throw ArcReq.Bad(label + " 的币种名称必须是 1 到 8 个字符，前后不能有空格", FieldPath.Clean(label));
            }
            return text;
        }

        // 日期到期间按自然月（同 GlSave 的 iperiod）。未覆盖：会计期间不按自然月划分（GL_mend / UA_Period 起止日不在月初月末）
        // 的账套，这里会落到相邻期间的汇率。
        void ParseDate(string text, string label)
        {
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw ArcReq.Bad(label + " 必须写成 " + Format, FieldPath.Clean(label));
            }
            Date = text;
            Year = day.Year;
            Period = day.Month;
        }

        // 列表的 fiscal_year：省略为 0（取登录年度），否则是 1900 到 9999 的整数。
        internal static int YearOf(object year)
        {
            if (year == null)
            {
                return 0;
            }
            if (!(year is int) || (int)year < 1900 || (int)year > 9999)
            {
                throw ArcReq.Bad("fiscal_year 必须是四位年度", "fiscal_year");
            }
            return (int)year;
        }

        // 列表的 after 不能是 get 专用的 "<币种>:<yyyy-mm-dd>"。
        internal static void CheckAfter(string after)
        {
            if (after != null && Parse(after, "after").Date != null)
            {
                throw ArcReq.Bad("after 必须是列表返回的 next", "after");
            }
        }

        // 浮动汇率的 cdate 可能只存日数：padded 为 true 时补成两位。
        internal string DayOfDate(bool padded)
        {
            string day = Date.Substring(8, 2);
            return padded || day[0] != '0' ? day : day.Substring(1);
        }

        static int Digits(string text, int maxLen, int min, int max, string label)
        {
            int value;
            bool ok = text.Length > 0 && text.Length <= maxLen;
            for (int i = 0; ok && i < text.Length; i++)
            {
                ok = text[i] >= '0' && text[i] <= '9';
            }
            if (!ok || !int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value) || value < min || value > max)
            {
                throw ArcReq.Bad(label + " 必须写成 " + Format + "，年度四位、期间 1 到 12", FieldPath.Clean(label));
            }
            return value;
        }

        static bool Part(string text, int max)
        {
            return text.Length > 0 && text.Length <= max && text.Trim().Length == text.Length;
        }
    }
}
