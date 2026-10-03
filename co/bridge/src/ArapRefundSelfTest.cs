using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的供应商退款 ap_refund（AP48）、客户退款 ar_refund（AR49）部分：规格、种类、列表条件、卡片、权限、预演模式，
    // 核销对这两种类型的拒绝，以及制单：请求、规则、红字分录的拼法和借贷平衡。只跑纯函数，不连库、不建 COM。
    internal static class ArapRefundSelfTest
    {
        public static void Run()
        {
            CheckSpec("ap_refund", "AP", "48", "供应商退款", "ap_payment");
            CheckSpec("ar_refund", "AR", "49", "客户退款", "ar_receipt");
            CheckList("ap_refund", "h.cFlag = N'AP' AND h.cVouchType = N'48'", false);
            CheckList("ar_refund", "h.cFlag = N'AR' AND h.cVouchType = N'49'", true);
            CheckWiring("ap_refund", "AP48", "ap_payment");
            CheckWiring("ar_refund", "AR49", "ar_receipt");
            CheckExcluded("ap_refund");
            CheckExcluded("ar_refund");
            CheckVoucherRules();
            CheckCashFlow();
            // 供应商退款：往来明细借方 -500、结算行贷方 -500 → 借 应付 -500（红字）、借 银行 500，凭证类别「收」。
            CheckVoucherParts("ap_refund", "AP", Detail("11", "0", "220201", -500m, 0m), Detail("12", "6", "100201", 0m, -500m), true);
            // 客户退款：往来明细贷方 -400、结算行借方 -400 → 贷 应收 -400（红字）、贷 银行 400，凭证类别「付」。
            CheckVoucherParts("ar_refund", "AR", Detail("21", "0", "112201", 0m, -400m), Detail("22", "6", "100201", -400m, 0m), false);
        }

        static void CheckVoucherRules()
        {
            Expect("voucher flag", ArapVoucherReq.FlagOf("ap_refund") == "AP" && ArapVoucherReq.FlagOf("ar_refund") == "AR");
            Expect("voucher receipt", ArapVoucherDoc.IsReceipt("ap_refund") && ArapVoucherDoc.IsReceipt("ar_refund"));
            Expect("voucher out sign", ArapVoucherRule.OutSign("ap_refund", "AP") == "RP" && ArapVoucherRule.OutSign("ar_refund", "AR") == "RP"
                && ArapVoucherRule.OutSign("ar_receipt", "AR") == "AR");
            Expect("voucher digest", ArapVoucherRule.Prefix("ap_refund") + ArapVoucherRule.Suffix("ap_refund") == "收退款"
                && ArapVoucherRule.Prefix("ar_refund") + ArapVoucherRule.Suffix("ar_refund") == "付退款"
                && ArapVoucherRule.Suffix("ap_payment") == "");
            Dictionary<string, object> body = Json.Parse(System.Text.Encoding.UTF8.GetBytes("{\"flag\":\"AP\",\"type\":\"ap_refund\",\"id\":5}"));
            VoucherAsk ask = ArapVoucherReq.Parse(body);
            Expect("voucher parse", ask.Kind == "ap_refund" && ask.Flag == "AP" && ask.Id == 5);
            Expect("voucher keys", string.Join("|", ArapVoucherReq.LockKeys(body, false)).StartsWith("ap_refund:5|", StringComparison.Ordinal));
        }

        // 现金流量：退款单按分录所在的列取数据来源方向。供应商退款 220201 借 −100 → 借方来源 2202 / 1 → 04；
        // 客户退款 112201 贷 −100、费用 630101 贷 +10 → 贷方来源 → 01。处理制单的原规则不变：借 −100 仍按贷方取，推不出 409。
        static void CheckCashFlow()
        {
            List<Dictionary<string, object>> src = new List<Dictionary<string, object>>();
            src.Add(Source("04", "2202", "1"));
            src.Add(Source("01", "1122", "0"));
            src.Add(Source("01", "6301", "0"));
            GlLine ap = Gl("220201", -100m, 0m);
            Expect("cash ap dir", ArapCashItems.Dir(ap, true) == "1" && ArapCashItems.Dir(ap, false) == "0");
            Expect("cash ap item", ArapCashItems.Derive(src, ap, true) == "04");
            Expect("cash ar item", ArapCashItems.Derive(src, Gl("112201", 0m, -100m), true) == "01"
                && ArapCashItems.Derive(src, Gl("630101", 0m, 10m), true) == "01");
            CashItemMap map = new CashItemMap();
            map.ByColumn = true;
            Expect("cash pick", ArapCashItems.Pick(src, ap, map) == "04" && map.Used.Count == 0);
            bool refused = false;
            try
            {
                ArapCashItems.Derive(src, ap);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 409;
            }
            Expect("cash old rule", refused);
        }

        static Dictionary<string, object> Source(string item, string prefix, string dir)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["item"] = item;
            one["src"] = prefix;
            one["dir"] = dir;
            return one;
        }

        static GlLine Gl(string account, decimal debit, decimal credit)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Debit = debit;
            line.Credit = credit;
            return line;
        }

        // 两行明细（往来、结算）拼出的分录：往来在前、红字留在明细的那一方，结算翻到同一方记正数；借贷平（两方合计都为 0）。
        static void CheckVoucherParts(string kind, string flag, Dictionary<string, object> dw, Dictionary<string, object> settle, bool debit)
        {
            VoucherDoc doc = new VoucherDoc();
            doc.Ask = new VoucherAsk();
            doc.Ask.Kind = kind;
            doc.Ask.Flag = flag;
            doc.Flag = flag;
            doc.Head = new Dictionary<string, object>();
            doc.Head["cDwCode"] = "V001";
            doc.Rows.Add(settle);
            doc.Rows.Add(dw);
            List<VoucherPart> parts = ArapVoucherBuild.Parts(null, doc, 2026);
            Expect(kind + " voucher parts", parts.Count == 2 && parts[0].Kind == ArapVoucherBuild.DwPart && parts[1].Kind == ArapVoucherBuild.SettlePart);
            decimal amount = Math.Abs(parts[1].Amount);
            Expect(kind + " voucher dw", parts[0].Debit == debit && parts[0].Amount == -amount && parts[0].Aid == CoRows.Col(dw, "aid"));
            Expect(kind + " voucher settle", parts[1].Debit == debit && parts[1].Amount == amount && parts[1].Settle == "1");
            List<VoucherRow> rows = new List<VoucherRow>();
            foreach (VoucherPart part in parts)
            {
                rows.Add(Row(part));
            }
            rows = ArapVoucherPlan.Order(rows);
            Expect(kind + " voucher order", rows[0].Line.Account == parts[0].Account);
            ArapVoucherPlan.Balance(rows);
            List<VoucherRow> merged = ArapVoucherPlan.Merge(rows);
            Expect(kind + " voucher merge", merged.Count == 2);
        }

        static Dictionary<string, object> Detail(string aid, string iflag, string account, decimal dm, decimal cm)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["aid"] = aid;
            row["iflag"] = iflag;
            row["cCode"] = account;
            row["dm"] = dm.ToString(System.Globalization.CultureInfo.InvariantCulture);
            row["cm"] = cm.ToString(System.Globalization.CultureInfo.InvariantCulture);
            row["cSSCode"] = "1";
            return row;
        }

        static VoucherRow Row(VoucherPart part)
        {
            GlLine line = new GlLine();
            line.Account = part.Account;
            line.Debit = part.Debit ? part.Amount : 0;
            line.Credit = part.Debit ? 0 : part.Amount;
            line.Dept = "";
            line.Person = "";
            line.Customer = "";
            line.Supplier = "";
            line.ItemClass = "";
            line.Item = "";
            line.Settle = part.Settle;
            line.DocNo = "";
            line.DocDate = "";
            VoucherRow row = new VoucherRow();
            row.Line = line;
            row.Marked = true;
            row.Aids.Add(part.Aid);
            return row;
        }

        static void CheckSpec(string name, string flag, string vtype, string title, string twin)
        {
            VoucherKind kind = Kinds.Find(name);
            Expect(name + " kind", kind != null && kind.Title == title && kind.Family == "ar" && kind.SubId == flag);
            Expect(name + " kind cols", All(kind.HeadTable == "Ap_CloseBill", kind.IdColumn == "iID", kind.BodyTable == "Ap_CloseBills",
                kind.LineIdColumn == "ID", kind.Creatable, kind.Updatable, kind.Deletable, kind.Verifiable));
            ArapSpec spec = ArapReq.Spec(kind);
            Expect(name + " spec", All(spec.Flag == flag, spec.VouchType == vtype, spec.Close,
                spec.Detail == (flag == "AR" ? "Ar_Detail" : "Ap_Detail")));
            // 模板、弃审条件按本类型的 cVouchType 发（AP 下 48、AR 下 49），不能沿用付款单 / 收款单的号。
            Expect(name + " template", ArapCond.Template(spec) == "<condition cVouchType='" + vtype + "'/>");
            ArapDoc doc = new ArapDoc();
            doc.Id = 7;
            doc.Code = "0000000001";
            doc.Row = new Dictionary<string, object>();
            Expect(name + " unsign", ArapCond.Unsign(spec, doc) == "<condition type='0' cVouchType='" + vtype
                + "' cVouchID='0000000001' iID='7'/>");
            Expect(name + " own no", ArapSql.OwnNo(spec, doc) == flag + vtype + "0000000001");
            Expect(name + " not note receipt", !ArapSql.NoteReceipt(spec, doc));
            ArapSpec other = ArapReq.Spec(Kinds.Find(twin));
            Expect(name + " differs from " + twin, other.Flag == flag && other.VouchType != vtype);
        }

        static void CheckList(string name, string cond, bool customer)
        {
            ListKind kind = ListKinds.Find(name);
            Expect(name + " list kind", kind != null && kind[ListKind.Cond] == cond);
            Expect(name + " list partner", kind.Has(ListKind.Cus) == customer && kind.Has(ListKind.Ven) == !customer);
            Expect(name + " search defines", VoucherSearchDefines.Supports(kind) && !VoucherSearchSql.HasInventory(kind));
        }

        static void CheckWiring(string name, string card, string twin)
        {
            VoucherKind kind = Kinds.Find(name);
            string[] plan = MetaFieldsVt.Plan(kind);
            Expect(name + " card", plan != null && plan[0] == card && plan[2] == MetaFieldsVt.Card);
            PermRule rule = PermRegistry.ForKey("voucher:" + name);
            PermRule same = PermRegistry.ForKey("voucher:" + twin);
            Expect(name + " perm", rule != null && same != null && string.Join(",", rule.Auths) == string.Join(",", same.Auths));
            Expect(name + " edit", EditMore.Handles(kind));
            string[] routes = new string[] { "vouchers/create", "vouchers/update", "vouchers/delete", "vouchers/verify" };
            for (int i = 0; i < routes.Length; i++)
            {
                string mode = DryRunModes.Lookup(routes[i], name, "", "");
                Expect(name + " dry " + routes[i], mode == DryRunModes.Lookup(routes[i], twin, "", "")
                    && mode != DryRunModes.Lookup(routes[i], "no_such_kind", "", ""));
            }
        }

        // 核销只认收款单 / 付款单：退款类型在请求解析时就 400（制单另收退款单，见 CheckVoucherParts）。
        static void CheckExcluded(string name)
        {
            Expect(name + " writeoff receipt", ArapWriteoffReq.FlagOfReceipt(name) == null && ArapWriteoffReq.FlagOfItem(name) == null);
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("arap refund " + name);
            }
        }
    }
}
