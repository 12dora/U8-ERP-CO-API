using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的字段权限部分（PermLoad.MergeColumns、PermColumnMap、PermMask、PermSnapshot.columns）：只测纯逻辑，不连库。
    // 合并规则、前缀与中文列名、不分大小写、金额组与别名、数量不连带、嵌套与 load_many、未登记类型从严、分段报表、主管不遮、指纹。
    // 由 PermSelfTest.Run 调用。
    internal static class PermColumnSelfTest
    {
        const string LoadPath = "/u8co/v1/vouchers/load";

        public static void Run()
        {
            CheckMerge();
            CheckNormalize();
            CheckSaleOut();
            CheckQuantity();
            CheckLoadMany();
            CheckUnmapped();
            CheckReports();
            CheckFamilySales();
            CheckFamilyStock();
            CheckAmountOrder();
            CheckStrictReports();
            CheckReceiptVouchers();
            CheckSkips();
            CheckSnapshot();
        }

        // 本人拒绝 ∪ 角色拒绝；本人不含 N 的行撤销角色拒绝；本人拒绝时角色放不开；角色不含 N 的行不起作用。
        static void CheckMerge()
        {
            PermContext p = Ctx();
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Row("0303", "iPrice", "0", "1"));
            rows.Add(Row("0303", "iUnitCost", "1", "1"));
            rows.Add(Row("24", "isum", "1", "1"));
            rows.Add(Row("24", "isum", "0", "0"));
            rows.Add(Row("26", "iCost", "0", "1"));
            rows.Add(Row("26", "iCost", "1", "0"));
            rows.Add(Row("88", "iOrderAmt", "1", "0"));
            rows.Add(Row(" ", "iPrice", "0", "1"));
            PermLoad.MergeColumns(p, rows);
            Expect("merge user", p.DeniedFields("0303").Contains("iprice"));
            Expect("merge role", p.DeniedFields("0303").Contains("iUnitCost"));
            Expect("merge user allow cancels role", !p.DeniedFields("24").Contains("isum") && !p.Columns.ContainsKey("24"));
            Expect("merge role cannot unmask", p.DeniedFields("26").Contains("iCost"));
            Expect("merge role non-N", !p.Columns.ContainsKey("88"));
            Expect("merge blank key", p.Columns.Count == 2);
        }

        static void CheckNormalize()
        {
            Expect("norm prefix", PermColumnMap.Normalize(" B;iPrice ") == "iPrice" && PermColumnMap.Normalize("T;x;iSum") == "iSum");
            Expect("norm title", PermColumnMap.Normalize("原币无税金额") == "iOriMoney" && PermColumnMap.Normalize("本币税额") == "iTaxPrice");
            Expect("norm plain", PermColumnMap.Normalize("cItemCName") == "cItemCName");
            Expect("money ci", PermColumnMap.IsMoney("iunitcost") && !PermColumnMap.IsMoney("iQuantity"));
            Expect("money alias", PermColumnMap.IsMoneyKey("amount") && PermColumnMap.IsMoneyKey("paid_nat_amount")
                && !PermColumnMap.IsMoneyKey("qty"));
        }

        // 只拒绝 iPrice（大小写不同、带前缀）：金额组整组置空，数量、存货、表头编码照给；masked_fields 排序。
        static void CheckSaleOut()
        {
            PermContext p = Ctx();
            Deny(p, "0303", "B;iprice");
            Dictionary<string, object> body = VoucherBody();
            int n = Run(p, Item(LoadPath, "sale_out"), body);
            Dictionary<string, object> line = Line(body, 0);
            Expect("so price null", line["iPrice"] == null && line["iUnitCost"] == null && line["iPPrice"] == null);
            Expect("so qty kept", (string)line["iQuantity"] == "2" && (string)line["cInvCode"] == "A01");
            Expect("so head kept", (string)Head(body)["cCode"] == "0000000001" && (bool)body["ok"]);
            List<string> masked = body[PermMask.Key] as List<string>;
            Expect("so masked", n == 3 && masked != null && string.Join(",", masked.ToArray()) == "iPPrice,iPrice,iUnitCost");
            Dictionary<string, object> clean = VoucherBody();
            Run(p, Item(LoadPath, "purchase_in"), clean);
            Expect("so other object", !clean.ContainsKey(PermMask.Key) && (string)Line(clean, 0)["iPrice"] == "10");
        }

        // 只拒绝数量（非金额）：数量置空，金额照给。
        static void CheckQuantity()
        {
            PermContext p = Ctx();
            Deny(p, "0301", "iQuantity");
            Dictionary<string, object> body = VoucherBody();
            Run(p, Item(LoadPath, "other_in"), body);
            Expect("qty null", Line(body, 0)["iQuantity"] == null && (string)Line(body, 0)["iPrice"] == "10");
            Dictionary<string, object> list = new Dictionary<string, object>();
            list["ok"] = true;
            list["rows"] = new List<object> { Map("code", "R1", "amount", "5") };
            PermContext r = Ctx();
            Deny(r, "01", "iSum");
            Run(r, Item(Requests.ListPath, null, "type", "sale_return_apply"), list);
            Expect("alias amount", ((Dictionary<string, object>)((List<object>)list["rows"])[0])["amount"] == null);
        }

        // load_many：每个条目各带 masked_fields，信封是并集；出错条目不动。
        static void CheckLoadMany()
        {
            PermContext p = Ctx();
            Deny(p, "0303", "iUnitCost");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = "sale_out";
            Dictionary<string, object> failed = Map("id", 9, "error", Map("code", "not_found", "message", "x"));
            body["items"] = new List<object> { VoucherBody(), failed };
            Run(p, Item(LoadMany.Path, "sale_out"), body);
            Dictionary<string, object> first = (Dictionary<string, object>)((List<object>)body["items"])[0];
            Expect("many line", Line(first, 0)["iUnitCost"] == null && first.ContainsKey(PermMask.Key));
            Expect("many failed", !failed.ContainsKey(PermMask.Key));
            Expect("many envelope", ((List<string>)body[PermMask.Key]).Contains("iPrice"));
        }

        // 未登记对象的类型：全部对象的拒绝字段按名字比对、不展开金额组；没有字段权限对象的模块不遮。
        static void CheckUnmapped()
        {
            PermContext p = Ctx();
            Deny(p, "0303", "iPrice");
            Dictionary<string, object> body = VoucherBody();
            Run(p, Item(LoadPath, "production_order"), body);
            Expect("unmapped direct", Line(body, 0)["iPrice"] == null && (string)Line(body, 0)["iUnitCost"] == "5");
            Dictionary<string, object> ar = new Dictionary<string, object>();
            ar["rows"] = new List<object> { Map("amount", "5", "iPrice", "1") };
            Run(p, Item(Requests.ListPath, null, "type", "ar_bill"), ar);
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)ar["rows"])[0];
            Expect("ar not masked", (string)row["amount"] == "5" && (string)row["iPrice"] == "1" && !ar.ContainsKey(PermMask.Key));
        }

        // 资金存货只遮采购段与存货段（票据金额不动）；销售分析遮成本与毛利、收入照给。
        static void CheckReports()
        {
            PermContext p = Ctx();
            Deny(p, "24", "iPrice");
            Deny(p, "0303", "iPrice");
            Dictionary<string, object> cash = new Dictionary<string, object>();
            cash["purchases"] = Map("items", new List<object> { Map("amount", 1m, "qty", 2m) }, "total_amount", 1m);
            cash["inventory"] = Map("qty", 3m, "amount", 4m);
            cash["notes_receivable"] = Map("open", Map("amount", 5m));
            Run(p, Item(Requests.ReportRoot + ReportsMgmtGlReq.CashName, null), cash);
            Dictionary<string, object> pur = (Dictionary<string, object>)cash["purchases"];
            Expect("cash purchases", pur["total_amount"] == null && ((Dictionary<string, object>)((List<object>)pur["items"])[0])["qty"] != null);
            Expect("cash inventory", ((Dictionary<string, object>)cash["inventory"])["amount"] == null);
            Dictionary<string, object> open = (Dictionary<string, object>)((Dictionary<string, object>)cash["notes_receivable"])["open"];
            Expect("cash notes kept", open["amount"] != null);
            Dictionary<string, object> sales = new Dictionary<string, object>();
            sales["items"] = new List<object> { Map("qty", 1m, "revenue", 9m, "cost_qty", 1m, "cogs", 6m, "gross", 3m, "gross_pct", 33m) };
            Run(p, Item(Requests.ReportRoot + ReportsMgmtSalesReq.Name, null), sales);
            Dictionary<string, object> s = (Dictionary<string, object>)((List<object>)sales["items"])[0];
            Expect("sales cogs", s["cogs"] == null && s["gross"] == null && s["gross_pct"] == null);
            Expect("sales kept", s["revenue"] != null && s["qty"] != null && s["cost_qty"] != null);
        }

        // 分族报表（销售、采购、收付款）：金额按单据类型的字段权限对象遮，数量、单号照给；对象没被拒绝的不遮。
        static void CheckFamilySales()
        {
            PermContext p = Ctx();
            Deny(p, "24", "iPrice");
            Dictionary<string, object> receipt = AmountBody("items", "amount", "amount_tax", "qty");
            Run(p, Item(Requests.ReportRoot + "purchase_analysis", null, "source", "receipt"), receipt);
            Expect("pa receipt", Masked(receipt, "items", "amount", "amount_tax") && Kept(receipt, "items", "qty")
                && ((Dictionary<string, object>)receipt["totals"])["amount_tax"] == null);
            Dictionary<string, object> invoice = AmountBody("items", "amount", "amount_tax", "qty");
            Run(p, Item(Requests.ReportRoot + "purchase_analysis", null), invoice);
            Expect("pa invoice own object", Kept(invoice, "items", "amount") && !invoice.ContainsKey(PermMask.Key));
            Deny(p, "25", "iMoney");
            Run(p, Item(Requests.ReportRoot + "purchase_analysis", null), invoice);
            Expect("pa invoice denied", Masked(invoice, "items", "amount", "amount_tax"));
            Deny(p, "17", "iNatSum");
            Dictionary<string, object> open = AmountBody("items", "open_amount", "open_qty", "lines");
            Run(p, Item(Requests.ReportRoot + "open_orders", null), open);
            Expect("open orders", Masked(open, "items", "open_amount") && Kept(open, "items", "open_qty"));
            Deny(p, "07", "iNatMoney");
            Dictionary<string, object> price = AmountBody("items", "unit_price", "unit_price_tax", "amount", "amount_tax", "qty");
            Run(p, Item(Requests.ReportRoot + "sales_price_history", null), price);
            Expect("price history", Masked(price, "items", "unit_price", "unit_price_tax", "amount", "amount_tax") && Kept(price, "items", "qty"));
            Dictionary<string, object> churn = new Dictionary<string, object>();
            churn["new"] = new List<object> { Map("customer_code", "C900001", "revenue_current", 5m, "months_current", 2) };
            churn["counts"] = Map("new", 1);
            Run(p, Item(Requests.ReportRoot + "customer_churn", null), churn);
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)churn["new"])[0];
            Expect("churn", row["revenue_current"] == null && row["months_current"] != null && row["customer_code"] != null);
            CheckReceipts();
        }

        // 收付款汇总按 side 取应收 / 应付的收款单、付款单对象。
        static void CheckReceipts()
        {
            PermContext p = Ctx();
            Deny(p, "VCH_AR48", "iAmount");
            Dictionary<string, object> ar = AmountBody("items", "amount", "refund_amount", "net_amount", "docs");
            Run(p, Item(Requests.ReportRoot + "receipts_payments", null, "side", "ar"), ar);
            Expect("receipts ar", Masked(ar, "items", "amount", "refund_amount", "net_amount") && Kept(ar, "items", "docs"));
            Dictionary<string, object> ap = AmountBody("items", "amount", "docs");
            Run(p, Item(Requests.ReportRoot + "receipts_payments", null, "side", "ap"), ap);
            Expect("payments ap own object", Kept(ap, "items", "amount") && !ap.ContainsKey(PermMask.Key));
        }

        // 库存金额：收发存汇总、库龄（存货核算金额，从严并上出入库单列表）、存货核算检查的暂估金额（采购入库单）；
        // 已有的 masked_fields（功能权限遮金额时标的 amount）并入；没有金额的报表不遮。
        static void CheckFamilyStock()
        {
            PermContext p = Ctx();
            Deny(p, "0303", "iUnitCost");
            Dictionary<string, object> sum = AmountBody("items", "opening_amt", "in_amt", "out_amt", "closing_amt", "closing_qty");
            Run(p, Item(Requests.ReportRoot + "stock_summary", null), sum);
            Expect("summary", Masked(sum, "items", "opening_amt", "in_amt", "out_amt", "closing_amt") && Kept(sum, "items", "closing_qty"));
            Dictionary<string, object> age = AmountBody("items", "amount", "unit_cost", "qty", "age_days");
            age["masked_fields"] = new List<string> { "amount" };
            Run(p, Item(Requests.ReportRoot + "stock_age", null), age);
            Expect("age", Masked(age, "items", "amount", "unit_cost") && Kept(age, "items", "age_days")
                && ((List<string>)age[PermMask.Key]).Contains("amount") && ((List<string>)age[PermMask.Key]).Contains("unit_cost"));
            Deny(p, "24", "iUnitCost");
            Dictionary<string, object> ia = new Dictionary<string, object>();
            ia["unbilled"] = Map("count", 1, "est_amount", 8m, "items", new List<object> { Map("qty", 2m, "est_amount", 8m) });
            ia["unbooked"] = Map("count", 0, "items", new List<object>());
            Run(p, Item(Requests.ReportRoot + "ia_anomalies", null), ia);
            Dictionary<string, object> unbilled = (Dictionary<string, object>)ia["unbilled"];
            Expect("ia est", unbilled["est_amount"] == null && unbilled["count"] != null && Kept(unbilled, "items", "qty")
                && ((Dictionary<string, object>)((List<object>)unbilled["items"])[0])["est_amount"] == null);
            Dictionary<string, object> none = AmountBody("items", "amount", "qty");
            string[] plain = new string[] { "qm_indicators", "mo_progress", "arrival_otif", "notes_due", "cash_flow", "cost_unit", "close_check" };
            for (int i = 0; i < plain.Length; i++)
            {
                Expect("no plan " + plain[i], PermColumnMap.Plan(Item(Requests.ReportRoot + plain[i], null)).Count == 0);
            }
            Run(p, Item(Requests.ReportRoot + "cost_unit", null), none);
            Expect("cost not masked", Kept(none, "items", "amount"));
            Dictionary<string, object> order = VoucherBody();
            Deny(p, "17", "iPrice");
            Run(p, Item(LoadPath, "sale_order"), order);
            Expect("sale order own object", Line(order, 0)["iPrice"] == null && Line(order, 0)["iUnitCost"] == null);
            PermContext other = Ctx();
            Deny(other, "0303", "iPrice");
            Dictionary<string, object> kept = VoucherBody();
            Run(other, Item(LoadPath, "sale_order"), kept);
            Expect("sale order other object", (string)Line(kept, 0)["iPrice"] == "10");
        }

        // 查询前问一次：字段权限会遮金额吗（PermMask.WillMaskAmounts）。会遮的报表不按金额排序、截断。
        static void CheckAmountOrder()
        {
            PermContext p = Ctx();
            Deny(p, "24", "iPrice");
            List<ColScope> receipt = PermColumnMap.Plan(Item(Requests.ReportRoot + "purchase_analysis", null, "source", "receipt"));
            List<ColScope> invoice = PermColumnMap.Plan(Item(Requests.ReportRoot + "purchase_analysis", null));
            Expect("will mask receipt", PermMask.WillMaskAmounts(p, receipt) && !PermMask.WillMaskAmounts(p, invoice));
            Expect("will mask none", !PermMask.WillMaskAmounts(Ctx(), receipt) && !PermMask.WillMaskAmounts(p, new List<ColScope>())
                && !PermMask.WillMaskAmounts(null, receipt));
            PermContext qty = Ctx();
            Deny(qty, "0303", "iQuantity");
            List<ColScope> age = PermColumnMap.Plan(Item(Requests.ReportRoot + "stock_age", null));
            Expect("will mask quantity only", !PermMask.WillMaskAmounts(qty, age) && PermMask.WillMaskAmounts(p, age));
            p.Supervisor = true;
            Expect("will mask supervisor", !PermMask.WillMaskAmounts(p, receipt));
            WorkContext ctx = new WorkContext();
            ctx.Item = Item("/u8co/v1/vouchers/create", "sale_out");
            ctx.Perm = p;
            Expect("will mask write", !PermMask.WillMaskAmounts(ctx) && !PermMask.WillMaskAmounts(null));
        }

        // 报表自己的对象上的拒绝是栏目标题，对不上响应的键：任何拒绝都按金额组（严格），其他对象仍按字段名。
        static void CheckStrictReports()
        {
            string[][] cases = new string[][]
            {
                new string[] { "open_orders", "SA[__]订单执行统计表", "未发货金额" },
                new string[] { "customer_churn", "SA[__]销售统计表", "本币金额" },
                new string[] { "purchase_analysis", "PU[__]综合统计表", "价税合计" },
                new string[] { "stock_age", "STInventoryAge", "成本" },
                new string[] { "stock_summary", "0c74b9f9-d4a0-43d5-ac1d-ba1c9cb26a69", "Price1" }
            };
            for (int i = 0; i < cases.Length; i++)
            {
                PermContext p = Ctx();
                Deny(p, cases[i][1], cases[i][2]);
                Dictionary<string, object> body = AmountBody("items", "amount", "open_amount", "unit_cost", "closing_amt", "qty");
                int n = Run(p, Item(Requests.ReportRoot + cases[i][0], null), body);
                Expect("strict " + cases[i][0], n > 0 && Masked(body, "items", "amount", "open_amount", "unit_cost", "closing_amt")
                    && Kept(body, "items", "qty") && PermMask.WillMaskAmounts(p, PermColumnMap.Plan(Item(Requests.ReportRoot + cases[i][0], null))));
            }
            PermContext loose = Ctx();
            Deny(loose, "17", "未发货金额");
            Dictionary<string, object> open = AmountBody("items", "open_amount");
            Expect("not strict on voucher object", Run(loose, Item(Requests.ReportRoot + "open_orders", null), open) == 0 && Kept(open, "items", "open_amount"));
        }

        // 收款单、付款单及退款的单据读取按 VCH_AR48 / VCH_AR49 / VCH_AP48 / VCH_AP49 遮（同收付款汇总）。
        static void CheckReceiptVouchers()
        {
            string[][] cases = new string[][]
            {
                new string[] { "ar_receipt", "VCH_AR48" }, new string[] { "ar_refund", "VCH_AR49" },
                new string[] { "ap_payment", "VCH_AP49" }, new string[] { "ap_refund", "VCH_AP48" }
            };
            for (int i = 0; i < cases.Length; i++)
            {
                PermContext p = Ctx();
                Deny(p, cases[i][1], "iAmount");
                Dictionary<string, object> rows = new Dictionary<string, object>();
                rows["rows"] = new List<object> { Map("amount", "5", "docs", "1") };
                Run(p, Item(Requests.ListPath, null, "type", cases[i][0]), rows);
                Expect("receipt voucher " + cases[i][0], Masked(rows, "rows", "amount") && Kept(rows, "rows", "docs"));
            }
        }

        // 一个 items 数组加 totals，每个键一个非空值。
        static Dictionary<string, object> AmountBody(string list, params string[] keys)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i < keys.Length; i++)
            {
                row[keys[i]] = 3m;
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[list] = new List<object> { row };
            body["totals"] = new Dictionary<string, object>(row);
            return body;
        }

        static bool Masked(Dictionary<string, object> body, string list, params string[] keys)
        {
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)body[list])[0];
            for (int i = 0; i < keys.Length; i++)
            {
                if (row[keys[i]] != null)
                {
                    return false;
                }
            }
            return body.ContainsKey(PermMask.Key);
        }

        static bool Kept(Dictionary<string, object> body, string list, string key)
        {
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)body[list])[0];
            return row[key] != null;
        }

        // perm/*、总账、写路由、非 200 不遮；主管、没有字段权限不加 masked_fields。
        static void CheckSkips()
        {
            Expect("skip perm", PermColumnMap.Plan(Item(PermSnapshot.Path, null)).Count == 0
                && PermColumnMap.Plan(Item(PermEvaluate.Path, null)).Count == 0);
            Expect("skip gl", PermColumnMap.Plan(Item(Requests.GlRoot + "load", null)).Count == 0);
            PermContext p = Ctx();
            Deny(p, "0303", "iPrice");
            p.Supervisor = true;
            Dictionary<string, object> body = VoucherBody();
            Expect("skip supervisor", Run(p, Item(LoadPath, "sale_out"), body) == 0 && !body.ContainsKey(PermMask.Key));
            Dictionary<string, object> none = VoucherBody();
            Expect("skip empty", Run(Ctx(), Item(LoadPath, "sale_out"), none) == 0 && (string)Line(none, 0)["iPrice"] == "10");
            WorkContext ctx = new WorkContext();
            ctx.Item = Item("/u8co/v1/vouchers/create", "sale_out");
            ctx.Perm = p;
            ApiResult result = ApiResult.Ok(VoucherBody());
            PermMask.Apply(ctx, result);
            Expect("skip write", !result.Body.ContainsKey(PermMask.Key));
        }

        // 快照 columns：只列有字段的对象、排序；主管 {}；指纹随字段权限变化。
        static void CheckSnapshot()
        {
            PermContext a = Ctx();
            Deny(a, "0303", "iUnitCost");
            Deny(a, "0303", "iPrice");
            a.Columns["26"] = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, object> body = PermSnapshot.ToJson(a);
            Dictionary<string, object> cols = body["columns"] as Dictionary<string, object>;
            Expect("snap columns", cols != null && cols.Count == 1
                && string.Join(",", ((List<string>)cols["0303"]).ToArray()) == "iPrice,iUnitCost");
            string fa = (string)body["fingerprint"];
            Expect("snap fp of", fa == PermSnapshot.FingerprintOf(a));
            Expect("snap fp columns", fa != PermSnapshot.FingerprintOf(Ctx()));
            Expect("snap summary", PermSnapshot.Summary(a, body).Contains(" columns=2 "));
            a.Supervisor = true;
            Expect("snap supervisor columns", ((Dictionary<string, object>)PermSnapshot.ToJson(a)["columns"]).Count == 0);
        }

        static int Run(PermContext p, WorkItem item, Dictionary<string, object> body)
        {
            return PermMask.Mask(p, item.Path, PermColumnMap.Plan(item), body);
        }

        static Dictionary<string, object> VoucherBody()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = "sale_out";
            body["id"] = 1;
            body["head"] = Map("cCode", "0000000001", "cWhCode", "01");
            body["lines"] = new List<object>
            {
                Map("cInvCode", "A01", "iQuantity", "2", "iPrice", "10", "iUnitCost", "5", "iPPrice", "11")
            };
            body["state"] = Map("verified", true);
            return body;
        }

        static Dictionary<string, object> Map(params object[] kv)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < kv.Length; i += 2)
            {
                map[(string)kv[i]] = kv[i + 1];
            }
            return map;
        }

        static Dictionary<string, object> Row(string key, string fld, string group, string deny)
        {
            return Map("k", key, "f", fld, "g", group, "n", deny);
        }

        static Dictionary<string, object> Head(Dictionary<string, object> body)
        {
            return (Dictionary<string, object>)body["head"];
        }

        static Dictionary<string, object> Line(Dictionary<string, object> body, int i)
        {
            return (Dictionary<string, object>)((List<object>)body["lines"])[i];
        }

        static WorkItem Item(string path, string type, params object[] body)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Type = type == null ? null : Kinds.Find(type);
            item.Body = Map(body);
            return item;
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

        static PermContext Ctx()
        {
            PermContext p = new PermContext();
            p.Acc = "998";
            p.Operator = "op1";
            p.Year = 2024;
            p.DateYear = 2024;
            p.AcctYear = 2024;
            return p;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("perm column " + name);
            }
        }
    }
}
