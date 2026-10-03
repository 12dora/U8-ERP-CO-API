using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 新增前：单据月份在 GL_mend 上 AR/AP 未结账；币种、结算方式、部门（末级）、业务员、项目大类与项目存在。
    internal static class ArapArch
    {
        public static void Check(object conn, ArapSpec spec, ArapInput input)
        {
            RefuseClosed(conn, spec, input.Date);
            Need(conn, "select top 1 cexch_name from foreigncurrency where cexch_name=?", input.Currency, "币种");
            string settle = Text(input.Head, "csscode");
            if (settle.Length > 0)
            {
                Settle(conn, settle);
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Refs(conn, input.Head, "cdeptcode", "cperson", seen);
            Item(conn, Text(input.Head, "citem_class"), Text(input.Head, "citemcode"), seen);
            for (int i = 0; i < input.Lines.Count; i++)
            {
                Dictionary<string, string> f = input.Lines[i].Fields;
                if (spec.Close)
                {
                    Refs(conn, f, "cdepcode", "cpersoncode", seen);
                    Item(conn, Text(f, "cxmclass"), Text(f, "cxm"), seen);
                }
                else
                {
                    Refs(conn, f, "cdeptcode", "cperson", seen);
                    Item(conn, Text(f, "citem_class"), Text(f, "citemcode"), seen);
                }
            }
        }

        // 年、月取单据日期（yyyy-MM-dd）。GL_mend 没有该月的行视为未结账。
        internal static void RefuseClosed(object conn, ArapSpec spec, string date)
        {
            int year = int.Parse(date.Substring(0, 4), CultureInfo.InvariantCulture);
            int month = int.Parse(date.Substring(5, 2), CultureInfo.InvariantCulture);
            string sql = "select top 1 convert(varchar(4), iperiod) from GL_mend where iyear=? and iperiod=? and isnull("
                + spec.MendFlag + ",0)<>0";
            if (Rows.Scalar(conn, sql, new object[] { year, month }) != null)
            {
                throw new BridgeException(409, "state_mismatch", (spec.Flag == "AR" ? "应收" : "应付") + "已结账");
            }
        }

        static void Settle(object conn, string code)
        {
            Dictionary<string, object> row = Rows.One(conn,
                "select cSSCode as code, bSSEnd as leaf from SettleStyle where cSSCode=?", new object[] { code });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "结算方式不存在 " + code);
            }
            if (!CoRows.FlagOf(row, "leaf"))
            {
                throw new BridgeException(400, "bad_request", "结算方式不是末级 " + code);
            }
        }

        internal static void Refs(object conn, Dictionary<string, string> map, string deptKey, string personKey, HashSet<string> seen)
        {
            string dept = Text(map, deptKey);
            if (dept.Length > 0 && seen.Add("dept|" + dept))
            {
                Dictionary<string, object> row = Rows.One(conn,
                    "select cDepCode as code, bDepEnd as leaf from Department where cDepCode=?", new object[] { dept });
                if (row == null)
                {
                    throw new BridgeException(400, "bad_request", "部门不存在 " + dept);
                }
                if (!CoRows.FlagOf(row, "leaf"))
                {
                    throw new BridgeException(400, "bad_request", "部门不是末级 " + dept);
                }
            }
            string person = Text(map, personKey);
            if (person.Length > 0 && seen.Add("person|" + person))
            {
                Need(conn, "select top 1 cPersonCode from Person where cPersonCode=?", person, "业务员");
            }
        }

        // 项目表名按 fitem.ctable，只接受 fitemss + 大类编码（字母数字）这一种形式。
        static void Item(object conn, string cls, string code, HashSet<string> seen)
        {
            if (cls.Length == 0)
            {
                if (code.Length > 0)
                {
                    throw new BridgeException(400, "bad_request", "填写项目时必须同时填写项目大类");
                }
                return;
            }
            if (!seen.Add("item|" + cls + "|" + code))
            {
                return;
            }
            string table = Rows.Scalar(conn, "select ctable from fitem where citem_class=?", new object[] { cls });
            if (table == null)
            {
                throw new BridgeException(400, "bad_request", "项目大类不存在 " + cls);
            }
            if (code.Length == 0)
            {
                return;
            }
            if (!AlnumOnly(cls) || !string.Equals(table.Trim(), "fitemss" + cls, StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(400, "bad_request", "项目大类无法识别 " + cls);
            }
            string sql = "select top 1 citemcode from " + CoRows.Ident("fitemss" + cls) + " where citemcode=?";
            if (Rows.Scalar(conn, sql, new object[] { code }) == null)
            {
                throw new BridgeException(400, "bad_request", "项目不存在 " + code);
            }
        }

        static bool AlnumOnly(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (!char.IsLetterOrDigit(text[i]) || text[i] > 'z')
                {
                    return false;
                }
            }
            return true;
        }

        internal static void Need(object conn, string sql, string code, string label)
        {
            if (Rows.Scalar(conn, sql, new object[] { code }) == null)
            {
                throw new BridgeException(400, "bad_request", label + "不存在 " + code);
            }
        }

        static string Text(Dictionary<string, string> map, string key)
        {
            string value;
            return map.TryGetValue(key, out value) ? value : "";
        }
    }
}
