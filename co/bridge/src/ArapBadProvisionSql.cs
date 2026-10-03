using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 计提坏账准备脚本的参数（经 #badp_args 交给 sql/arap/bad_provision.sql）。
    internal sealed class ProvisionArgs
    {
        public string Date;
        public int Year;
        public int Period;
        public string YearStart;
        public bool PayDays;
    }

    // 计提坏账准备（9F）的 SQL：参数表、账龄起算口径、会计年度第一天、脚本拒绝编号到中文、提交后的回读。
    // 参数表用不带参数的整段 SQL 建（留在请求连接上，脚本看得见），之后的写入才带参数。
    internal static class ArapBadProvisionSql
    {
        internal const string Script = "sql/arap/bad_provision.sql";
        internal const string Style = "9F";
        internal const string StyleName = "计提坏账";

        internal const string PrepSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#badp_args') IS NOT NULL DROP TABLE #badp_args;"
            + " CREATE TABLE #badp_args (y smallint, m tinyint, d nvarchar(10), ys nvarchar(10), payday int);"
            + " SET NOCOUNT OFF;";

        internal const string DropSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#badp_args') IS NOT NULL DROP TABLE #badp_args;"
            + " IF OBJECT_ID('tempdb..#badp_age') IS NOT NULL DROP TABLE #badp_age;"
            + " IF OBJECT_ID('tempdb..#badp_bal') IS NOT NULL DROP TABLE #badp_bal;"
            + " SET NOCOUNT OFF;";

        const string ArgsFill = "INSERT INTO #badp_args (y, m, d, ys, payday) VALUES (?, ?, ?, ?, ?)";
        // 账龄起算日是否加付款条件的信用天数（应收选项 BadAgeAnalyseDate）。
        const string PayDaysSql = "select top 1 ltrim(rtrim(cValue)) from AccInformation where cSysID=N'AR' and cName=N'BadAgeAnalyseDate'";
        // 会计年度第一天（UA_Period，与 WriteoffSql.PeriodOf 同表）。
        const string YearStartSql = "select convert(varchar(10), min(p.dBegin), 23) from UFSYSTEM..UA_Period p"
            + " where p.cAcc_Id=? and p.iYear=? and (p.bIsDelete=0 or p.bIsDelete is null)";
        // 提交后的回读：当年那一行的处理号和坏账准备余额。
        const string RereadSql = "select top 1 cCancelNo as no, convert(varchar(40), iRemainAmount) as remain from Ar_BadPara"
            + " where autoid=? and iYear=?";

        // 脚本内部防御（参数缺失）的编号，按内部错误处理。
        internal const int InternalRefusal = 50160;

        // 脚本拒绝编号 → 中文 409。{0} 年度、{1} 期间、{2} 英文提示最后一个冒号后的部分（凭证号）。
        static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50161] = "应收 {0} 年 {1} 月已结账";
            map[50162] = "未设置 {0} 年坏账准备参数（应收款管理 › 设置 › 坏账准备）";
            map[50163] = "{0} 年的坏账准备参数有多行，请在 U8 里核对";
            map[50164] = "不支持的坏账计提方法（接口只支持应收余额百分比法、账龄分析法、销售收入百分比法）";
            map[50165] = "未设置坏账准备的账龄区间（应收款管理 › 设置 › 坏账准备）";
            map[50166] = "本次计提金额为 0";
            map[50167] = "{0} 年的坏账准备计提已制单，请先删除凭证{2}";
            map[50168] = "坏账准备参数里没有设置坏账准备科目（应收款管理 › 设置 › 坏账准备）";
            map[50169] = "坏账准备参数已被修改，已回滚，请重试";
            map[50170] = "坏账准备的账龄区间设置有误（上限天数重复或有多个「以上」区间），请在 U8 里核对";
            return map;
        }

        // 建参数表并写入参数。
        public static void Prepare(object conn, ProvisionArgs args)
        {
            UnwriteoffSql.Run(conn, PrepSql);
            GlSql.Exec(conn, ArgsFill, new object[] { args.Year, args.Period, args.Date, args.YearStart, args.PayDays ? 1 : 0 });
        }

        // 失败不影响结果（临时表随连接释放）；只为登录复用时不把临时表留给下一笔请求。
        public static void Drop(object conn)
        {
            try
            {
                UnwriteoffSql.Run(conn, DropSql);
            }
            catch (Exception)
            {
            }
        }

        public static bool PayDays(object conn)
        {
            return IsOn(Rows.Scalar(conn, PayDaysSql, new object[0]));
        }

        // 选项值 1 / True 为加信用天数，其余（含读不到）按单据日期。纯函数，--selftest 用。
        internal static bool IsOn(string value)
        {
            string text = (value ?? "").Trim();
            return text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase);
        }

        // 会计年度第一天；读不到按自然年 1 月 1 日。
        public static string YearStart(object conn, string acc, int year)
        {
            string text = Rows.Scalar(conn, YearStartSql, new object[] { acc ?? "", year });
            DateTime day;
            if (text != null && DateTime.TryParseExact(text.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                return text.Trim();
            }
            return year.ToString("0000", CultureInfo.InvariantCulture) + "-01-01";
        }

        // 脚本拒绝 → 中文 409 state_mismatch；内部防御和没登记的编号 500。
        internal static BridgeException Refused(ScriptRefusal refusal, int year, int period)
        {
            int number = refusal == null ? 0 : refusal.Number;
            string text;
            if (number == InternalRefusal || !Texts.TryGetValue(number, out text))
            {
                return new BridgeException(500, "internal", "计提坏账准备脚本拒绝 " + number.ToString(CultureInfo.InvariantCulture));
            }
            string tail = ArapExGainSql.TailOf(refusal.Message);
            return ArapWriteoffGate.State(string.Format(CultureInfo.InvariantCulture, text,
                year.ToString(CultureInfo.InvariantCulture), period.ToString(CultureInfo.InvariantCulture),
                tail.Length > 0 ? " " + tail : ""));
        }

        // 新连接上读当年那一行（提交后回读，不加 NOLOCK）；没有返回 null。
        public static Dictionary<string, object> Reread(object conn, int rowId, int year)
        {
            return Rows.One(conn, RereadSql, new object[] { rowId, year });
        }

        // 计提方法名（Ar_BadPara.iJtStyle）。纯函数，--selftest 用。
        internal static string MethodName(int style)
        {
            switch (style)
            {
                case 1:
                    return "应收余额百分比法";
                case 2:
                    return "账龄分析法";
                case 3:
                    return "销售收入百分比法";
                default:
                    return "";
            }
        }

        // 去掉小数尾部的 0（脚本的 decimal(28,10) 文本），保持数值不变。纯函数，--selftest 用。
        internal static decimal Trim(decimal value)
        {
            return value / 1.0000000000000000000000000000m;
        }
    }
}
