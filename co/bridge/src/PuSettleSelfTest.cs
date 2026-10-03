using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的采购结算单读取部分：种类登记（写入另见 PuSettleWriteSelfTest），列表的列、APPLY 与 psufts / psdufts 增量，SQL 占位符，
    // 读线程池、权限规则、查找按存货、模板卡片。只跑纯函数，不连库。
    internal static class PuSettleSelfTest
    {
        public static void Run()
        {
            CheckKind();
            CheckList();
            CheckWiring();
            PuSettleWriteSelfTest.Run();
            PuSettleManSelfTest.Run();
        }

        static void CheckKind()
        {
            VoucherKind kind = Kinds.Find(PuSettleRead.KindName);
            Expect("settle kind", kind != null);
            Expect("settle kind cols", All(kind.HeadTable == "PurSettleVouch", kind.IdColumn == "PSVID",
                kind.CodeColumn == "cSVCode", kind.BodyFk == "PSVID", kind.LineIdColumn == "ID", kind.SubId == "PU"));
            // 写入开放参照采购发票结算、删除（PuSettleWriteSelfTest），手工结算（新增，PuSettleManSelfTest）。
            Expect("settle writes", All(kind.Creatable, !kind.Updatable, kind.Deletable, !kind.Verifiable,
                !kind.Closable, !kind.Workflow, kind.Sources.Length == 1, kind.GenerateFrom == "purchase_invoice",
                kind.VerifierColumn.Length == 0));
        }

        static void CheckList()
        {
            ListKind kind = ListKinds.Find(PuSettleRead.KindName);
            Expect("settle list kind", kind != null);
            Expect("settle list cols", All(kind.HasBodyUfts, kind.VerifiedSql() == "0", kind.ClosedSql() == null,
                kind.Has(ListKind.Ven), !kind.Has(ListKind.Cus), !kind.Has(ListKind.Wh)));
            string keys = string.Join(",", ListSql.FullKeys(kind));
            Expect("settle list keys", keys.EndsWith(",ufts,settle_type,bus_type,pt_code,opening,memo,line_count,accounted_lines,"
                + "invoice_lines,receipt_count,first_invoice_code,first_in_code,quantity,amount", StringComparison.Ordinal));
            ListArgs args = ListArgs.Vouchers(Body("{\"type\":\"purchase_settle\",\"changed_since\":\"123\",\"after\":5,"
                + "\"limit\":20,\"filter\":{\"code\":\"000000000000001\",\"date_from\":\"2026-09-01\","
                + "\"date_to\":\"2026-09-30\",\"ven_code\":\"V1\",\"maker\":\"op001\",\"verified\":false}}"));
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, null);
            Expect("settle list params", All(Count(sql, '?') == ps.Count, ps.Count == 9, (int)ps[0] == 21, (int)ps[1] == 5));
            Expect("settle list sql", All(sql.Contains(" FROM PurSettleVouch h OUTER APPLY (SELECT MAX(d.psdufts) AS bu"),
                sql.Contains("h.psufts > CONVERT(binary(8), CONVERT(bigint, ?))"),
                sql.Contains("d2.psdufts > CONVERT(binary(8), CONVERT(bigint, ?))"),
                sql.EndsWith(" ORDER BY h.PSVID", StringComparison.Ordinal)));
            ListArgs keysOnly = ListArgs.Vouchers(Body("{\"type\":\"purchase_settle\",\"keys_only\":true}"));
            List<object> kp = new List<object>();
            string ksql = ListSql.Vouchers(keysOnly, kp, null);
            Expect("settle keys sql", All(ksql.StartsWith("SELECT TOP (?) h.PSVID AS id, h.cSVCode AS code, ", StringComparison.Ordinal),
                ksql.Contains("b.bu > h.psufts"), Count(ksql, '?') == kp.Count));
            ListRefused("settle cus", "{\"type\":\"purchase_settle\",\"filter\":{\"cus_code\":\"C1\"}}");
            ListRefused("settle closed", "{\"type\":\"purchase_settle\",\"filter\":{\"closed\":true}}");
        }

        static void CheckWiring()
        {
            VoucherKind kind = Kinds.Find(PuSettleRead.KindName);
            PermRule rule = PermRegistry.ForKey("voucher:" + PuSettleRead.KindName);
            Expect("settle rule", rule != null && rule.Auths.Length > 0);
            Expect("settle list rule", PermRegistry.Find(Item(Requests.ListPath, "{\"type\":\"purchase_settle\"}")) == rule);
            Expect("settle sql load", RouteClass.IsSqlLoad(kind));
            Expect("settle search inv", VoucherSearchSql.HasInventory(ListKinds.Find(PuSettleRead.KindName))
                && !VoucherSearchDefines.Supports(ListKinds.Find(PuSettleRead.KindName)));
            string[] plan = MetaFieldsVt.Plan(kind);
            Expect("settle vt", plan != null && plan[0] == "99" && plan[2] == MetaFieldsVt.Card);
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
        }

        static void ListRefused(string name, string json)
        {
            try
            {
                ListArgs.Vouchers(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static WorkItem Item(string path, string json)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = Body(json);
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

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
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
