using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的退货申请单 sale_return_apply 部分：种类登记（K12 起可写）、写入的登录前校验与预演模式、列表列与 SQL 占位符、查找（存货、表头自定义项）、
    // 读线程池、权限规则、模板卡片与单据追溯的节点和边。不连库。
    internal static class ReturnsApplySelfTest
    {
        public static void Run()
        {
            CheckKind();
            CheckList();
            CheckWiring();
            CheckTrace();
        }

        static void CheckKind()
        {
            VoucherKind k = Kinds.Find(ReturnsApplyRead.KindName);
            Expect("ra kind", k != null);
            Expect("ra kind cols", All(k.HeadTable == "SA_ReturnsApplyMain", k.IdColumn == "ID", k.CodeColumn == "cCode",
                k.BodyTable == "SA_ReturnsApplyDetail", k.BodyFk == "ID", k.LineIdColumn == "AutoID", k.Family == "sa"));
            // K12：新增、修改、删除、审核走销售 CO（VT 34、卡片 SA31）；不开关闭，也不是生单的目标。
            Expect("ra writable", All(k.Creatable, k.Updatable, k.Deletable, k.Verifiable, !k.Closable, !k.Workflow,
                k.Sources.Length == 0, k.SubId == "SA", k.SaVt == 34, k.SaCard == "SA31", k.VerifyDateColumn == "dverifydate"));
            VoucherKind ret = Kinds.Find("sale_return");
            Expect("ra return source", Array.IndexOf(ret.Sources, ReturnsApplyRead.KindName) > 0 && ret.GenerateFrom == "dispatch");
            CheckWrites();
        }

        // 登录前校验（ReturnsApplyReq）与预演模式表。
        static void CheckWrites()
        {
            string create = "/u8co/v1/vouchers/create";
            string update = "/u8co/v1/vouchers/update";
            Expect("ra create ok", ReqOk(create, "{\"source_line_id\":7,\"quantity\":2,\"cReasonCode\":\"01\",\"cdefine22\":\"x\"}"));
            Expect("ra create no source", !ReqOk(create, "{\"quantity\":2}"));
            Expect("ra create qty", !ReqOk(create, "{\"source_line_id\":7,\"quantity\":\"2\"}"));
            Expect("ra create field", !ReqOk(create, "{\"source_line_id\":7,\"quantity\":2,\"cinvcode\":\"A\"}"));
            Expect("ra update ok", ReqOk(update, "{\"op\":\"update\",\"line_id\":3,\"iquantity\":1.5}"));
            Expect("ra update add", !ReqOk(update, "{\"op\":\"add\",\"cinvcode\":\"A\"}"));
            Expect("ra update empty", !ReqOk(update, "{\"op\":\"update\",\"line_id\":3}"));
            Expect("ra head", ReturnsApplyReq.HeadAllowed("cDefine16", true) && !ReturnsApplyReq.HeadAllowed("cCusCode", true)
                && !ReturnsApplyReq.HeadAllowed("cDepCode", false));
            string[] ops = new string[] { "create", "update", "delete", "verify" };
            for (int i = 0; i < ops.Length; i++)
            {
                // 写入由 SaVoucherService 自行提交（ReturnsApplyTran），预演只到调用之前。
                Expect("ra dry " + ops[i], DryRunModes.Lookup("vouchers/" + ops[i], ReturnsApplyRead.KindName, ops[i], "")
                    == "validate");
            }
            Expect("ra dry gen", DryRunModes.Lookup("vouchers/generate", "sale_return", "generate", ReturnsApplyRead.KindName)
                == "rollback");
        }

        static bool ReqOk(string path, string line)
        {
            WorkItem item = new WorkItem();
            item.Type = Kinds.Find(ReturnsApplyRead.KindName);
            item.Head = new Dictionary<string, object>();
            item.Lines = new object[] { Body(line) };
            try
            {
                return ReturnsApplyReq.Check(item, path);
            }
            catch (BridgeException ex)
            {
                Expect("ra req status", ex.Status == 400);
                return false;
            }
        }

        static void CheckList()
        {
            ListKind kind = ListKinds.Find(ReturnsApplyRead.KindName);
            Expect("ra list kind", kind != null && !kind.HasBodyUfts && kind.Has(ListKind.Apply) && !kind.Has(ListKind.Cond));
            Expect("ra list flags", All(kind.Has(ListKind.Cus), !kind.Has(ListKind.Ven), !kind.Has(ListKind.Wh),
                !kind.Has(ListKind.Red), kind.ClosedSql() != null));
            string keys = string.Join(",", ListSql.FullKeys(kind));
            Expect("ra list keys", keys.EndsWith(",ufts,sale_type,bus_type,wf,verify_state,currency,memo,line_count,quantity,"
                + "amount,created_at,modified_at", StringComparison.Ordinal));
            ListArgs args = ListArgs.Vouchers(Body("{\"type\":\"sale_return_apply\",\"changed_since\":\"123\",\"after\":5,"
                + "\"limit\":20,\"filter\":{\"code\":\"0000000001\",\"date_from\":\"2026-08-01\",\"date_to\":\"2026-08-31\","
                + "\"cus_code\":\"C01\",\"verified\":true,\"closed\":false}}"));
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, null);
            Expect("ra list params", All(Count(sql, '?') == ps.Count, ps.Count == 7, (int)ps[0] == 21, (int)ps[1] == 5));
            Expect("ra list sql", All(sql.Contains(" FROM SA_ReturnsApplyMain h OUTER APPLY (SELECT COUNT(*) AS n"),
                sql.Contains("d.ID = h.ID) b WHERE h.ID > ?"), sql.Contains("h.ufts > CONVERT(binary(8), CONVERT(bigint, ?))"),
                !sql.Contains("d2."), sql.EndsWith(" ORDER BY h.ID", StringComparison.Ordinal)));
            ListArgs keysOnly = ListArgs.Vouchers(Body("{\"type\":\"sale_return_apply\",\"keys_only\":true}"));
            List<object> kp = new List<object>();
            string ksql = ListSql.Vouchers(keysOnly, kp, null);
            Expect("ra keys sql", All(ksql.StartsWith("SELECT TOP (?) h.ID AS id, h.cCode AS code, ", StringComparison.Ordinal),
                Count(ksql, '?') == kp.Count, kp.Count == 2));
            ListRefused("ra wh", "{\"type\":\"sale_return_apply\",\"filter\":{\"wh_code\":\"01\"}}");
            ListRefused("ra ven", "{\"type\":\"sale_return_apply\",\"filter\":{\"ven_code\":\"V01\"}}");
            ListRefused("ra red", "{\"type\":\"sale_return_apply\",\"filter\":{\"red\":true}}");
        }

        static void CheckWiring()
        {
            string name = ReturnsApplyRead.KindName;
            VoucherKind kind = Kinds.Find(name);
            PermRule rule = PermRegistry.ForKey("voucher:" + name);
            Expect("ra rule", rule != null && Array.IndexOf(rule.Auths, "SA03250104") >= 0
                && Array.IndexOf(rule.Auths, "SA03250201") >= 0 && rule.Auths.Length == 2);
            Expect("ra list rule", PermRegistry.Find(Item(Requests.ListPath, "{\"type\":\"" + name + "\"}")) == rule);
            Expect("ra sql load", RouteClass.IsSqlLoad(kind));
            ListKind listKind = ListKinds.Find(name);
            Expect("ra search", VoucherSearchSql.HasInventory(listKind) && VoucherSearchSql.HasPartner(listKind)
                && VoucherSearchDefines.Supports(listKind));
            string[] plan = MetaFieldsVt.Plan(kind);
            Expect("ra vt", plan != null && plan[0] == "SA31" && plan[2] == MetaFieldsVt.Card);
        }

        static void CheckTrace()
        {
            TraceNode node = ReportsTraceMap.Node(ReturnsApplyRead.KindName);
            Expect("ra trace node", node != null && node.Head == "SA_ReturnsApplyMain" && node.Id == "ID");
            bool fromDispatch = false;
            bool toReturn = false;
            TraceEdge[] edges = ReportsTraceMap.All();
            for (int i = 0; i < edges.Length; i++)
            {
                fromDispatch |= edges[i].From == "dispatch" && edges[i].To == ReturnsApplyRead.KindName;
                toReturn |= edges[i].From == ReturnsApplyRead.KindName && edges[i].To == "sale_return"
                    && edges[i].Source.Contains("d.irtnappid=u.AutoID");
            }
            Expect("ra trace edges", fromDispatch && toReturn);
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

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
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
