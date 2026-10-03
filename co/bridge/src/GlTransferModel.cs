using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一张要生成的结转凭证。期间损益按（类别、本年利润科目、收入 / 支出）各一张；自定义转账每个转账序号一张。
    internal sealed class GlTransferVoucher
    {
        public string Sign = "";
        public string Digest = "";
        // 自定义转账的转账序号；期间损益为空。
        public string TranId = "";
        // 期间损益：income（贷方性质的损益科目）/ expense（借方性质）；自定义转账为空。
        public string Pack = "";
        public GlDraft Draft;
        public GlKey Key;
        public int Seq;
        public string OutNo = "";
    }

    // 生成计划：要生成的凭证、跳过的定义（原因），核对模式下从余额里去掉的已有凭证。
    internal sealed class GlTransferPlan
    {
        public GlTransferAsk Ask;
        public string Date;
        public string Maker;
        public List<GlTransferVoucher> Vouchers = new List<GlTransferVoucher>();
        public List<object> Skipped = new List<object>();
        public List<object> Excluded = new List<object>();
        // 期间损益补生成时本期已有的期间损益凭证（[{sign, no, pack}]，GlTransferPnlDone）。
        public List<object> Existing = new List<object>();
        // 操作员的权限快照（GlTransferPerm 按它查数据权限）。
        public PermContext Perm;

        public void Skip(string tranId, string account, string reason)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            if (tranId.Length > 0)
            {
                one["tran_id"] = tranId;
            }
            if (account.Length > 0)
            {
                one["account"] = account;
            }
            one["reason"] = reason;
            Skipped.Add(one);
        }

        // 跳过原因合在一句里（全部跳过时 409 的消息）。
        public string SkipText()
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < Skipped.Count && i < 5; i++)
            {
                Dictionary<string, object> one = (Dictionary<string, object>)Skipped[i];
                object id;
                object account;
                string head = one.TryGetValue("tran_id", out id) ? id + " " : "";
                head += one.TryGetValue("account", out account) ? account + " " : "";
                parts.Add(head + one["reason"]);
            }
            return string.Join("；", parts.ToArray());
        }
    }

    // 科目档案（当年 code 表），按编码不分大小写取。辅助核算按 GlCheck 的规则：个人往来同时要部门。
    internal sealed class GlTransferCodes
    {
        const string Sql = "SELECT ccode, CONVERT(int, ISNULL(bend,0)) bend, CONVERT(int, ISNULL(bproperty,1)) bproperty,"
            + " ISNULL(cclass,N'') cclass, CONVERT(int, ISNULL(bdept,0)) bdept, CONVERT(int, ISNULL(bperson,0)) bperson,"
            + " CONVERT(int, ISNULL(bcus,0)) bcus, CONVERT(int, ISNULL(bsup,0)) bsup, CONVERT(int, ISNULL(bitem,0)) bitem,"
            + " ISNULL(cass_item,N'') cass_item FROM code WHERE iyear=?";
        const int MaxCodes = 100000;
        readonly Dictionary<string, Dictionary<string, object>> _map =
            new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);

        public static GlTransferCodes Load(object conn, int year)
        {
            GlTransferCodes codes = new GlTransferCodes();
            foreach (Dictionary<string, object> row in Rows.Query(conn, Sql, new object[] { year }, MaxCodes))
            {
                codes.Add(row);
            }
            return codes;
        }

        internal void Add(Dictionary<string, object> row)
        {
            _map[GlSql.Col(row, "ccode")] = row;
        }

        public Dictionary<string, object> Find(string code)
        {
            Dictionary<string, object> row;
            return code != null && _map.TryGetValue(code.Trim(), out row) ? row : null;
        }

        public bool Leaf(string code)
        {
            return GlSql.Bit(Find(code), "bend");
        }

        // 科目的项目大类（cass_item）；查不到为空串。
        public string ItemClass(string code)
        {
            return GlSql.Col(Find(code), "cass_item");
        }

        // 借方性质（bproperty=1）；查不到按借方。
        public bool DebitNature(string code)
        {
            Dictionary<string, object> row = Find(code);
            return row == null || GlSql.Int(row, "bproperty") != 0;
        }

        // 该科目要哪些辅助核算维度（GlTransferBal.AuxCols 的下标）：部门、人员、客户、供应商、项目大类、项目。
        public bool[] Dims(string code)
        {
            Dictionary<string, object> row = Find(code);
            bool person = GlSql.Bit(row, "bperson");
            bool item = GlSql.Bit(row, "bitem");
            return new bool[]
            {
                GlSql.Bit(row, "bdept") || person, person, GlSql.Bit(row, "bcus"), GlSql.Bit(row, "bsup"), item, item
            };
        }

        public static bool Any(bool[] dims)
        {
            foreach (bool on in dims)
            {
                if (on)
                {
                    return true;
                }
            }
            return false;
        }
    }

    // GL_bautotran 的一行。用 SELECT * 读（列很多，固定辅助项列按名字取，不分大小写；没有的列为空）。
    internal sealed class GlTransferDef
    {
        public string TranId;
        public int Inid;
        public string Sign;
        public string Text;
        public string Code;
        public string Digest;
        // bd_c：1 借方，0 贷方（CE() 行与 QM 行的方向已在测试账套核对）。
        public bool Debit;
        public string Formula;
        // 定义行上固定的辅助项，下标同 GlTransferBal.AuxCols。
        public string[] Aux = new string[6];

        public static GlTransferDef Of(Dictionary<string, object> raw)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(raw, StringComparer.OrdinalIgnoreCase);
            GlTransferDef def = new GlTransferDef();
            def.TranId = GlSql.Col(row, "ctran_id");
            def.Inid = GlSql.Int(row, "inid");
            def.Sign = GlSql.Col(row, "csign");
            def.Text = GlSql.Col(row, "ctext");
            def.Code = GlSql.Col(row, "ccode");
            def.Digest = GlSql.Col(row, "cdigest");
            string side = GlSql.Col(row, "bd_c");
            def.Debit = side == "1" || side.Equals("true", StringComparison.OrdinalIgnoreCase);
            def.Formula = GlSql.Col(row, "cformula");
            for (int i = 0; i < GlTransferBal.AuxCols.Length; i++)
            {
                def.Aux[i] = GlSql.Col(row, GlTransferBal.AuxCols[i]);
            }
            return def;
        }

        // 转账序号排序：都是数字时按数值（期间损益 1…148），否则按文本。
        public static int Order(string a, string b)
        {
            long x;
            long y;
            if (long.TryParse(a, out x) && long.TryParse(b, out y))
            {
                return x.CompareTo(y);
            }
            return string.CompareOrdinal(a, b);
        }
    }
}
