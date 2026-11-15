using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的账龄分析部分：应收、应付的 {INNER} 里 ? 与参数一一对应（basis / as_of / 到期日天数，
    // 再接明细条件）；外层 SQL 含 max_age / 按（往来单位、原单类型、单号）合计的净余额窗口；oldk 拆分；
    // 分桶 age 与原单 vdate / vage 分开；default_credit_days 的请求校验。不连库。
    internal static class ReportsAgingSelfTest
    {
        const int Days = 37;
        const string AsOf = "2026-09-30";

        public static void Run()
        {
            CheckOrder("ar", "due");
            CheckOrder("ap", "due");
            CheckOrder("ar", "document");
            CheckOrder("ap", "document");
            Expect("aging max_age", ReportsArapAging.AgingSql.Contains("max_age"));
            CheckNet();
            CheckCollision();
            CheckOldk();
            Expect("aging days default", Parse("ar", "due", null).DefaultCreditDays == 0
                && !Parse("ar", "due", null).HasDefaultCreditDays);
            Expect("aging days parsed", Parse("ar", "due", Days).DefaultCreditDays == Days);
            Refused("aging days document", "document", 30);
            Refused("aging days missing basis", null, 30);
            Refused("aging days negative", "due", -1);
            Refused("aging days too many", "due", 3651);
            Refused("aging days text", "due", "30");
        }

        // 每个 ? 看它前后的文本认出用途：「?=1」是 basis（due 时 1），「CONVERT(date, ?」是 as_of，
        // 「DATEADD(day, ?」是 default_credit_days；其余（科目条件等）不核对。三种都要至少出现一次。
        static void CheckOrder(string side, string basis)
        {
            bool due = basis == "due";
            ReportArgs a = Parse(side, basis, due ? (object)Days : null);
            List<object> args = new List<object>();
            string sql = ReportsArapAging.Inner(a, args);
            int seen = 0;
            int at = -1;
            for (int i = 0; i < args.Count; i++)
            {
                at = sql.IndexOf('?', at + 1);
                Expect("aging placeholders " + side, at >= 0);
                seen |= Check(side + " " + basis, sql, at, args[i], due ? 1 : 0, due ? Days : 0);
            }
            Expect("aging placeholder count " + side + " " + basis, sql.IndexOf('?', at + 1) < 0
                && (seen & 3) == 3 && (!due || seen == 7));
            CheckInner(side, sql);
        }

        // 分桶 age 按全部行；原单计龄日期 / 天数只取 rd=0。应收按原单 SUM 并选出 vt；应付仍逐行并选出类型。
        static void CheckInner(string side, string sql)
        {
            Expect("aging inner vdate " + side, sql.Contains("vdate") && sql.Contains("vage"));
            if (side == "ar")
            {
                Expect("aging ar net doc", sql.Contains("SUM(g.bal)")
                    && sql.Contains("GROUP BY g.cDwCode, g.vt, g.vid"));
                Expect("aging ar bucket age", sql.Contains(
                    "DATEDIFF(day, CASE WHEN ?=1 THEN MIN(g.dDue) ELSE MIN(g.dVouchDate) END, CONVERT(date, ?, 23)) age")
                    && !sql.Contains("MIN(CASE WHEN g.rd=0 THEN g.dAge END), CONVERT(date, ?, 23)) age"));
                Expect("aging ar origin date", sql.Contains(
                    "MIN(CASE WHEN g.rd=0 THEN CASE WHEN ?=1 THEN g.dDue ELSE g.dVouchDate END END) vdate"));
                Expect("aging ar origin vage", sql.Contains("CONVERT(date, ?, 23)) vage"));
                Expect("aging ar type col", sql.Contains("g.vt, g.vid"));
                return;
            }
            Expect("aging ap line age", sql.Contains("Ap_Detail") && !sql.Contains("SUM(g.bal)"));
            Expect("aging ap origin date", sql.Contains("a.dAge vdate")
                && sql.Contains("DATEDIFF(day, a.dAge,"));
            Expect("aging ap type col", sql.Contains("d.cCoVouchType vt") && sql.Contains("d.cCoVouchID vid"));
        }

        // 应付已结清原单：发票行 +100 账龄 700、9P −100，逐行 bal>0 仍会入选；MAX 必须再要求
        // 窗口合计净余额 > 0。应收内层已按单据 SUM，同一条件对净余额为 0 的原单同样排除。
        static void CheckNet()
        {
            Expect("aging net window", ReportsArapAging.AgingSql.Contains(ReportsArapAging.NetExpr));
            Expect("aging max net", ReportsArapAging.MaxAgeExpr.Contains("x.net>0")
                && ReportsArapAging.MaxAgeExpr.Contains("x.bal>0")
                && ReportsArapAging.MaxAgeExpr.Contains("x.vage>0"));
            Expect("aging oldk net", ReportsArapAging.OldkExpr.Contains("x.net>0")
                && ReportsArapAging.OldkExpr.Contains("x.vage"));
            // 分桶合计由 Sums 生成后替换 {SUMS}，按行余额累加，不受净额条件影响。
            string sums = ReportsArapAging.Sums(2);
            Expect("aging buckets line", ReportsArapAging.AgingSql.Contains("{SUMS}")
                && sums.Contains("SUM(CASE WHEN x.is_open=1 AND x.bk=1 THEN x.bal ELSE 0 END) b1")
                && sums.IndexOf("x.net", StringComparison.Ordinal) < 0);
        }

        // 同号不同类型必须分开合计：应收发票 C900001 与同号收款单、应付已结清发票 C900001 与同号应付单，
        // 窗口按（往来单位、类型、单号），oldk 含类型。内层选出 vt / cCoVouchType。
        static void CheckCollision()
        {
            Expect("aging net type", ReportsArapAging.NetExpr.Contains("PARTITION BY m.cDwCode, m.vt, m.vid")
                && !ReportsArapAging.NetExpr.Contains("PARTITION BY m.cDwCode, m.vid)"));
            Expect("aging oldk type", ReportsArapAging.OldkExpr.Contains("ISNULL(x.vt, N'')")
                && ReportsArapAging.OldkExpr.Contains("ISNULL(x.vid, N'')"));
            Expect("aging sql type", ReportsArapAging.AgingSql.Contains("m.vt, m.vid")
                && ReportsArapAging.AgingSql.Contains("r.vt, r.vid"));
            Expect("aging mfilter before net", ReportsArapAging.AgingSql.Contains("m WHERE 1=1{MFILTER}) x"));
        }

        static void CheckOldk()
        {
            Expect("aging oldk null", ReportsArapAging.OldestDate(null) == null);
            Expect("aging oldk empty", ReportsArapAging.OldestDate("") == null
                && ReportsArapAging.OldestDate("   ") == null);
            Expect("aging oldk short", ReportsArapAging.OldestDate("0000000042|C900001") == null);
            Expect("aging oldk date", ReportsArapAging.OldestDate("0000000042|C900001|2026-01-15")
                == "2026-01-15");
            Expect("aging oldk typed", ReportsArapAging.OldestDate("0000000042|01|C900001|2026-01-15")
                == "2026-01-15");
            Expect("aging oldk empty vid", ReportsArapAging.OldestDate("0000000042||2026-01-15")
                == "2026-01-15");
            Expect("aging oldk empty type", ReportsArapAging.OldestDate("0000000042||C900001|2026-01-15")
                == "2026-01-15");
            Expect("aging oldk empty date", ReportsArapAging.OldestDate("0000000042|C900001|") == null);
            Expect("aging oldk vid", ReportsArapAging.OldkExpr.Contains("ISNULL(x.vid, N'')"));
        }

        static int Check(string side, string sql, int at, object arg, int basis, int days)
        {
            string before = sql.Substring(0, at);
            if (string.CompareOrdinal(sql, at, "?=1", 0, 3) == 0)
            {
                Expect("aging basis " + side, arg is int && (int)arg == basis);
                return 1;
            }
            if (before.EndsWith("CONVERT(date, ", StringComparison.Ordinal))
            {
                Expect("aging as_of " + side, (arg as string) == AsOf);
                return 2;
            }
            if (before.EndsWith("DATEADD(day, ", StringComparison.Ordinal))
            {
                Expect("aging credit days " + side, arg is int && (int)arg == days);
                return 4;
            }
            return 0;
        }

        static ReportArgs Parse(string side, string basis, object days)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["side"] = side;
            body["as_of"] = AsOf;
            if (basis != null)
            {
                body["basis"] = basis;
            }
            if (days != null)
            {
                body["default_credit_days"] = days;
            }
            return ReportsReq.Parse("arap_aging", body);
        }

        static void Refused(string name, string basis, object days)
        {
            try
            {
                Parse("ar", basis, days);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "default_credit_days");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
