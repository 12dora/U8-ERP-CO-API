using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 按 U8 制单的规则拼分录（已在测试账套逐项核对）。只拼「科目、方向、金额、来源」，辅助核算、合并、排序在 ArapVoucherLines。
    // 往来科目一律取审核时登记在往来明细上的 cCode（U8 按控制科目设置写好的），不另查。
    // 销售发票：往来（借）= 明细 iDAmount；收入（贷）按表体 iNatMoney、销项税（贷）按 iNatTax，科目查对方科目设置 → 基本科目。
    // 采购发票：采购科目（借）按表体 iMoney、进项税（借）按 iTaxPrice，往来（贷）= 明细 iCAmount。
    // 收付款单：明细 iFlag=6 是结算行（现金、银行、票据科目），iFlag=0 是往来行，iFlag=3 是票据登记行（不出分录，只回写）。
    // 退款单同收付款单，往来行红字（负数）、结算行翻到对方记正数，往来行在前（同 U8）。
    // 应收单 / 应付单：往来 = 明细；对方 = 表体 Ap_Vouchs.cCode（不含税 iNoTaxAmount），税额 iNatTax 走税金科目。
    internal static class ArapVoucherBuild
    {
        public const string DwPart = "dw";
        public const string SettlePart = "settle";
        public const string OppPart = "opp";

        const string SaleBody = "select b.cInvCode, isnull(i.cInvCCode,N'') as invc, {m} as m, {t} as t, {r} as r, "
            + "isnull(b.cItem_class,N'') as ic, isnull(b.cItemCode,N'') as ico from SaleBillVouchs b "
            + "left join Inventory i on i.cInvCode=b.cInvCode where b.SBVID=? order by b.AutoID";
        const string BuyBody = "select b.cInvCode, isnull(i.cInvCCode,N'') as invc, {m} as m, {t} as t, {r} as r, "
            + "isnull(b.cItem_class,N'') as ic, isnull(b.cItemCode,N'') as ico from PurBillVouchs b "
            + "left join Inventory i on i.cInvCode=b.cInvCode where b.PBVID=? order by b.ID";
        const string BillBody = "select isnull(b.cCode,N'') as ccode, {a} as a, {t} as t, {n} as n, {r} as r, "
            + "isnull(b.cDeptCode,N'') as dept, isnull(b.cPerson,N'') as person, isnull(b.cItem_Class,N'') as ic, "
            + "isnull(b.cItemCode,N'') as ico from Ap_Vouchs b where b.cLink=(select h.cLink from Ap_Vouch h where h.Auto_ID=?) "
            + "order by b.Auto_ID";

        public static List<VoucherPart> Parts(object conn, VoucherDoc doc, int year)
        {
            switch (doc.Ask.Kind)
            {
                case "sale_invoice":
                    return Invoice(conn, doc, year, true);
                case "purchase_invoice":
                    return Invoice(conn, doc, year, false);
                case "ar_receipt":
                case "ap_payment":
                case "ar_refund":
                case "ap_refund":
                    return Receipt(doc);
                default:
                    return Bill(conn, doc, year);
            }
        }

        // 往来明细上的往来行（iFlag 0）：金额在借方（应收）或贷方（应付），明细行自己的部门、业务员作辅助核算来源。
        // 方向跟明细的列走、金额带符号：退款单的负数登记行出红字分录（供应商退款借方负数、客户退款贷方负数，同 U8）。
        static void AddDw(List<VoucherPart> parts, VoucherDoc doc, Dictionary<string, object> row)
        {
            decimal dm = WriteoffSql.Num(CoRows.Col(row, "dm"));
            decimal cm = WriteoffSql.Num(CoRows.Col(row, "cm"));
            VoucherPart part = Part(parts, CoRows.Col(row, "cCode"), dm != 0, dm != 0 ? dm : cm, DwPart);
            part.Aid = CoRows.Col(row, "aid");
            part.Ctx = doc.HeadCtx().With(CoRows.Col(row, "cDeptCode"), CoRows.Col(row, "cPerson"),
                CoRows.Col(row, "cItem_Class"), CoRows.Col(row, "cItemCode"), CoRows.Col(row, "cInvCode"));
            part.Ctx.Dw = CoRows.Col(row, "cDwCode").Length > 0 ? CoRows.Col(row, "cDwCode") : doc.Dw;
        }

        static VoucherPart Part(List<VoucherPart> parts, string account, bool debit, decimal amount, string kind)
        {
            VoucherPart part = new VoucherPart();
            part.Account = account;
            part.Debit = debit;
            part.Amount = decimal.Round(amount, 2, MidpointRounding.AwayFromZero);
            part.Kind = kind;
            if (part.Amount != 0)
            {
                parts.Add(part);
            }
            return part;
        }

        static List<VoucherPart> Invoice(object conn, VoucherDoc doc, int year, bool sale)
        {
            List<VoucherPart> parts = new List<VoucherPart>();
            foreach (Dictionary<string, object> row in doc.Rows)
            {
                AddDw(parts, doc, row);
            }
            OppKeys partner = Partner(conn, doc, sale ? "select isnull(cSTCode,N'') from SaleBillVouch where SBVID=?"
                : "select isnull(cPTCode,N'') from PurBillVouch where PBVID=?");
            string sql = (sale ? SaleBody : BuyBody).Replace("{m}", WriteoffSql.Dec(sale ? "b.iNatMoney" : "b.iMoney", 2))
                .Replace("{t}", WriteoffSql.Dec(sale ? "b.iNatTax" : "b.iTaxPrice", 2)).Replace("{r}", WriteoffSql.Dec("b.iTaxRate", 4));
            List<Dictionary<string, object>> body = Rows.Query(conn, sql, new object[] { doc.Id }, 1001);
            if (body.Count == 0)
            {
                throw ArapVoucherDoc.Refuse("发票没有表体");
            }
            List<VoucherPart> taxes = new List<VoucherPart>();
            string income = sale ? ArapVoucherAcct.SaleIncome : ArapVoucherAcct.BuyCost;
            string tax = sale ? ArapVoucherAcct.SaleTax : ArapVoucherAcct.BuyTax;
            foreach (Dictionary<string, object> line in body)
            {
                OppKeys keys = ArapVoucherAcct.Line(partner, CoRows.Col(line, "cInvCode"), CoRows.Col(line, "invc"),
                    WriteoffSql.Num(CoRows.Col(line, "r")));
                decimal money = Positive(line, "m");
                decimal taxed = Positive(line, "t");
                VoucherCtx ctx = doc.HeadCtx().With("", "", CoRows.Col(line, "ic"), CoRows.Col(line, "ico"), keys.Inv);
                if (money != 0)
                {
                    Part(parts, ArapVoucherAcct.Need(conn, doc.Flag, year, income, keys), !sale, money, OppPart).Ctx = ctx;
                }
                if (taxed != 0)
                {
                    Part(taxes, ArapVoucherAcct.Need(conn, doc.Flag, year, tax, keys), !sale, taxed, OppPart).Ctx = ctx;
                }
            }
            parts.AddRange(taxes);
            return parts;
        }

        static OppKeys Partner(object conn, VoucherDoc doc, string bizSql)
        {
            OppKeys partner = ArapVoucherAcct.Partner(conn, doc.Flag, doc.Dw, CoRows.Col(doc.Head, "cur"));
            partner.BizType = bizSql == null ? "" : (Rows.Scalar(conn, bizSql, new object[] { doc.Id }) ?? "").Trim();
            return partner;
        }

        static decimal Positive(Dictionary<string, object> line, string name)
        {
            decimal value = WriteoffSql.Num(CoRows.Col(line, name));
            if (value < 0)
            {
                throw ArapVoucherDoc.Refuse("红字（负数）单据制单暂不支持");
            }
            return value;
        }

        static List<VoucherPart> Receipt(VoucherDoc doc)
        {
            List<VoucherPart> settle = new List<VoucherPart>();
            List<VoucherPart> dw = new List<VoucherPart>();
            foreach (Dictionary<string, object> row in doc.Rows)
            {
                string flag = CoRows.Col(row, "iflag");
                if (flag == "0")
                {
                    AddDw(dw, doc, row);
                    continue;
                }
                if (flag == "3")
                {
                    continue;
                }
                if (flag != "6")
                {
                    throw ArapVoucherDoc.Refuse("往来明细有无法识别的行（iFlag=" + flag + "），请在 U8 客户端制单");
                }
                VoucherPart part = AddSettle(settle, row);
                part.Aid = CoRows.Col(row, "aid");
                part.Ctx = doc.HeadCtx().With(CoRows.Col(row, "cDeptCode"), CoRows.Col(row, "cPerson"),
                    CoRows.Col(row, "cItem_Class"), CoRows.Col(row, "cItemCode"), "");
                part.Settle = CoRows.Col(row, "cSSCode");
                part.DocNo = doc.NoteNo.Trim();
                part.DocDate = doc.VDate;
            }
            if (settle.Count == 0 || dw.Count == 0)
            {
                throw ArapVoucherDoc.Refuse("收付款单的往来明细缺结算行或往来行，请在 U8 客户端制单");
            }
            // 退款单：往来（红字）在前、结算在后（同 U8 生成的退款凭证）；收付款单结算在前。
            if (ArapVoucherRule.IsRefund(doc.Ask.Kind))
            {
                dw.AddRange(settle);
                return dw;
            }
            settle.AddRange(dw);
            return settle;
        }

        // 结算行（iFlag 6）按借贷差定方向、金额取正：收付款单与原来相同；退款单的负数登记行翻到对方、记正数
        // （供应商退款借银行、客户退款贷银行，同 U8）。
        static VoucherPart AddSettle(List<VoucherPart> settle, Dictionary<string, object> row)
        {
            decimal net = WriteoffSql.Num(CoRows.Col(row, "dm")) - WriteoffSql.Num(CoRows.Col(row, "cm"));
            return Part(settle, CoRows.Col(row, "cCode"), net > 0, Math.Abs(net), SettlePart);
        }

        static List<VoucherPart> Bill(object conn, VoucherDoc doc, int year)
        {
            List<VoucherPart> parts = new List<VoucherPart>();
            bool debit = false;
            foreach (Dictionary<string, object> row in doc.Rows)
            {
                AddDw(parts, doc, row);
                debit = WriteoffSql.Num(CoRows.Col(row, "dm")) > 0;
            }
            OppKeys partner = Partner(conn, doc, null);
            string sql = BillBody.Replace("{a}", WriteoffSql.Dec("b.iAmount", 2)).Replace("{t}", WriteoffSql.Dec("b.iNatTax", 2))
                .Replace("{n}", "convert(varchar(40), convert(decimal(28,2), b.iNoTaxAmount))").Replace("{r}", WriteoffSql.Dec("b.iTaxRate", 4));
            List<Dictionary<string, object>> body = Rows.Query(conn, sql, new object[] { doc.Id }, 1001);
            List<VoucherPart> taxes = new List<VoucherPart>();
            string taxCol = doc.Flag == "AP" ? ArapVoucherAcct.BuyTax : ArapVoucherAcct.SaleTax;
            for (int i = 0; i < body.Count; i++)
            {
                Dictionary<string, object> line = body[i];
                decimal taxed = Positive(line, "t");
                string net = CoRows.Col(line, "n");
                decimal amount = taxed == 0 ? Positive(line, "a") : (net.Length > 0 ? Positive(line, "n") : Positive(line, "a") - taxed);
                if (CoRows.Col(line, "ccode").Length == 0)
                {
                    throw ArapVoucherDoc.Refuse("表体第 " + (i + 1).ToString(CultureInfo.InvariantCulture) + " 行没有科目");
                }
                VoucherCtx ctx = doc.HeadCtx().With(CoRows.Col(line, "dept"), CoRows.Col(line, "person"), CoRows.Col(line, "ic"),
                    CoRows.Col(line, "ico"), "");
                Part(parts, CoRows.Col(line, "ccode"), !debit, amount, OppPart).Ctx = ctx;
                if (taxed != 0)
                {
                    OppKeys keys = ArapVoucherAcct.Line(partner, "", "", WriteoffSql.Num(CoRows.Col(line, "r")));
                    Part(taxes, ArapVoucherAcct.Need(conn, doc.Flag, year, taxCol, keys), !debit, taxed, OppPart).Ctx = ctx;
                }
            }
            if (body.Count == 0)
            {
                throw ArapVoucherDoc.Refuse("单据没有表体");
            }
            parts.AddRange(taxes);
            return parts;
        }
    }
}
