using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的账龄分析部分：应收、应付的 {INNER} 里 ? 与参数一一对应（应收 basis、as_of、到期日天数、as_of，
    // 应付 basis、到期日天数、as_of、as_of，再接明细条件）；default_credit_days 的请求校验。不连库。
    internal static class ReportsAgingSelfTest
    {
        const int Days = 37;
        const string AsOf = "2026-09-30";

        public static void Run()
        {
            CheckOrder("ar");
            CheckOrder("ap");
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
        static void CheckOrder(string side)
        {
            ReportArgs a = Parse(side, "due", Days);
            List<object> args = new List<object>();
            string sql = ReportsArapAging.Inner(a, args);
            int seen = 0;
            int at = -1;
            for (int i = 0; i < args.Count; i++)
            {
                at = sql.IndexOf('?', at + 1);
                Expect("aging placeholders " + side, at >= 0);
                seen |= Check(side, sql, at, args[i]);
            }
            Expect("aging placeholder count " + side, sql.IndexOf('?', at + 1) < 0 && seen == 7);
        }

        static int Check(string side, string sql, int at, object arg)
        {
            string before = sql.Substring(0, at);
            if (string.CompareOrdinal(sql, at, "?=1", 0, 3) == 0)
            {
                Expect("aging basis " + side, arg is int && (int)arg == 1);
                return 1;
            }
            if (before.EndsWith("CONVERT(date, ", StringComparison.Ordinal))
            {
                Expect("aging as_of " + side, (arg as string) == AsOf);
                return 2;
            }
            if (before.EndsWith("DATEADD(day, ", StringComparison.Ordinal))
            {
                Expect("aging credit days " + side, arg is int && (int)arg == Days);
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
