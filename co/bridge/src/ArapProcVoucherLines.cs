using System;
using System.Collections.Generic;

namespace U8Co
{
    // 处理制单的科目校验和辅助核算（同 ArapVoucherLines 的规则，往来单位按明细行所在的表）：科目存在、末级、未封存、不是现金流量科目；
    // 只按科目要求填辅助项（客户：应收明细行的 cDwCode；供应商：应付明细行的 cDwCode；部门、个人、项目取明细行），缺了 409。
    // 外币：9I / 9J / BZ 的科目不能核算外币；9M 的往来科目可以（凭证行写币种、原币 0，同 U8），汇兑损益科目 pl_code 必须是本币末级科目（400）。
    internal static class ArapProcVoucherLines
    {
        const string AccountSql = "select ccode, convert(int, isnull(bend,0)) bend, convert(int, isnull(bclose,0)) bclose, "
            + "convert(int, isnull(bdept,0)) bdept, convert(int, isnull(bperson,0)) bperson, convert(int, isnull(bcus,0)) bcus, "
            + "convert(int, isnull(bsup,0)) bsup, convert(int, isnull(bitem,0)) bitem, isnull(cass_item,N'') cass_item, "
            + "convert(int, isnull(bCashItem,0)) bcashitem, isnull(cother,N'') cother, isnull(cexch_name,N'') cexch_name "
            + "from code where iyear=? and ccode=?";

        // 汇兑损益科目：存在、末级、未封存、本币。不符 400 pl_code。
        public static void CheckPl(object conn, int year, string code)
        {
            Dictionary<string, object> acct = Rows.One(conn, AccountSql, new object[] { year, code });
            string why = null;
            if (acct == null)
            {
                why = "不存在";
            }
            else if (!GlSql.Bit(acct, "bend"))
            {
                why = "不是末级科目";
            }
            else if (GlSql.Bit(acct, "bclose"))
            {
                why = "已封存";
            }
            else if (GlSql.Col(acct, "cexch_name").Length > 0)
            {
                why = "核算外币，汇兑损益科目必须是本币科目";
            }
            if (why != null)
            {
                throw new BridgeException(400, "bad_request", "pl_code 科目 " + code + " " + why, "pl_code");
            }
        }

        public static List<ProcLine> Build(object conn, ProcVoucherAsk ask, List<ProcPart> parts, int year)
        {
            Dictionary<string, Dictionary<string, object>> cache = new Dictionary<string, Dictionary<string, object>>(
                StringComparer.OrdinalIgnoreCase);
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.Ordinal);
            List<ProcLine> lines = new List<ProcLine>();
            foreach (ProcPart part in parts)
            {
                Dictionary<string, object> acct = Account(conn, year, part.Account, cache, ask.Notes || ask.Style == "9H");
                ProcLine line = new ProcLine();
                line.Line = NewLine(part);
                line.VType = part.VType.Length > 0 ? part.VType : part.Row.VType;
                line.VId = part.VId.Length > 0 ? part.VId : part.Row.VId;
                line.Pl = part.Pl;
                line.Controlled = GlSql.Col(acct, "cother").Length > 0;
                line.CashItem = GlSql.Bit(acct, "bcashitem");
                string fc = GlSql.Col(acct, "cexch_name");
                if (fc.Length > 0)
                {
                    Need(ask.ExchangeGain && !part.Pl, "科目 " + part.Account + " 核算外币，外币处理制单暂不支持");
                    line.Fc = fc;
                    line.Line.Currency = fc;
                }
                Aux(conn, acct, part.Row, line.Line);
                if (!part.Pl)
                {
                    line.Rows.Add(part.Row.Key);
                }
                if (line.Line.Customer.Length > 0 || line.Line.Supplier.Length > 0)
                {
                    line.Op = PersonName(conn, part.Row.Person, names);
                }
                lines.Add(line);
            }
            return lines;
        }

        // 给档案存在性核对（ArapVoucherRefs.Check）用的行。
        public static List<VoucherRow> AsRows(List<ProcLine> lines)
        {
            List<VoucherRow> rows = new List<VoucherRow>();
            foreach (ProcLine line in lines)
            {
                VoucherRow row = new VoucherRow();
                row.Line = line.Line;
                rows.Add(row);
            }
            return rows;
        }

        static GlLine NewLine(ProcPart part)
        {
            GlLine line = new GlLine();
            line.Account = part.Account;
            line.Digest = part.Digest;
            line.Debit = part.Debit ? part.Amount : 0;
            line.Credit = part.Debit ? 0 : part.Amount;
            line.Dept = "";
            line.Person = "";
            line.Customer = "";
            line.Supplier = "";
            line.ItemClass = "";
            line.Item = "";
            line.Settle = "";
            line.DocNo = "";
            line.DocDate = "";
            line.Currency = "";
            return line;
        }

        // cash：放行现金流量科目（票据处理的银行科目、票据科目常设为现金流量科目，U8 照常制单；坏账收回 9H 的结算科目同理）。
        static Dictionary<string, object> Account(object conn, int year, string code,
            Dictionary<string, Dictionary<string, object>> cache, bool cash)
        {
            Dictionary<string, object> acct;
            if (cache.TryGetValue(code, out acct))
            {
                return acct;
            }
            acct = Rows.One(conn, AccountSql, new object[] { year, code });
            string name = "科目 " + code;
            Need(acct != null, name + " 不存在");
            Need(GlSql.Bit(acct, "bend"), name + " 不是末级科目");
            Need(!GlSql.Bit(acct, "bclose"), name + " 已封存");
            Need(cash || !GlSql.Bit(acct, "bcashitem"), name + " 是现金流量科目，请在 U8 客户端制单");
            cache[code] = acct;
            return acct;
        }

        static void Aux(object conn, Dictionary<string, object> acct, ProcVoucherRow row, GlLine line)
        {
            string name = "科目 " + line.Account;
            if (GlSql.Bit(acct, "bcus"))
            {
                Need(row.Ledger == "AR", name + " 核算客户，但明细行（" + row.VId + "）的往来单位是供应商");
                line.Customer = Need(row.Dw, name + " 核算客户，明细行没有客户");
            }
            if (GlSql.Bit(acct, "bsup"))
            {
                Need(row.Ledger == "AP", name + " 核算供应商，但明细行（" + row.VId + "）的往来单位是客户");
                line.Supplier = Need(row.Dw, name + " 核算供应商，明细行没有供应商");
            }
            if (GlSql.Bit(acct, "bperson"))
            {
                line.Person = Need(row.Person, name + " 核算个人，明细行没有业务员");
                line.Dept = row.Dept.Length > 0 ? row.Dept : PersonDept(conn, row.Person);
                Need(line.Dept, name + " 核算个人，业务员没有部门");
            }
            if (GlSql.Bit(acct, "bdept"))
            {
                line.Dept = Need(line.Dept.Length > 0 ? line.Dept : row.Dept, name + " 核算部门，明细行没有部门");
            }
            if (GlSql.Bit(acct, "bitem"))
            {
                Item(conn, GlSql.Col(acct, "cass_item"), row, line, name);
            }
        }

        static void Item(object conn, string cls, ProcVoucherRow row, GlLine line, string name)
        {
            string table = Rows.Scalar(conn, "select isnull(ctable,N'') from fitem where citem_class=?", new object[] { cls }) ?? "";
            line.ItemClass = cls;
            if (table.Trim().Equals("inventory", StringComparison.OrdinalIgnoreCase))
            {
                line.Item = Need(row.Inv, name + " 核算存货项目，明细行没有存货");
                return;
            }
            bool same = row.ItemClass.Equals(cls, StringComparison.OrdinalIgnoreCase);
            line.Item = Need(same ? row.ItemCode : "", name + " 核算项目（大类 " + cls + "），明细行没有该大类的项目");
        }

        static string PersonDept(object conn, string person)
        {
            return (Rows.Scalar(conn, "select isnull(cDepCode,N'') from Person where cPersonCode=?", new object[] { person }) ?? "").Trim();
        }

        static string PersonName(object conn, string person, Dictionary<string, string> names)
        {
            if (person.Length == 0)
            {
                return "-";
            }
            string name;
            if (!names.TryGetValue(person, out name))
            {
                name = (Rows.Scalar(conn, "select isnull(cPersonName,N'') from Person where cPersonCode=?", new object[] { person }) ?? "").Trim();
                names[person] = name;
            }
            return name.Length > 0 ? name : "-";
        }

        static void Need(bool ok, string message)
        {
            if (!ok)
            {
                throw ArapVoucherDoc.Refuse(message);
            }
        }

        static string Need(string value, string message)
        {
            if (value == null || value.Length == 0)
            {
                throw ArapVoucherDoc.Refuse(message);
            }
            return value;
        }
    }
}
