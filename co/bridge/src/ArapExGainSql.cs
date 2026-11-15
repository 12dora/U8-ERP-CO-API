using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 汇兑损益脚本的参数（经 #exg_args 交给 sql/arap/exgain_*.sql）。RateText 是汇率的定点文本（脚本按 decimal(26,10) 读）。
    internal sealed class ExGainArgs
    {
        public string Flag;
        public int Year;
        public int Period;
        public string RegDate;
        public string Accounter;
        public string Currency;
        public string RateText;
        public bool Settle;
        public bool ByDate;
    }

    // 汇兑损益的 SQL：脚本参数表、币种和调整汇率、脚本拒绝编号到中文、提交后的回读。
    // 参数表和两张发票回写临时表都用不带参数的整段 SQL 建（Connection.Execute，不经 sp_executesql），留在请求连接上，
    // 脚本和 U8 的 clsWrite2Bill（同一连接）都看得见；之后的写入才带参数。
    internal static class ArapExGainSql
    {
        internal const string CreateScript = "sql/arap/exgain_create.sql";
        internal const string PersistScript = "sql/arap/exgain_persist.sql";
        internal const string CancelScript = "sql/arap/exgain_cancel.sql";
        internal const string CancelEndScript = "sql/arap/exgain_cancel_end.sql";
        internal const string SaleTable = "#ap_SaleBillVouchHXdata";
        internal const string PurTable = "#ap_purbillHXdata";
        // 回读时一条 SQL 里最多带几个处理号。
        const int Chunk = 50;

        internal const string PrepSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#exg_args') IS NOT NULL DROP TABLE #exg_args;"
            + " CREATE TABLE #exg_args (flag nvarchar(4), y smallint, m tinyint, reg_date nvarchar(10), accounter nvarchar(20),"
            + " cexch_name nvarchar(60), rate nvarchar(40), digest nvarchar(255), settle int, by_date int);"
            + " IF OBJECT_ID('tempdb..#exg_dw') IS NOT NULL DROP TABLE #exg_dw; CREATE TABLE #exg_dw (cDwCode nvarchar(40));"
            + " IF OBJECT_ID('tempdb..#exg_nos') IS NOT NULL DROP TABLE #exg_nos; CREATE TABLE #exg_nos (cCancelNo nvarchar(30));"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " CREATE TABLE #ap_SaleBillVouchHXdata (autoid bigint, iexchsum decimal(29,6), imoneysum decimal(29,6));"
            + " CREATE INDEX idx_HXautoid ON #ap_SaleBillVouchHXdata (autoid);"
            + " IF OBJECT_ID('tempdb..#ap_purbillHXdata') IS NOT NULL DROP TABLE #ap_purbillHXdata;"
            + " CREATE TABLE #ap_purbillHXdata (autoid bigint, iexchsum decimal(29,6), imoneysum decimal(29,6));"
            + " CREATE INDEX idx_PurHXautoid ON #ap_purbillHXdata (autoid);"
            + " SET NOCOUNT OFF;";

        internal const string DropSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#exg_args') IS NOT NULL DROP TABLE #exg_args;"
            + " IF OBJECT_ID('tempdb..#exg_dw') IS NOT NULL DROP TABLE #exg_dw;"
            + " IF OBJECT_ID('tempdb..#exg_nos') IS NOT NULL DROP TABLE #exg_nos;"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " IF OBJECT_ID('tempdb..#ap_purbillHXdata') IS NOT NULL DROP TABLE #ap_purbillHXdata;"
            + " IF OBJECT_ID('tempdb..#exg_skip') IS NOT NULL DROP TABLE #exg_skip;"
            + " IF OBJECT_ID('tempdb..#exg_odd') IS NOT NULL DROP TABLE #exg_odd;"
            + " SET NOCOUNT OFF;";

        const string ArgsFill = "INSERT INTO #exg_args (flag, y, m, reg_date, accounter, cexch_name, rate, digest, settle, by_date)"
            + " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)";
        const string DwFill = "INSERT INTO #exg_dw (cDwCode) VALUES (?)";
        const string NoFill = "INSERT INTO #exg_nos (cCancelNo) VALUES (?)";

        // 币种：是否计算汇兑损益（bCal）、是否本位币（iotherused = -1）。
        const string CurrencySql = "select top 1 convert(varchar(10), isnull(bCal,0)) as cal,"
            + " convert(varchar(10), isnull(iotherused,0)) as home from foreigncurrency where cexch_name=?";
        // 调整汇率：exch iType=3，cdate 是不补零的期间号（同 U8 界面的取数，实测）。
        const string RateSql = "select top 1 convert(varchar(40), convert(decimal(26,10), nflat)) from exch"
            + " where cexch_name=? and iType=3 and cdate=? and iYear=? and isnull(nflat,0)>0";

        // 脚本拒绝编号 → 中文 409（{0} 是英文提示最后一个冒号后的处理号）。英文原文只进审计。
        static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50020] = "该期间已结账，不能处理汇兑损益{0}";
            map[50021] = "该币种没有设置为计算汇兑损益（外币设置），请在 U8 里检查";
            map[50022] = "汇率必须大于 0";
            map[50023] = "按该汇率计算，没有需要调整的汇兑损益";
            map[50026] = "汇兑损益已制单，请先删除凭证{0}";
            map[50027] = "同一单据之后还做过汇兑损益，请先取消后面的{0}";
            map[50028] = "该单据之后还有核销、转账等其他处理，请先取消后面的处理{0}";
            map[50030] = "该登记日期没有可取消（未制单）的汇兑损益";
            map[50031] = "该登记日期的汇兑损益超过 1000 个，请用 cancel_nos 分批取消";
            map[50032] = "该处理号含进出口（RZ）等接口不处理的单据类型，是在 U8 里做的汇兑损益，请在 U8 里取消{0}";
            map[50033] = "汇兑损益要调整余额的单据在 U8 里找不到或不唯一，已回滚{0}";
            map[50034] = "需要调整的只有表头登记（没有发票行）的发票，接口不处理，请在 U8 里做汇兑损益{0}";
            map[50035] = "该期间已结账，不能取消汇兑损益{0}";
            return map;
        }

        // 建参数表和临时表，写入参数、往来单位、处理号。
        public static void Prepare(object conn, ExGainArgs args, List<string> partners, List<string> nos)
        {
            UnwriteoffSql.Run(conn, PrepSql);
            GlSql.Exec(conn, ArgsFill, new object[]
            {
                args.Flag, args.Year, args.Period, args.RegDate, args.Accounter ?? "", args.Currency ?? "",
                args.RateText ?? "", args.Settle ? 1 : 0, args.ByDate ? 1 : 0
            });
            foreach (string code in partners)
            {
                GlSql.Exec(conn, DwFill, new object[] { code });
            }
            foreach (string no in nos)
            {
                GlSql.Exec(conn, NoFill, new object[] { no });
            }
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

        // 币种必须存在、不是本位币、设置为计算汇兑损益（bCal=1）；不符 409。
        public static void CheckCurrency(object conn, string currency)
        {
            Dictionary<string, object> row = Rows.One(conn, CurrencySql, new object[] { currency });
            if (row == null)
            {
                throw ArapWriteoffGate.State("币种 " + currency + " 不存在");
            }
            if (CoRows.Col(row, "home") == "-1")
            {
                throw ArapWriteoffGate.State("本位币不做汇兑损益");
            }
            if (CoRows.Col(row, "cal") != "1")
            {
                throw ArapWriteoffGate.State(Texts[50021]);
            }
        }

        // 该期的调整汇率（exch iType=3）；没有返回 0。
        public static decimal AdjustRate(object conn, string currency, int year, int period)
        {
            string text = Rows.Scalar(conn, RateSql, new object[]
            {
                currency, period.ToString(CultureInfo.InvariantCulture), year
            });
            return WriteoffSql.Num(text);
        }

        // 调用方指定的汇率优先，否则用调整汇率；都没有 409「本期没有调整汇率」。纯函数，--selftest 用。
        internal static decimal PickRate(decimal asked, decimal adjust, string currency)
        {
            if (asked > 0)
            {
                return asked;
            }
            if (adjust > 0)
            {
                return adjust;
            }
            throw ArapWriteoffGate.State("本期没有调整汇率（" + currency + "），请在 U8 外币设置里录入调整汇率，或在请求里给 rate");
        }

        // 汇率的定点文本（脚本按 decimal(26,10) 读）。
        internal static string RateText(decimal rate)
        {
            return decimal.Round(rate, 10).ToString("0.##########", CultureInfo.InvariantCulture);
        }

        // 脚本拒绝 → 中文。50025（处理号不存在）是 404，其余 409 state_mismatch；没登记的编号 500。
        internal static BridgeException Refused(ScriptRefusal refusal)
        {
            int number = refusal == null ? 0 : refusal.Number;
            string tail = TailOf(refusal == null ? "" : refusal.Message);
            if (number == 50025)
            {
                return new BridgeException(404, "not_found", "汇兑损益处理号不存在" + (tail.Length > 0 ? "：" + tail : ""));
            }
            string text;
            if (!Texts.TryGetValue(number, out text))
            {
                return new BridgeException(500, "internal", "汇兑损益脚本拒绝 " + number.ToString(CultureInfo.InvariantCulture));
            }
            return ArapWriteoffGate.State(string.Format(CultureInfo.InvariantCulture, text, tail.Length > 0 ? "：" + tail : ""));
        }

        // 英文提示最后一个冒号后的部分（处理号），没有冒号为空。
        internal static string TailOf(string message)
        {
            string text = message ?? "";
            int at = text.LastIndexOf(':');
            return at < 0 ? "" : text.Substring(at + 1).Trim();
        }

        // 新连接上数这些处理号还剩几行 9M（提交后回读，不加 NOLOCK）。
        public static int CountRows(object conn, string flag, List<string> nos)
        {
            int total = 0;
            for (int start = 0; start < nos.Count; start += Chunk)
            {
                int take = Math.Min(Chunk, nos.Count - start);
                List<object> args = new List<object>();
                args.Add(flag);
                for (int i = 0; i < take; i++)
                {
                    args.Add(nos[start + i]);
                }
                string raw = Rows.Scalar(conn, CountSql(flag, take), args.ToArray());
                total += CoRows.AsId(raw);
            }
            return total;
        }

        internal static string CountSql(string flag, int count)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("select convert(varchar(20), count(*)) from ").Append(flag == "AP" ? "Ap_Detail" : "Ar_Detail");
            sb.Append(" where cProcStyle=N'9M' and cFlag=? and cCancelNo in (");
            for (int i = 0; i < count; i++)
            {
                sb.Append(i == 0 ? "?" : ",?");
            }
            return sb.Append(")").ToString();
        }
    }
}
