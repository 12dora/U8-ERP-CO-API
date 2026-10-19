using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 数电号列配置、sale_invoice 的 code_like OR、核销字段 / settled 筛选，以及字段权限拒绝路径。不连库。
    internal static class TaxInvoiceNoSelfTest
    {
        const string Acc = "801";
        const string Col = "cDefine10";
        const string Fragment = "99000000000000000001";

        public static void Run()
        {
            CheckConfig();
            CheckList();
            CheckSearch();
            CheckSettledUnknown();
            CheckDenied();
            CheckMask();
            CheckDefinesPerm();
            CheckApplyLines();
        }

        static void CheckConfig()
        {
            string[] allowed = new string[] { Acc, "802" };
            Dictionary<string, string> ok = ConfigRulesTax.Load(Json(
                "{\"taxInvoiceNoFields\":{\"801\":\"  cdefine10  \",\"802\":\"CTaxBillCode\"}}"), allowed);
            Expect("tax cfg canon", ok[Acc] == Col && ok["802"] == "cTaxBillCode");
            Expect("tax cfg missing", ConfigRulesTax.Load(Json("{}"), allowed).Count == 0);
            Expect("tax cfg key", ConfigRulesTax.Key == "taxInvoiceNoFields");
            BridgeConfig.CheckKeys(Json("{\"taxInvoiceNoFields\":{}}"));
            Reject("tax cfg col", "{\"taxInvoiceNoFields\":{\"801\":\"cDefine4\"}}", allowed, "cDefine1");
            Reject("tax cfg acc", "{\"taxInvoiceNoFields\":{\"803\":\"cDefine10\"}}", allowed, "allowedAccounts");
            Reject("tax cfg digits", "{\"taxInvoiceNoFields\":{\"81\":\"cDefine10\"}}", allowed, "3 位数字");
            Reject("tax cfg obj", "{\"taxInvoiceNoFields\":[]}", allowed, "必须是对象");
            Reject("tax cfg str", "{\"taxInvoiceNoFields\":{\"801\":1}}", allowed, "列名字符串");
        }

        static void CheckList()
        {
            ListKind inv = ListKinds.Find("sale_invoice");
            ListKind ar = ListKinds.Find("ar_bill");
            ListKind ap = ListKinds.Find("ap_bill");
            Expect("tax list keys", HasKey(inv, "tax_invoice_no") && HasKey(inv, "open_amount") && HasKey(inv, "amount")
                && HasKey(inv, "settled"));
            CheckListApply(inv);
            Expect("tax bill keys", HasKey(ar, "open_amount") && HasKey(ar, "settled") && HasKey(ap, "open_amount")
                && HasKey(ap, "settled") && !HasKey(ar, "tax_invoice_no"));
            Expect("tax order extra", !HasKey(ListKinds.Find("sale_order"), "tax_invoice_no")
                && !HasKey(ListKinds.Find("sale_order"), "settled"));
            CheckListSelect(inv);
        }

        static void CheckListApply(ListKind inv)
        {
            string apply = ListSql.ApplyOf(inv);
            Expect("tax list apply", inv.Has(ListKind.Apply) && inv[ListKind.Apply].IndexOf("SaleBillVouchs",
                StringComparison.Ordinal) >= 0 && apply.IndexOf("OUTER APPLY", StringComparison.Ordinal) >= 0
                && apply.IndexOf("{FILTER}", StringComparison.Ordinal) < 0
                && apply.IndexOf("AS amount", StringComparison.Ordinal) >= 0);
        }

        static void CheckListSelect(ListKind inv)
        {
            string bare = ListSql.FullSelect(inv);
            Expect("tax list null col", bare.IndexOf("NULL AS tax_invoice_no", StringComparison.Ordinal) >= 0
                && bare.IndexOf("h.cDefine10", StringComparison.Ordinal) < 0);
            string filled = ListSql.FullSelect(inv, Ctx(null));
            Expect("tax list col", filled.IndexOf("h.cDefine10", StringComparison.Ordinal) >= 0
                && filled.IndexOf("w.open_amount AS open_amount", StringComparison.Ordinal) >= 0
                && filled.IndexOf("w.amount AS amount", StringComparison.Ordinal) >= 0);
            Expect("tax money alias", PermColumnMap.IsMoneyKey("settled") && PermColumnMap.IsMoney("iRAmount")
                && PermColumnMap.IsMoneyKey("open_amount") && PermColumnMap.IsMoneyKey("amount"));
        }

        static void CheckSearch()
        {
            List<object> ps = new List<object>();
            string orSql = Search("sale_invoice", "code_like", Fragment, Ctx(null), ps);
            Expect("tax or", orSql.IndexOf("OR h.cDefine10 LIKE", StringComparison.Ordinal) >= 0
                && Count(orSql, '?') == ps.Count && LikeCount(ps) == 2);
            ps = new List<object>();
            string none = Search("sale_invoice", "code_like", Fragment, null, ps);
            Expect("tax or none", none.IndexOf("OR h.", StringComparison.Ordinal) < 0 && LikeCount(ps) == 1
                && Count(none, '?') == ps.Count);
            ps = new List<object>();
            string order = Search("sale_order", "code_like", Fragment, Ctx(null), ps);
            Expect("tax or order", order.IndexOf("OR h.cDefine10", StringComparison.Ordinal) < 0 && LikeCount(ps) == 1);
            ps = new List<object>();
            string settled = Search("sale_invoice", "settled", false, null, ps);
            Expect("tax settled sql", settled.IndexOf("w.open_amount", StringComparison.Ordinal) >= 0
                && settled.IndexOf("ISNULL(", StringComparison.Ordinal) >= 0 && Count(settled, '?') == ps.Count);
            ps = new List<object>();
            string bill = Search("ar_bill", "settled", true, null, ps);
            Expect("tax settled bill", bill.IndexOf("h.iRAmount", StringComparison.Ordinal) >= 0
                && bill.IndexOf(" = 1", StringComparison.Ordinal) >= 0 && Count(bill, '?') == ps.Count);
        }

        static void CheckSettledUnknown()
        {
            string expr = ListKinds.SbvSettled;
            Expect("tax settled unk extra", ListKinds.Find("sale_invoice")[ListKind.Extra]
                .IndexOf(expr + " AS settled", StringComparison.Ordinal) >= 0
                && expr.IndexOf("w.amount IS NULL THEN NULL", StringComparison.Ordinal) >= 0);
            List<object> ps = new List<object>();
            string yes = Search("sale_invoice", "settled", true, null, ps);
            Expect("tax settled unk true", yes.IndexOf(expr + " = 1", StringComparison.Ordinal) >= 0
                && yes.IndexOf("ISNULL(" + expr, StringComparison.Ordinal) < 0
                && Count(yes, '?') == ps.Count);
            ps = new List<object>();
            string no = Search("sale_invoice", "settled", false, null, ps);
            Expect("tax settled unk false", no.IndexOf(expr + " = 0", StringComparison.Ordinal) >= 0
                && no.IndexOf("ISNULL(" + expr, StringComparison.Ordinal) < 0
                && Count(no, '?') == ps.Count);
        }

        static void CheckDenied()
        {
            PermContext denyCol = Perm();
            Deny(denyCol, "07", Col);
            List<object> ps = new List<object>();
            string sql = Search("sale_invoice", "code_like", Fragment, Ctx(denyCol), ps);
            Expect("tax deny or", sql.IndexOf("OR h.cDefine10", StringComparison.Ordinal) < 0 && LikeCount(ps) == 1);
            string select = ListSql.FullSelect(ListKinds.Find("sale_invoice"), Ctx(denyCol));
            Expect("tax deny select", select.IndexOf("h.cDefine10", StringComparison.Ordinal) >= 0);
            Settled403("iNatSum");
            Settled403("iMoneySum");
            PermContext sup = Perm();
            sup.Supervisor = true;
            Deny(sup, "07", Col);
            ps = new List<object>();
            string keep = Search("sale_invoice", "code_like", Fragment, Ctx(sup), ps);
            Expect("tax or supervisor", keep.IndexOf("OR h.cDefine10 LIKE", StringComparison.Ordinal) >= 0
                && LikeCount(ps) == 2);
            ps = new List<object>();
            string none = Search("sale_invoice", "code_like", Fragment, Ctx(Perm()), ps);
            Expect("tax or no denial", none.IndexOf("OR h.cDefine10 LIKE", StringComparison.Ordinal) >= 0
                && LikeCount(ps) == 2);
        }

        static void Settled403(string fld)
        {
            PermContext denyMoney = Perm();
            Deny(denyMoney, "07", fld);
            try
            {
                Search("sale_invoice", "settled", false, Ctx(denyMoney), new List<object>());
            }
            catch (BridgeException ex)
            {
                Expect("tax settled 403 " + fld, ex.Status == 403 && ex.Code == "no_permission"
                    && ex.Message.IndexOf("无权按核销状态筛选", StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException("tax settled 403 " + fld);
        }

        static void CheckMask()
        {
            WorkItem item = ListItem();
            PermContext denyCol = Perm();
            Deny(denyCol, "07", Col);
            Dictionary<string, object> body = RowBody("99000000000000000001", "1.00", true);
            PermMask.Mask(denyCol, item.Path, PermColumnMap.Plan(item), body);
            Dictionary<string, object> row = First(body);
            Expect("tax mask col", row["tax_invoice_no"] == null && (string)row["open_amount"] == "1.00"
                && (string)row["amount"] == "2.00" && (bool)row["settled"]);
            PermContext denyMoney = Perm();
            Deny(denyMoney, "07", "iMoneySum");
            Dictionary<string, object> money = RowBody("x", "1.00", true);
            PermMask.Mask(denyMoney, item.Path, PermColumnMap.Plan(item), money);
            Dictionary<string, object> mrow = First(money);
            Expect("tax mask money", mrow["open_amount"] == null && mrow["amount"] == null && mrow["settled"] == null
                && (string)mrow["tax_invoice_no"] == "x");
        }

        static WorkContext Ctx(PermContext perm)
        {
            WorkContext ctx = new WorkContext();
            WorkItem item = ListItem();
            item.Path = VoucherSearch.Path;
            ctx.Item = item;
            ctx.Config = item.Config;
            // 读路由在 PermHook 里会取快照；自检没有连接，没给快照时用不受限的空快照。
            ctx.Perm = perm ?? Perm();
            return ctx;
        }

        static WorkItem ListItem()
        {
            WorkItem item = new WorkItem();
            item.Acc = Acc;
            item.Config = Cfg();
            item.Path = Requests.ListPath;
            item.Body = new Dictionary<string, object>();
            item.Body["type"] = "sale_invoice";
            return item;
        }

        static BridgeConfig Cfg()
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.AllowedAccounts = new string[] { Acc };
            cfg.TaxInvoiceNoFields = new Dictionary<string, string>(StringComparer.Ordinal);
            cfg.TaxInvoiceNoFields[Acc] = Col;
            return cfg;
        }

        static string Search(string type, string key, object value, WorkContext ctx, List<object> ps)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = type;
            body[key] = value;
            return VoucherSearchSql.Build(VoucherSearchArgs.Parse(body, ctx), ps, ctx);
        }

        static void CheckDefinesPerm()
        {
            PermContext deny = Perm();
            Deny(deny, "07", "B;" + Col);
            Defines403("sale_invoice", "define10", Ctx(deny), "define10");
            Deny(deny, "07", "cdefine1");
            List<object> ok = new List<object>();
            string sql = SearchDefines("sale_invoice", "define8", "HT-001", Ctx(deny), ok);
            Expect("tax define allowed", sql.IndexOf("h.cDefine8", StringComparison.Ordinal) >= 0
                && Count(sql, '?') == ok.Count);
            PermContext so = Perm();
            Deny(so, "17", Col);
            Defines403("sale_order", "define10", Ctx(so), "define10");
            CheckDefinesMask();
        }

        static void CheckDefinesMask()
        {
            WorkItem item = ListItem();
            item.Path = VoucherSearch.Path;
            PermContext deny = Perm();
            Deny(deny, "VCH_07", "T;CDEFINE10");
            Dictionary<string, object> body = RowBody("99000000000000000001", "1.00", true);
            Dictionary<string, object> defs = new Dictionary<string, object>();
            defs["define10"] = "HT-001";
            defs["define1"] = "OK";
            First(body)["defines"] = defs;
            PermMask.Mask(deny, item.Path, PermColumnMap.Plan(item), body);
            Dictionary<string, object> got = (Dictionary<string, object>)First(body)["defines"];
            Expect("tax define mask", got["define10"] == null && (string)got["define1"] == "OK");
            Dictionary<string, object> req = new Dictionary<string, object>();
            Dictionary<string, object> spec = new Dictionary<string, object>();
            spec["define10"] = "HT-001";
            req["defines"] = spec;
            List<DefineMatch> list = VoucherSearchDefines.Parse(req, ListKinds.Find("sale_invoice"));
            Dictionary<string, object> page = new Dictionary<string, object>();
            page["type"] = "sale_invoice";
            page["items"] = new List<object> { new Dictionary<string, object>() };
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["def_define10"] = "HT-001";
            VoucherSearchDefines.Attach(page, new List<Dictionary<string, object>> { row }, list, Ctx(deny));
            Dictionary<string, object> attached = (Dictionary<string, object>)First(page)["defines"];
            Expect("tax define attach", attached["define10"] == null);
        }

        static void Defines403(string type, string key, WorkContext ctx, string part)
        {
            try
            {
                SearchDefines(type, key, "HT-001", ctx, new List<object>());
            }
            catch (BridgeException ex)
            {
                Expect("tax define 403 " + type + " " + key, ex.Status == 403 && ex.Code == "no_permission"
                    && ex.Message.IndexOf("无权按表头自定义项 " + part + " 搜索", StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException("tax define 403 " + type + " " + key);
        }

        static string SearchDefines(string type, string key, string value, WorkContext ctx, List<object> ps)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = type;
            Dictionary<string, object> defines = new Dictionary<string, object>();
            defines[key] = value;
            body["defines"] = defines;
            return VoucherSearchSql.Build(VoucherSearchArgs.Parse(body, ctx), ps, ctx);
        }

        static void CheckApplyLines()
        {
            PermContext p = Perm();
            p.On.Add(PermObj.Inventory);
            HashSet<string> codes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            codes.Add("M1");
            p.Codes[PermObj.Inventory] = codes;
            WorkContext ctx = Ctx(p);
            List<object> ps = new List<object>();
            string apply = ListSql.ApplyOf(ListKinds.Find("sale_invoice"), ctx, ps);
            Expect("tax apply line", apply.IndexOf("b.cInvCode", StringComparison.Ordinal) >= 0
                && apply.IndexOf("IN (", StringComparison.Ordinal) >= 0 && ps.Contains("M1")
                && apply.IndexOf("{FILTER}", StringComparison.Ordinal) < 0);
            List<object> sps = new List<object>();
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_invoice";
            string sql = VoucherSearchSql.Build(VoucherSearchArgs.Parse(body, ctx), sps, ctx);
            Expect("tax apply search", sql.IndexOf("OUTER APPLY", StringComparison.Ordinal) >= 0
                && sql.IndexOf("b.cInvCode", StringComparison.Ordinal) >= 0 && (int)sps[0] == 51
                && (string)sps[1] == "M1" && (int)sps[2] == 0 && Count(sql, '?') == sps.Count);
            List<object> none = new List<object>();
            string bare = ListSql.ApplyOf(ListKinds.Find("sale_invoice"), Ctx(Perm()), none);
            Expect("tax apply none", bare.IndexOf("b.cInvCode", StringComparison.Ordinal) < 0 && none.Count == 0);
        }

        static Dictionary<string, object> RowBody(string tax, string open, bool settled)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["tax_invoice_no"] = tax;
            row["open_amount"] = open;
            row["amount"] = "2.00";
            row["settled"] = settled;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["items"] = new List<object> { row };
            return body;
        }

        static Dictionary<string, object> First(Dictionary<string, object> body)
        {
            return (Dictionary<string, object>)((List<object>)body["items"])[0];
        }

        static PermContext Perm()
        {
            PermContext p = new PermContext();
            p.Acc = Acc;
            p.Operator = "op001";
            p.Year = 2024;
            p.DateYear = 2024;
            p.AcctYear = 2024;
            return p;
        }

        static void Deny(PermContext p, string key, string fld)
        {
            HashSet<string> set;
            if (!p.Columns.TryGetValue(key, out set))
            {
                set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                p.Columns[key] = set;
            }
            set.Add(fld);
        }

        static void Reject(string name, string json, string[] allowed, string part)
        {
            try
            {
                ConfigRulesTax.Load(Json(json), allowed);
            }
            catch (InvalidOperationException ex)
            {
                Expect(name, ex.Message.IndexOf(part, StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static Dictionary<string, object> Json(string text)
        {
            return (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(text);
        }

        static bool HasKey(ListKind kind, string key)
        {
            return Array.IndexOf(kind.ExtraKeys, key) >= 0;
        }

        static int LikeCount(List<object> ps)
        {
            int n = 0;
            for (int i = 0; i < ps.Count; i++)
            {
                string text = ps[i] as string;
                if (text != null && text.IndexOf(Fragment, StringComparison.Ordinal) >= 0)
                {
                    n++;
                }
            }
            return n;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
