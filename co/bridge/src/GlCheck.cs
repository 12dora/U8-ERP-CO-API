using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调用 Transact 之前查库校验：它自己提交，报错之前可能已经走了一半，能挡的都先挡。
    internal static class GlCheck
    {
        const string AccountSql = "SELECT ccode, CONVERT(int, ISNULL(bend,0)) bend, CONVERT(int, ISNULL(bclose,0)) bclose,"
            + " CONVERT(int, ISNULL(bdept,0)) bdept, CONVERT(int, ISNULL(bperson,0)) bperson, CONVERT(int, ISNULL(bcus,0)) bcus,"
            + " CONVERT(int, ISNULL(bsup,0)) bsup, CONVERT(int, ISNULL(bitem,0)) bitem, ISNULL(cass_item,'') cass_item,"
            + " CONVERT(int, ISNULL(bCashItem,0)) bcashitem, ISNULL(cother,'') cother, ISNULL(cexch_name,'') cexch_name"
            + " FROM code WHERE iyear=? AND ccode=?";

        public static void Validate(object conn, int year, GlDraft draft)
        {
            Dictionary<string, Dictionary<string, object>> accounts = new Dictionary<string, Dictionary<string, object>>();
            HashSet<string> seen = new HashSet<string>();
            decimal cashNet = 0;
            decimal flowNet = 0;
            for (int i = 0; i < draft.Lines.Count; i++)
            {
                GlLine line = draft.Lines[i];
                string at = "第 " + (i + 1).ToString(CultureInfo.InvariantCulture) + " 行";
                Dictionary<string, object> acct = Account(conn, year, line.Account, accounts, at);
                if (GlSql.Bit(acct, "bcashitem"))
                {
                    cashNet += line.Debit - line.Credit;
                }
                else
                {
                    flowNet += FlowNet(line);
                }
                Aux(conn, line, acct, at);
                Refs(conn, line, seen, at);
                Currency(conn, line, acct, seen, at);
            }
            if (flowNet != -cashNet && GlState.Option(conn, "bXJLL", true))
            {
                throw GlReq.Bad("现金流量不平：现金流量科目净额（借-贷）" + Money(cashNet) + "，对方分录上 cash_flow 合计（借-贷）应为 "
                    + Money(-cashNet) + "，实际 " + Money(flowNet));
            }
        }

        // U8 把现金流量项目挂在对方分录上（对方分录流量合计 = -现金科目净额）。
        // 挂在现金科目自己行上的（如银行互转）不参与核对。
        static decimal FlowNet(GlLine line)
        {
            decimal net = 0;
            foreach (GlFlow flow in line.Flows)
            {
                net += flow.Debit - flow.Credit;
            }
            return net;
        }

        static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        static Dictionary<string, object> Account(object conn, int year, string code,
            Dictionary<string, Dictionary<string, object>> cache, string at)
        {
            Dictionary<string, object> acct;
            if (cache.TryGetValue(code, out acct))
            {
                return acct;
            }
            acct = Rows.One(conn, AccountSql, new object[] { year, code });
            string name = at + "科目 " + code;
            if (acct == null)
            {
                throw GlReq.Bad(name + " 不存在");
            }
            if (!GlSql.Bit(acct, "bend"))
            {
                throw GlReq.Bad(name + " 不是末级科目");
            }
            if (GlSql.Bit(acct, "bclose"))
            {
                throw GlReq.Bad(name + " 已封存");
            }
            Controlled(conn, GlSql.Col(acct, "cother"), name);
            cache[code] = acct;
            return acct;
        }

        // 应收、应付受控科目：总账选项不允许时不能在总账凭证里用（bUseYSKM / bUseYFKM）。
        static void Controlled(object conn, string other, string name)
        {
            if (other.Length == 0)
            {
                return;
            }
            string upper = other.ToUpperInvariant();
            if (upper.IndexOf("AR", StringComparison.Ordinal) >= 0 && !GlState.Option(conn, "bUseYSKM", false))
            {
                throw GlReq.Bad(name + " 是应收系统受控科目，总账凭证不能使用");
            }
            if (upper.IndexOf("AP", StringComparison.Ordinal) >= 0 && !GlState.Option(conn, "bUseYFKM", false))
            {
                throw GlReq.Bad(name + " 是应付系统受控科目，总账凭证不能使用");
            }
        }

        static void Aux(object conn, GlLine line, Dictionary<string, object> acct, string at)
        {
            string name = at + "科目 " + line.Account;
            bool person = GlSql.Bit(acct, "bperson");
            if (person && line.Dept.Length == 0 && line.Person.Length > 0)
            {
                line.Dept = PersonDept(conn, line.Person, at);
            }
            Want(GlSql.Bit(acct, "bdept") || person, line.Dept, "部门 dept", name);
            Want(person, line.Person, "人员 person", name);
            Want(GlSql.Bit(acct, "bcus"), line.Customer, "客户 customer", name);
            Want(GlSql.Bit(acct, "bsup"), line.Supplier, "供应商 supplier", name);
            bool item = GlSql.Bit(acct, "bitem");
            Want(item, line.Item, "项目 item", name);
            string cls = GlSql.Col(acct, "cass_item");
            if (!item)
            {
                Want(false, line.ItemClass, "项目大类 item_class", name);
                return;
            }
            if (line.ItemClass.Length == 0)
            {
                line.ItemClass = cls;
            }
            else if (!line.ItemClass.Equals(cls, StringComparison.OrdinalIgnoreCase))
            {
                throw GlReq.Bad(name + " 的项目大类是 " + cls + "，不是 " + line.ItemClass);
            }
        }

        static void Want(bool needed, string value, string label, string name)
        {
            if (needed && value.Length == 0)
            {
                throw GlReq.Bad(name + " 需要填写" + label);
            }
            if (!needed && value.Length > 0)
            {
                throw GlReq.Bad(name + " 不核算" + label + "，不能填写");
            }
        }

        static string PersonDept(object conn, string person, string at)
        {
            Dictionary<string, object> row = Rows.One(conn, "SELECT ISNULL(cDepCode,'') d FROM Person WHERE cPersonCode=?",
                new object[] { person });
            if (row == null)
            {
                throw GlReq.Bad(at + "人员 " + person + " 不存在");
            }
            return GlSql.Col(row, "d");
        }

        static void Refs(object conn, GlLine line, HashSet<string> seen, string at)
        {
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM Department WHERE cDepCode=? AND bDepEnd=1", line.Dept,
                at + "部门 " + line.Dept + " 不存在或不是末级部门");
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM Person WHERE cPersonCode=?", line.Person,
                at + "人员 " + line.Person + " 不存在");
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM Customer WHERE cCusCode=?", line.Customer,
                at + "客户 " + line.Customer + " 不存在");
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM Vendor WHERE cVenCode=?", line.Supplier,
                at + "供应商 " + line.Supplier + " 不存在");
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM SettleStyle WHERE cSSCode=?", line.Settle,
                at + "结算方式 " + line.Settle + " 不存在");
            foreach (GlFlow flow in line.Flows)
            {
                Exists(conn, seen, "SELECT TOP 1 'x' x FROM fitemss98 WHERE citemcode=? AND ISNULL(bclose,0)=0", flow.Item,
                    at + "现金流量项目 " + flow.Item + " 不存在或已关闭");
            }
        }

        static void Currency(object conn, GlLine line, Dictionary<string, object> acct, HashSet<string> seen, string at)
        {
            string exch = GlSql.Col(acct, "cexch_name");
            if (exch.Length == 0 && line.Currency.Length > 0)
            {
                throw GlReq.Bad(at + "科目 " + line.Account + " 不核算外币，不能填写币种");
            }
            if (exch.Length > 0 && !exch.Equals(line.Currency, StringComparison.OrdinalIgnoreCase))
            {
                throw GlReq.Bad(at + "科目 " + line.Account + " 核算外币 " + exch + "，需要填写该币种和汇率");
            }
            Exists(conn, seen, "SELECT TOP 1 'x' x FROM foreigncurrency WHERE cexch_name=?", line.Currency,
                at + "币种 " + line.Currency + " 不存在");
        }

        // 同一请求里同一个编码只查一次。键带上 SQL，避免不同档案的同名编码互相顶替。
        static void Exists(object conn, HashSet<string> seen, string sql, string code, string message)
        {
            if (code == null || code.Length == 0 || seen.Contains(sql + "\n" + code))
            {
                return;
            }
            if (Rows.Scalar(conn, sql, new object[] { code }) == null)
            {
                throw GlReq.Bad(message);
            }
            seen.Add(sql + "\n" + code);
        }

        // 制单序时（bMakShtSort）：新凭证日期不早于本期同类别最后一张；修改时夹在前后两张之间。
        public static void Order(object conn, GlKey key, string date, bool update)
        {
            if (!GlState.Option(conn, "bMakShtSort", true))
            {
                return;
            }
            const string Base = " FROM GL_accvouch WHERE iyear=? AND iperiod=? AND csign=?";
            object[] args = new object[] { key.Year, key.Period, key.Sign };
            string before = update
                ? Rows.Scalar(conn, "SELECT CONVERT(varchar(10), MAX(dbill_date), 23) d" + Base + " AND ino_id<?", GlSql.With(args, new object[] { key.No }))
                : Rows.Scalar(conn, "SELECT CONVERT(varchar(10), MAX(dbill_date), 23) d" + Base, args);
            if (before != null && string.CompareOrdinal(date, before.Trim()) < 0)
            {
                throw GlState.Refuse("制单序时控制：凭证日期不能早于本期 " + key.Sign + " 字前一张凭证的日期 " + before.Trim());
            }
            if (!update)
            {
                return;
            }
            string after = Rows.Scalar(conn, "SELECT CONVERT(varchar(10), MIN(dbill_date), 23) d" + Base + " AND ino_id>?",
                GlSql.With(args, new object[] { key.No }));
            if (after != null && string.CompareOrdinal(date, after.Trim()) > 0)
            {
                throw GlState.Refuse("制单序时控制：凭证日期不能晚于本期 " + key.Sign + " 字后一张凭证的日期 " + after.Trim());
            }
        }
    }
}
