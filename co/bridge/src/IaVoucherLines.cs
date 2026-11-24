using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 合并中的一行分录。Key 是 U8 的 Group By（方向、科目、按科目要求的辅助项）。
    internal sealed class IaLine
    {
        public string Key = "";
        public GlLine Line;
        public IaLineSrc Src = new IaLineSrc();
        public decimal Qty;
        public bool Measure;
        public string Op = "";
        // 首次出现的先后（U8 插入临时表的顺序），排序键相同时按它，保证稳定。
        public int Seq;
    }

    // 拼一张凭证的分录（同 U8 frmMakePz 写 GL_accvouch 的 Select … Group By）：每行明细两边——存货科目（入库借、出库贷）、
    // 对方科目（反方向），金额是记账金额（入库 iAInPrice、出库 iAOutPrice，红字为负数、方向不变）。
    // 按（方向、科目、科目要求的辅助项；核算个人 / 客户 / 供应商的科目另按业务员；存货方与其他方分开）合并、金额为 0 的行去掉；
    // 借方在前，再按科目、部门、个人、供应商、客户、项目大类、项目排，相同时按首次出现的先后（同 U8 的 inid）。
    // 辅助项只按科目要求填（部门：核算部门或个人；个人；客户；供应商；项目——存货大类取存货编码），值取明细行（空时取单据表头），缺了 409。
    // 数量只写在有计量单位的科目上。科目要求凭证自定义项（bcDefineN）时 409：桥不搬单据的自定义项。
    internal static class IaVoucherLines
    {
        const string AccountSql = "select ccode, convert(int, isnull(bend,0)) bend, convert(int, isnull(bclose,0)) bclose, "
            + "convert(int, isnull(bdept,0)) bdept, convert(int, isnull(bperson,0)) bperson, convert(int, isnull(bcus,0)) bcus, "
            + "convert(int, isnull(bsup,0)) bsup, convert(int, isnull(bitem,0)) bitem, isnull(cass_item,N'') cass_item, "
            + "isnull(cother,N'') cother, isnull(cexch_name,N'') cexch_name, isnull(cmeasure,N'') cmeasure, "
            // bit 列不能直接相加（SQL Server 报「bit 和 bit 在 add 运算符中不兼容」），逐列转 int。
            + "convert(int, isnull(bcDefine1,0))+convert(int, isnull(bcDefine2,0))+convert(int, isnull(bcDefine3,0))"
            + "+convert(int, isnull(bcDefine4,0))+convert(int, isnull(bcDefine5,0))+convert(int, isnull(bcDefine6,0))"
            + "+convert(int, isnull(bcDefine7,0))+convert(int, isnull(bcDefine8,0))+convert(int, isnull(bcDefine9,0))"
            + "+convert(int, isnull(bcDefine10,0))+convert(int, isnull(bcDefine11,0))+convert(int, isnull(bcDefine12,0))"
            + "+convert(int, isnull(bcDefine13,0))+convert(int, isnull(bcDefine14,0))+convert(int, isnull(bcDefine15,0))"
            + "+convert(int, isnull(bcDefine16,0)) defs "
            + "from code where iyear=? and ccode=?";
        public const int LineMax = 500;

        // 科目、项目大类、人员姓名的缓存（一次请求内）。
        internal sealed class Cache
        {
            public Dictionary<string, Dictionary<string, object>> Accounts =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);
            public Dictionary<string, string> Tables = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            public Dictionary<string, string> Names = new Dictionary<string, string>(StringComparer.Ordinal);
        }

        // 一张凭证合并中的状态。
        sealed class LineSet
        {
            public object Conn;
            public int Year;
            public Cache Cache;
            public List<IaLine> Merged = new List<IaLine>();
            public Dictionary<string, IaLine> ByKey = new Dictionary<string, IaLine>(StringComparer.Ordinal);
        }

        public static List<IaLine> Build(object conn, IaPlan plan, int year, Cache cache)
        {
            LineSet set = new LineSet();
            set.Conn = conn;
            set.Year = year;
            set.Cache = cache;
            foreach (IaBill bill in plan.Bills)
            {
                foreach (IaRow row in bill.Rows)
                {
                    Add(set, bill, row, true);
                    Add(set, bill, row, false);
                }
            }
            List<IaLine> kept = new List<IaLine>();
            foreach (IaLine line in set.Merged)
            {
                if (line.Line.Debit != 0 || line.Line.Credit != 0)
                {
                    line.Line.Qty = line.Measure ? line.Qty : 0;
                    kept.Add(line);
                }
            }
            kept.Sort(CompareLines);
            Check(kept);
            return kept;
        }

        // 一行明细的一边（inv=true 存货科目，false 对方科目），并进同键的分录。
        static void Add(LineSet set, IaBill bill, IaRow row, bool inv)
        {
            string account = inv ? row.InHead : row.OppHead;
            bool debit = inv == row.Type.In;
            Dictionary<string, object> acct = Account(set.Conn, set.Year, account, set.Cache);
            GlLine probe = Aux(set.Conn, bill, row, acct, set.Cache);
            string person = Named(acct) ? row.Person : "";
            // 存货方和发出商品方（3201 的对方）U8 都锁科目（bcodeedit=0，生产数据核对），另成一组。
            bool locked = inv || row.Type.Opp == "out";
            IaLine line = Find(set, KeyOf(probe, debit, person, locked), probe, acct);
            line.Src.CodeEditable = !locked;
            if (debit)
            {
                line.Line.Debit += row.Amount;
            }
            else
            {
                line.Line.Credit += row.Amount;
            }
            line.Qty += row.Qty;
            line.Line.Digest = IaText.MaxText(line.Line.Digest, row.Digest);
            line.Src.BillType = IaText.MaxCode(line.Src.BillType, row.Type.Code);
            line.Src.BillCode = IaText.MaxCode(line.Src.BillCode, row.Code);
            line.Src.BillDate = IaText.MaxCode(line.Src.BillDate, row.VDate);
            if (person.Length > 0)
            {
                line.Op = IaText.MaxText(line.Op, PersonName(set.Conn, person, set.Cache));
            }
        }

        // 科目核算个人、客户或供应商：U8 按业务员分组并写 cname。
        static bool Named(Dictionary<string, object> acct)
        {
            return GlSql.Bit(acct, "bperson") || GlSql.Bit(acct, "bcus") || GlSql.Bit(acct, "bsup");
        }

        // U8 的 Group By：方向、科目、科目要求的辅助项、业务员（科目核算个人 / 客户 / 供应商时）、锁科目的一方与其他方（bcodeedit）。
        internal static string KeyOf(GlLine probe, bool debit, string person, bool locked)
        {
            return string.Join("\u0001", new string[]
            {
                debit ? "D" : "C", probe.Account.ToUpperInvariant(), probe.Dept, probe.Person, probe.Customer, probe.Supplier,
                probe.ItemClass, probe.Item, person, locked ? "I" : "O"
            });
        }

        static IaLine Find(LineSet set, string key, GlLine probe, Dictionary<string, object> acct)
        {
            IaLine line;
            if (!set.ByKey.TryGetValue(key, out line))
            {
                line = New(key, probe, acct);
                line.Seq = set.Merged.Count;
                set.ByKey[key] = line;
                set.Merged.Add(line);
            }
            return line;
        }

        static IaLine New(string key, GlLine probe, Dictionary<string, object> acct)
        {
            IaLine line = new IaLine();
            line.Key = key;
            line.Line = probe;
            line.Measure = GlSql.Col(acct, "cmeasure").Length > 0;
            string other = GlSql.Col(acct, "cother");
            bool ia = other.ToUpperInvariant().IndexOf("IA", StringComparison.Ordinal) >= 0;
            // 同 U8：cOther 是 AP 或含 IA 时 ccodecontrol 取 cOther，否则 IA,FA,#；含 IA 的科目分录不可改金额（bvalueedit=0）。
            line.Src.Control = other == "AP" || ia ? other : "IA,FA,#";
            line.Src.Editable = !ia;
            return line;
        }

        // 只按科目要求填辅助项，返回一行空金额的分录（作合并键和最终分录用）。
        static GlLine Aux(object conn, IaBill bill, IaRow row, Dictionary<string, object> acct, Cache cache)
        {
            GlLine line = Empty(GlSql.Col(acct, "ccode"));
            string name = "单据 " + bill.Label() + "：科目 " + line.Account;
            bool person = GlSql.Bit(acct, "bperson");
            if (GlSql.Bit(acct, "bdept") || person)
            {
                line.Dept = Need(row.Dept, name + (person ? " 核算个人" : " 核算部门") + "，单据没有部门");
            }
            if (person)
            {
                line.Person = Need(row.Person, name + " 核算个人，单据没有业务员");
            }
            if (GlSql.Bit(acct, "bcus"))
            {
                line.Customer = Need(row.Customer, name + " 核算客户，单据没有客户");
            }
            if (GlSql.Bit(acct, "bsup"))
            {
                line.Supplier = Need(row.Vendor, name + " 核算供应商，单据没有供应商");
            }
            if (GlSql.Bit(acct, "bitem"))
            {
                Item(conn, GlSql.Col(acct, "cass_item"), row, line, cache);
                Need(line.Item, name + " 核算项目（大类 " + line.ItemClass + "），单据没有该大类的项目");
            }
            return line;
        }

        // 项目：明细行上有同一大类的项目就用它，否则大类是存货时取存货编码。
        static void Item(object conn, string cls, IaRow row, GlLine line, Cache cache)
        {
            line.ItemClass = cls;
            if (row.ItemClass.Equals(cls, StringComparison.OrdinalIgnoreCase) && row.Item.Length > 0)
            {
                line.Item = row.Item;
                return;
            }
            line.Item = Inventory(conn, cls, cache) ? row.Inv : "";
        }

        static GlLine Empty(string account)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Digest = "";
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

        // 项目大类是存货（fitem.ctable = inventory）时项目取存货编码（同 U8 cItemClass='ch' 时 isnull(cItemCode, cInvCode)）。
        static bool Inventory(object conn, string cls, Cache cache)
        {
            string table;
            if (!cache.Tables.TryGetValue(cls, out table))
            {
                table = (Rows.Scalar(conn, "select isnull(ctable,N'') from fitem where citem_class=?", new object[] { cls }) ?? "").Trim();
                cache.Tables[cls] = table;
            }
            return table.Equals("inventory", StringComparison.OrdinalIgnoreCase);
        }

        static Dictionary<string, object> Account(object conn, int year, string code, Cache cache)
        {
            Dictionary<string, object> acct;
            if (cache.Accounts.TryGetValue(code, out acct))
            {
                return acct;
            }
            acct = Rows.One(conn, AccountSql, new object[] { year, code });
            string name = "科目 " + code;
            Must(acct != null, name + " 不存在");
            Must(GlSql.Bit(acct, "bend"), name + " 不是末级科目");
            Must(!GlSql.Bit(acct, "bclose"), name + " 已封存");
            Must(GlSql.Col(acct, "cexch_name").Length == 0, name + " 核算外币，外币制单暂不支持");
            Must(GlSql.Col(acct, "defs") == "0", name + " 要求凭证自定义项，桥不搬单据的自定义项，请在 U8 客户端制单");
            cache.Accounts[code] = acct;
            return acct;
        }

        static string PersonName(object conn, string person, Cache cache)
        {
            string name;
            if (!cache.Names.TryGetValue(person, out name))
            {
                name = (Rows.Scalar(conn, "select isnull(cPersonName,N'') from Person where cPersonCode=?", new object[] { person }) ?? "").Trim();
                cache.Names[person] = name;
            }
            return name;
        }

        // 借方在前（借方红字也算借方），再按科目、部门、个人、供应商、客户、项目大类、项目（同 U8 的 Order by）。
        internal static int CompareLines(IaLine a, IaLine b)
        {
            int side = (a.Line.Debit != 0 ? 0 : 1) - (b.Line.Debit != 0 ? 0 : 1);
            if (side != 0)
            {
                return side;
            }
            string[] x = new string[] { a.Line.Account, a.Line.Dept, a.Line.Person, a.Line.Supplier, a.Line.Customer, a.Line.ItemClass, a.Line.Item };
            string[] y = new string[] { b.Line.Account, b.Line.Dept, b.Line.Person, b.Line.Supplier, b.Line.Customer, b.Line.ItemClass, b.Line.Item };
            for (int i = 0; i < x.Length; i++)
            {
                int cmp = string.Compare(x[i], y[i], StringComparison.OrdinalIgnoreCase);
                if (cmp != 0)
                {
                    return cmp;
                }
            }
            return a.Seq.CompareTo(b.Seq);
        }

        // 借贷相等、有发生额、行数 2 到 LineMax。
        internal static void Check(List<IaLine> lines)
        {
            decimal debit = 0;
            decimal credit = 0;
            foreach (IaLine line in lines)
            {
                debit += line.Line.Debit;
                credit += line.Line.Credit;
            }
            if (lines.Count < 2)
            {
                throw IaVoucherLoad.Refuse("单据金额合计为 0，没有可生成的分录");
            }
            if (lines.Count > LineMax)
            {
                throw IaVoucherLoad.Refuse("凭证分录 " + lines.Count.ToString(CultureInfo.InvariantCulture) + " 行，超过 "
                    + LineMax.ToString(CultureInfo.InvariantCulture) + " 行；请改为逐张制单或分批");
            }
            if (debit != credit)
            {
                throw IaVoucherLoad.Refuse("拼出的凭证借贷不平（借 " + ArapVoucherAcct.Text(debit) + "，贷 " + ArapVoucherAcct.Text(credit) + "）");
            }
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw IaVoucherLoad.Refuse(message);
            }
        }

        static string Need(string value, string message)
        {
            if (value == null || value.Length == 0)
            {
                throw IaVoucherLoad.Refuse(message);
            }
            return value;
        }
    }
}
