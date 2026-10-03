using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一行分录连同合并、回写要用的信息。
    internal sealed class VoucherRow
    {
        public GlLine Line;
        // 这一行分录汇总了哪些往来明细行（Auto_ID），合并时一起并过来。
        public List<string> Aids = new List<string>();
        public string Op = "";
        public bool Marked;
        // 来自第几张单据（VoucherPlan.Docs 的下标）；合并制单不跨单据合并分录。
        public int Bill;
        public bool Controlled;
        public bool Cash;
        public bool CashItem;
    }

    // 科目校验和辅助核算：科目要存在、末级、未封存、不核算外币；只按科目要求（code 的 bcus / bsup / bdept / bperson /
    // bitem）填辅助项，值取自单据，缺了 409，不去猜。往来单位按 flag 只能是客户（AR）或供应商（AP）。
    // 项目大类是「存货核算」（fitem.ctable = inventory）时项目取存货编码，其余大类要单据行上带了同一大类的项目。
    // 有客户或供应商辅助项的行另写业务员姓名（cname，同 U8 写入的 GL_accvouch；单据没有业务员写「-」）。
    internal static class ArapVoucherLines
    {
        const string AccountSql = "select ccode, convert(int, isnull(bend,0)) bend, convert(int, isnull(bclose,0)) bclose, "
            + "convert(int, isnull(bdept,0)) bdept, convert(int, isnull(bperson,0)) bperson, convert(int, isnull(bcus,0)) bcus, "
            + "convert(int, isnull(bsup,0)) bsup, convert(int, isnull(bitem,0)) bitem, isnull(cass_item,N'') cass_item, "
            + "convert(int, isnull(bcash,0)) bcash, convert(int, isnull(bbank,0)) bbank, convert(int, isnull(bCashItem,0)) bcashitem, "
            + "isnull(cother,N'') cother, isnull(cexch_name,N'') cexch_name from code where iyear=? and ccode=?";

        public static List<VoucherRow> Build(object conn, VoucherDoc doc, List<VoucherPart> parts, int year)
        {
            Dictionary<string, Dictionary<string, object>> cache = new Dictionary<string, Dictionary<string, object>>(
                StringComparer.OrdinalIgnoreCase);
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.Ordinal);
            List<VoucherRow> rows = new List<VoucherRow>();
            foreach (VoucherPart part in parts)
            {
                Dictionary<string, object> acct = Account(conn, year, part.Account, cache);
                VoucherRow row = new VoucherRow();
                row.Line = NewLine(part);
                Aux(conn, doc.Flag, acct, part.Ctx, row.Line);
                bool cash = GlSql.Bit(acct, "bcash") || GlSql.Bit(acct, "bbank");
                row.Cash = cash;
                row.CashItem = GlSql.Bit(acct, "bcashitem");
                row.Controlled = GlSql.Col(acct, "cother").Length > 0;
                row.Marked = part.Kind == ArapVoucherBuild.DwPart || (part.Kind == ArapVoucherBuild.SettlePart && !cash);
                if (part.Aid.Length > 0)
                {
                    row.Aids.Add(part.Aid);
                }
                if (row.Line.Customer.Length > 0 || row.Line.Supplier.Length > 0)
                {
                    row.Op = PersonName(conn, part.Ctx.Person, names);
                }
                rows.Add(row);
            }
            return rows;
        }

        static GlLine NewLine(VoucherPart part)
        {
            GlLine line = new GlLine();
            line.Account = part.Account;
            line.Digest = "";
            line.Debit = part.Debit ? part.Amount : 0;
            line.Credit = part.Debit ? 0 : part.Amount;
            line.Dept = "";
            line.Person = "";
            line.Customer = "";
            line.Supplier = "";
            line.ItemClass = "";
            line.Item = "";
            line.Settle = part.Settle;
            line.DocNo = part.DocNo;
            line.DocDate = part.DocDate;
            line.Currency = "";
            return line;
        }

        static Dictionary<string, object> Account(object conn, int year, string code,
            Dictionary<string, Dictionary<string, object>> cache)
        {
            Dictionary<string, object> acct;
            if (cache.TryGetValue(code, out acct))
            {
                return acct;
            }
            if (code.Length == 0)
            {
                throw ArapVoucherDoc.Refuse("往来明细或单据表体上缺科目，请在 U8 客户端制单");
            }
            acct = Rows.One(conn, AccountSql, new object[] { year, code });
            string name = "科目 " + code;
            if (acct == null)
            {
                throw ArapVoucherDoc.Refuse(name + " 不存在");
            }
            if (!GlSql.Bit(acct, "bend"))
            {
                throw ArapVoucherDoc.Refuse(name + " 不是末级科目");
            }
            if (GlSql.Bit(acct, "bclose"))
            {
                throw ArapVoucherDoc.Refuse(name + " 已封存");
            }
            if (GlSql.Col(acct, "cexch_name").Length > 0)
            {
                throw ArapVoucherDoc.Refuse(name + " 核算外币，外币制单暂不支持");
            }
            cache[code] = acct;
            return acct;
        }

        static void Aux(object conn, string flag, Dictionary<string, object> acct, VoucherCtx ctx, GlLine line)
        {
            string name = "科目 " + line.Account;
            if (GlSql.Bit(acct, "bcus"))
            {
                Need(flag == "AR", name + " 核算客户，但单据的往来单位是供应商");
                line.Customer = Need(ctx.Dw, name + " 核算客户，单据没有客户");
            }
            if (GlSql.Bit(acct, "bsup"))
            {
                Need(flag == "AP", name + " 核算供应商，但单据的往来单位是客户");
                line.Supplier = Need(ctx.Dw, name + " 核算供应商，单据没有供应商");
            }
            if (GlSql.Bit(acct, "bperson"))
            {
                line.Person = Need(ctx.Person, name + " 核算个人，单据没有业务员");
                line.Dept = ctx.Dept.Length > 0 ? ctx.Dept : PersonDept(conn, ctx.Person);
                Need(line.Dept, name + " 核算个人，业务员没有部门");
            }
            if (GlSql.Bit(acct, "bdept"))
            {
                line.Dept = Need(line.Dept.Length > 0 ? line.Dept : ctx.Dept, name + " 核算部门，单据没有部门");
            }
            if (GlSql.Bit(acct, "bitem"))
            {
                Item(conn, GlSql.Col(acct, "cass_item"), ctx, line, name);
            }
        }

        static void Item(object conn, string cls, VoucherCtx ctx, GlLine line, string name)
        {
            string table = Rows.Scalar(conn, "select isnull(ctable,N'') from fitem where citem_class=?", new object[] { cls }) ?? "";
            line.ItemClass = cls;
            if (table.Trim().Equals("inventory", StringComparison.OrdinalIgnoreCase))
            {
                line.Item = Need(ctx.Inventory, name + " 核算存货项目，单据行没有存货");
                return;
            }
            bool same = ctx.ItemClass.Equals(cls, StringComparison.OrdinalIgnoreCase);
            line.Item = Need(same ? ctx.ItemCode : "", name + " 核算项目（大类 " + cls + "），单据没有该大类的项目");
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
