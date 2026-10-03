using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 新增前的档案检查。UFAPBO.SaveVouch 不像界面那样逐项校验，这里先挡住明显的错。
    // 受控科目看 code.cother：应收 AR、应付 AP。收付款单表体 cKm、应收/应付单表头 cCode 必须是本系统受控科目；
    // 应收/应付单表体 cCode 不能是受控科目；收付款单表头 cCode（结算科目）只查存在且末级。
    internal static class ArapRefs
    {
        public static void Check(object conn, ArapSpec spec, ArapInput input)
        {
            ArapArch.Check(conn, spec, input);
            Party(conn, spec, Text(input.Head, "cdwcode"), input.Date);
            int year = int.Parse(input.Date.Substring(0, 4), CultureInfo.InvariantCulture);
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Subject(conn, year, Text(input.Head, "ccode"), spec.Close ? null : spec.Flag, seen);
            for (int i = 0; i < input.Lines.Count; i++)
            {
                Dictionary<string, string> fields = input.Lines[i].Fields;
                if (spec.Close)
                {
                    Subject(conn, year, Text(fields, "ckm"), spec.Flag, seen);
                }
                else
                {
                    Subject(conn, year, Text(fields, "ccode"), "", seen);
                }
            }
        }

        internal static void Party(object conn, ArapSpec spec, string code, string date)
        {
            bool customer = spec.Flag == "AR";
            string sql = customer
                ? "select cCusCode as code, convert(varchar(10), dEndDate, 23) as ended from Customer where cCusCode=?"
                : "select cVenCode as code, convert(varchar(10), dEndDate, 23) as ended from Vendor where cVenCode=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { code });
            string label = customer ? "客户" : "供应商";
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", label + "不存在 " + code);
            }
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, date) <= 0)
            {
                throw new BridgeException(400, "bad_request", label + "已停用 " + code);
            }
        }

        // control：null 不查受控；"" 必须不是 AR/AP 受控；"AR"/"AP" 必须是该系统受控。
        internal static void Subject(object conn, int year, string code, string control, HashSet<string> seen)
        {
            string key = (control ?? "*") + "|" + code;
            if (!seen.Add(key))
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn,
                "select bend, isnull(cother,N'') as cother from code where iyear=? and ccode=?", new object[] { year, code });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "科目不存在 " + code);
            }
            if (!CoRows.FlagOf(row, "bend"))
            {
                throw new BridgeException(400, "bad_request", "科目不是末级 " + code);
            }
            if (control != null)
            {
                Controlled(CoRows.Col(row, "cother").ToUpperInvariant(), control, code);
            }
        }

        static void Controlled(string other, string control, string code)
        {
            if (control.Length == 0 && (other == "AR" || other == "AP"))
            {
                throw new BridgeException(400, "bad_request", "表体科目不能是应收/应付受控科目 " + code);
            }
            if (control.Length > 0 && other != control)
            {
                throw new BridgeException(400, "bad_request", "科目不是" + (control == "AR" ? "应收" : "应付") + "受控科目 " + code);
            }
        }

        static string Text(Dictionary<string, string> map, string key)
        {
            string value;
            return map.TryGetValue(key, out value) ? value : "";
        }
    }
}
