using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字冲销的原凭证分录：照 U8 自己做的冲销凭证（红蓝分录逐行对照）抄成红字分录：
    // 科目、辅助核算（部门、人员、客户、供应商、项目）、结算方式、票号、票据日期、币种、汇率、原始单据、业务员、备注原样复制；
    // 本币、原币、数量取负（单价不变）；现金流量项目原样复制、金额取负；摘要前加「[冲销yyyy.MM.dd 类别-NNNN号凭证]」，最多 120 字。
    internal static class GlReverseSrc
    {
        const int MaxLines = 10000;
        const int DigestMax = 120;

        // 金额、原币按字符串取（Rows 的约定），数量与汇率转成定点小数再取，不经 float。
        internal const string LineSql = "SELECT inid, ISNULL(ccode,'') ccode, ISNULL(cdigest,'') dg,"
            + " CONVERT(varchar(40), ISNULL(md,0)) md, CONVERT(varchar(40), ISNULL(mc,0)) mc,"
            + " CONVERT(varchar(40), ISNULL(md_f,0)) md_f, CONVERT(varchar(40), ISNULL(mc_f,0)) mc_f,"
            + " CONVERT(varchar(40), CONVERT(decimal(28,6), ISNULL(nd_s,0))) nd, CONVERT(varchar(40), CONVERT(decimal(28,6), ISNULL(nc_s,0))) nc,"
            + " CONVERT(varchar(40), CONVERT(decimal(28,10), ISNULL(nfrat,0))) rate, ISNULL(cexch_name,'') exch,"
            + " ISNULL(cdept_id,'') dept, ISNULL(cperson_id,'') person, ISNULL(ccus_id,'') cus, ISNULL(csup_id,'') sup,"
            + " ISNULL(citem_class,'') icls, ISNULL(citem_id,'') item, ISNULL(csettle,'') settle, ISNULL(cn_id,'') docno,"
            + " CONVERT(varchar(10), dt_date, 23) docdate, ISNULL(cname,'') op, ISNULL(coutbillsign,'') bt, ISNULL(coutid,'') bid,"
            + " ISNULL(ctext1,'') t1, ISNULL(ctext2,'') t2, ISNULL(coutsign,'') outsign, ISNULL(coutno_id,'') outno,"
            + " ISNULL(cblueoutno_id,'') blue, ISNULL(idoc,0) idoc," + GlCarry.DefsCol
            + " FROM GL_accvouch" + GlState.KeyWhere + " ORDER BY inid";

        const string CashSql = "SELECT inid, ISNULL(cCashItem,'') item, CONVERT(varchar(40), ISNULL(md,0)) md,"
            + " CONVERT(varchar(40), ISNULL(mc,0)) mc FROM GL_CashTable"
            + " WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=? ORDER BY inid, i_id";

        public static List<Dictionary<string, object>> ReadLines(object conn, GlKey key)
        {
            return Rows.Query(conn, LineSql, GlSql.KeyArgs(key), MaxLines);
        }

        public static List<Dictionary<string, object>> ReadCash(object conn, GlKey key, int seq)
        {
            return Rows.Query(conn, CashSql, new object[] { key.Year, key.Period, seq, key.No }, MaxLines);
        }

        // 原凭证本身是红字冲销凭证、带自定义项、数量不在金额那一边、有原币却没有汇率（红字报文表达不了）时 409。
        public static void Gate(List<Dictionary<string, object>> rows)
        {
            if (rows.Count < 2)
            {
                throw GlState.Refuse("原凭证分录少于 2 行，不能冲销");
            }
            foreach (Dictionary<string, object> row in rows)
            {
                if (GlSql.Col(row, "blue").Length > 0)
                {
                    throw GlState.Refuse("凭证本身是红字冲销凭证，不能再冲销");
                }
                if (GlSql.Int(row, "defs") != 0)
                {
                    throw GlState.Refuse("凭证带自定义项，桥不复制，请在 U8 客户端冲销");
                }
                bool debit = GlSql.Money(row, "md") != 0;
                if (Dec(row, debit ? "nc" : "nd") != 0)
                {
                    throw GlState.Refuse("原凭证第 " + GlSql.Col(row, "inid") + " 行的数量与金额不在同一方向，请在 U8 客户端冲销");
                }
                if (FcWithoutRate(row))
                {
                    throw GlState.Refuse("原凭证第 " + GlSql.Col(row, "inid") + " 行有原币金额但汇率为 0，请在 U8 客户端冲销");
                }
            }
        }

        // 外币行的汇率为 0 而原币不为 0：红字分录只在汇率大于 0 时写原币（Line），保存后回读必然对不上，事先拒绝。
        static bool FcWithoutRate(Dictionary<string, object> row)
        {
            if (GlSql.Col(row, "exch").Length == 0 || Dec(row, "rate") > 0)
            {
                return false;
            }
            return GlSql.Money(row, "md_f") != 0 || GlSql.Money(row, "mc_f") != 0;
        }

        // 红字分录。srcKey、srcDate 用于摘要前缀。
        public static GlDraft Draft(GlKey srcKey, string srcDate, List<Dictionary<string, object>> rows,
            List<Dictionary<string, object>> cash)
        {
            GlDraft draft = new GlDraft();
            draft.Sign = srcKey.Sign;
            draft.Attachments = Math.Max(0, GlSql.Int(rows[0], "idoc"));
            string prefix = Prefix(srcKey, srcDate);
            foreach (Dictionary<string, object> row in rows)
            {
                draft.Lines.Add(Line(row, prefix, cash));
            }
            return draft;
        }

        static GlLine Line(Dictionary<string, object> row, string prefix, List<Dictionary<string, object>> cash)
        {
            GlLine line = new GlLine();
            line.Account = GlSql.Col(row, "ccode");
            line.Digest = Digest(prefix, GlSql.Col(row, "dg"));
            line.Debit = -GlSql.Money(row, "md");
            line.Credit = -GlSql.Money(row, "mc");
            bool debit = line.Debit != 0;
            line.Dept = GlSql.Col(row, "dept");
            line.Person = GlSql.Col(row, "person");
            line.Customer = GlSql.Col(row, "cus");
            line.Supplier = GlSql.Col(row, "sup");
            line.ItemClass = GlSql.Col(row, "icls");
            line.Item = GlSql.Col(row, "item");
            line.Settle = GlSql.Col(row, "settle");
            line.DocNo = GlSql.Col(row, "docno");
            line.DocDate = GlSql.Col(row, "docdate");
            line.Currency = GlSql.Col(row, "exch");
            line.Rate = line.Currency.Length > 0 ? Dec(row, "rate") : 0;
            line.Fc = line.Rate > 0 ? -GlSql.Money(row, debit ? "md_f" : "mc_f") : 0;
            line.Qty = -Dec(row, debit ? "nd" : "nc");
            line.Flows = Flows(GlSql.Col(row, "inid"), cash);
            return line;
        }

        static List<GlFlow> Flows(string entry, List<Dictionary<string, object>> cash)
        {
            List<GlFlow> list = new List<GlFlow>();
            foreach (Dictionary<string, object> c in cash)
            {
                if (GlSql.Col(c, "inid") != entry)
                {
                    continue;
                }
                GlFlow flow = new GlFlow();
                flow.Item = GlSql.Col(c, "item");
                flow.Debit = -GlSql.Money(c, "md");
                flow.Credit = -GlSql.Money(c, "mc");
                list.Add(flow);
            }
            return list;
        }

        // 备注、外部业务类型按表头原样复制，原始单据类型 / 单据号、业务员按分录号原样复制（同 U8 的冲销凭证）。
        public static GlCarry Carry(List<Dictionary<string, object>> rows)
        {
            GlCarry carry = new GlCarry();
            carry.Memo1 = GlSql.Col(rows[0], "t1");
            carry.Memo2 = GlSql.Col(rows[0], "t2");
            carry.OutSign = GlSql.Col(rows[0], "outsign");
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> row = rows[i];
                carry.Put(i + 1, GlSql.Col(row, "ccode"), new string[]
                {
                    GlSql.Col(row, "bt"), GlSql.Col(row, "bid"), GlSql.Col(row, "op")
                });
            }
            return carry;
        }

        // U8 的前缀：「[冲销yyyy.MM.dd 类别-NNNN号凭证]」，例如「[冲销2026.01.31 转-0009号凭证]」；日期是原凭证制单日期，凭证号至少 4 位。
        internal static string Prefix(GlKey srcKey, string srcDate)
        {
            return "[冲销" + (srcDate ?? "").Replace('-', '.') + " " + srcKey.Sign + "-"
                + srcKey.No.ToString(CultureInfo.InvariantCulture).PadLeft(4, '0') + "号凭证]";
        }

        internal static string Digest(string prefix, string digest)
        {
            string text = prefix + (digest ?? "");
            return text.Length > DigestMax ? text.Substring(0, DigestMax) : text;
        }

        internal static decimal Dec(Dictionary<string, object> row, string name)
        {
            decimal value;
            decimal.TryParse(GlSql.Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
            return value;
        }
    }
}
