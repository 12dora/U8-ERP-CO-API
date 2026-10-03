using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的单据搜索部分：每种类型带上它支持的全部条件（含表头自定义项）时 SQL 的 ? 个数与参数一致，不支持的条件 400 且 field 正确。不连库。
    internal static class VoucherSearchSelfTest
    {
        static readonly HashSet<string> NoInventory = new HashSet<string>(StringComparer.Ordinal)
        {
            "ar_receipt", "ap_payment", "ar_bill", "ap_bill", "ap_refund", "ar_refund",
            // 票据（ListKindsNotes.cs）：没有存货。
            "ar_note", "ap_note"
        };

        // 表头表没有 cDefine1–16 的类型（生产订单的列叫 Define1–16，物料清单没有）；票据未开放表头自定义项。
        static readonly HashSet<string> NoDefines = new HashSet<string>(StringComparer.Ordinal)
        {
            "production_order", "bom", "ar_note", "ap_note",
            // 采购结算单：PurSettleVouch 没有 cDefine 列。
            PuSettleRead.KindName,
            // 存货调价单：表头自定义项的列类型未在有数据的账套核对，暂不开放。
            InvPriceAdjustRead.KindName
        };

        public static void Run()
        {
            foreach (string name in ListKinds.Names())
            {
                CheckKind(name);
            }
            Expect("search read", PermRegistry.IsRead(VoucherSearch.Path) && RouteClass.IsSqlRead(Item("sale_order")));
            Expect("search like", VoucherSearchArgs.Parse(Body("sale_order", "code_like", "a_b%")).CodeLike == "%a\\_b\\%%");
            Expect("search default limit", VoucherSearchArgs.Parse(Body("sale_order", null, null)).Limit == VoucherSearchArgs.DefaultLimit);
            Expect("search partner name", Array.IndexOf(VoucherSearchSql.Keys(ListKinds.Find("ar_receipt")), VoucherSearchSql.PartnerName) >= 0
                && Array.IndexOf(VoucherSearchSql.Keys(ListKinds.Find("production_order")), VoucherSearchSql.PartnerName) < 0);
            Refused("partner", Body("production_order", "partner", "C01"), "partner");
            Refused("warehouse", Body("sale_order", "warehouse", "01"), "warehouse");
            Refused("inventory", Body("ar_bill", "inventory", "M1"), "inventory");
            Refused("closed", Body("sale_invoice", "closed", true), "closed");
            Refused("dept", Body("production_order", "dept", "01"), "dept");
            Refused("limit", Body("sale_order", "limit", VoucherSearchArgs.MaxLimit + 1), "limit");
            Refused("after", Body("sale_order", "after", -1), "after");
            Refused("code_like", Body("sale_order", "code_like", new string('x', 41)), "code_like");
            Refused("date", Body("sale_order", "date_from", "2026-13-01"), "date_from");
            Refused("verified", Body("sale_order", "verified", "yes"), "verified");
            Refused("type", Body("no_such_type", null, null), "type");
            Defines();
        }

        // 表头自定义项（VoucherSearchDefines）：条件、选择列、LIKE 转义，以及各种 400 的 field。
        static void Defines()
        {
            Dictionary<string, object> defines = new Dictionary<string, object>();
            defines["define1"] = "\u3000HT-001 \u00A0";
            defines["define10"] = Spec("like", "a_b");
            defines["define11"] = Spec("prefix", "X%");
            VoucherSearchArgs args = VoucherSearchArgs.Parse(Body("sale_order", "defines", defines));
            List<object> ps = new List<object>();
            string sql = VoucherSearchSql.Build(args, ps, null);
            Expect("defines params", Count(sql, '?') == ps.Count && ps.Contains("HT-001") && ps.Contains("%a\\_b%")
                && ps.Contains("X\\%%"));
            Expect("defines sql", sql.Contains("REPLACE(h.cDefine1, NCHAR(12288), N' '), NCHAR(160), N' '))) = ?")
                && sql.Contains("AS def_define10") && sql.Contains("REPLACE(h.cDefine11, NCHAR(12288)"));
            Expect("defines none", VoucherSearchArgs.Parse(Body("sale_order", null, null)).Defines == null);
            Refused("define17", Body("sale_order", "defines", One("define17", "x")), "defines.define17");
            Refused("define01", Body("sale_order", "defines", One("define01", "x")), "defines.define01");
            Refused("define1 nul", Body("sale_order", "defines", One("define1\u0000", "x")), "defines");
            Refused("define1 space", Body("sale_order", "defines", One("define1 ", "x")), "defines");
            Refused("define+1", Body("sale_order", "defines", One("define+1", "x")), "defines");
            Refused("define4 date", Body("sale_order", "defines", One("define4", "x")), "defines.define4");
            Refused("defines kind", Body("production_order", "defines", One("define1", "x")), "defines");
            Refused("defines empty", Body("sale_order", "defines", new Dictionary<string, object>()), "defines");
            Refused("defines text", Body("sale_order", "defines", "HT-001"), "defines");
            Refused("defines blank", Body("sale_order", "defines", One("define1", " \u3000\u00A0")), "defines.define1");
            Refused("defines long", Body("sale_order", "defines", One("define1", new string('x', 121))), "defines.define1");
            Refused("defines op", Body("sale_order", "defines", One("define1", Spec("regex", "x"))), "defines.define1");
            Dictionary<string, object> two = Spec("eq", "x");
            two["like"] = "y";
            Refused("defines two ops", Body("sale_order", "defines", One("define1", two)), "defines.define1");
            Dictionary<string, object> five = new Dictionary<string, object>();
            foreach (string key in new string[] { "define1", "define2", "define3", "define8", "define9" })
            {
                five[key] = "x";
            }
            Refused("defines many", Body("sale_order", "defines", five), "defines");
        }

        static Dictionary<string, object> Spec(string op, object value)
        {
            Dictionary<string, object> spec = new Dictionary<string, object>();
            spec[op] = value;
            return spec;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            return Spec(key, value);
        }

        // 带上该类型支持的全部条件，SQL 的 ? 个数等于参数个数；inventory 支持与否同常量表。
        static void CheckKind(string name)
        {
            ListKind kind = ListKinds.Find(name);
            Expect("search inventory " + name, VoucherSearchSql.HasInventory(kind) == !NoInventory.Contains(name));
            Expect("search defines " + name, VoucherSearchDefines.Supports(kind) == !NoDefines.Contains(name));
            Dictionary<string, object> body = Body(name, "code_like", "01");
            body["maker"] = "op001";
            body["date_from"] = "2026-01-01";
            body["date_to"] = "2026-01-31";
            body["verified"] = true;
            body["after"] = 7;
            body["limit"] = 10;
            Optional(body, kind.Has(ListKind.Cus) || kind.Has(ListKind.Ven), "partner", "C01");
            Optional(body, kind.Has(ListKind.Dep), "dept", "01");
            Optional(body, kind.Has(ListKind.Person), "person", "P01");
            Optional(body, kind.Has(ListKind.Wh), "warehouse", "W01");
            Optional(body, kind.ClosedSql() != null, "closed", false);
            Optional(body, VoucherSearchSql.HasInventory(kind), "inventory", "M1");
            Optional(body, VoucherSearchDefines.Supports(kind), "defines", One("define1", Spec("like", "HT")));
            VoucherSearchArgs args = VoucherSearchArgs.Parse(body);
            List<object> ps = new List<object>();
            string sql = VoucherSearchSql.Build(args, ps, null);
            Expect("search params " + name, Count(sql, '?') == ps.Count && (int)ps[0] == 11 && (int)ps[1] == 7);
            Expect("search order " + name, sql.EndsWith(" ORDER BY h." + kind[ListKind.Id], StringComparison.Ordinal));
        }

        static void Optional(Dictionary<string, object> body, bool supported, string key, object value)
        {
            if (supported)
            {
                body[key] = value;
            }
        }

        static void Refused(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                VoucherSearchArgs.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect("search refuse " + name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("search refuse " + name);
        }

        static Dictionary<string, object> Body(string type, string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = type;
            if (key != null)
            {
                body[key] = value;
            }
            return body;
        }

        static WorkItem Item(string type)
        {
            WorkItem item = new WorkItem();
            item.Path = VoucherSearch.Path;
            item.Type = Kinds.Find(type);
            return item;
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
