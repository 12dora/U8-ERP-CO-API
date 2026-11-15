using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的批量读取部分：vouchers/load_many 的 ids 校验、COM 类型上限、锁键、线程池分类，
    // archives/get_many 的 codes 校验与权限登记。不连库、不建 COM。
    internal static class LoadManySelfTest
    {
        public static void Run()
        {
            CheckIds();
            CheckRouting();
            CheckCodes();
        }

        static void CheckIds()
        {
            VoucherKind so = Kinds.Find("sale_order");
            VoucherKind pbv = Kinds.Find("purchase_invoice");
            Expect("load_many sql 20", LoadManyReq.Parse(pbv, Ids(20)).Length == 20);
            Expect("load_many com 5", LoadManyReq.Parse(so, Ids(5)).Length == 5);
            Refused("com 6", so, Ids(6), "ids", LoadManyReq.ComHint);
            Refused("sql 21", pbv, Ids(21), "ids", null);
            Refused("empty", pbv, Ids(0), "ids", null);
            Refused("missing", pbv, new Dictionary<string, object>(), "ids", null);
            Refused("dup", pbv, Body(new object[] { 3, 3 }), "ids.1", null);
            Refused("zero", pbv, Body(new object[] { 0 }), "ids.0", null);
            Refused("text", pbv, Body(new object[] { "3" }), "ids.0", null);
            Refused("bool", pbv, Body(new object[] { true }), "ids.0", null);
            Refused("big", pbv, Body(new object[] { 1L + int.MaxValue }), "ids.0", null);
        }

        static void CheckRouting()
        {
            WorkItem com = Item(LoadMany.Path, "sale_order", Body(new object[] { 3, 5 }));
            string[] keys = DocLocks.KeysOf(com);
            Expect("load_many locks", keys.Length == 2 && keys[0] == "sale_order:3" && keys[1] == "sale_order:5");
            Expect("load_many no gate", Array.IndexOf(keys, WriteGate.Key) < 0 && !WriteGate.IsWrite(com));
            Expect("load_many bad locks", DocLocks.KeysOf(Item(LoadMany.Path, "sale_order", Ids(6))).Length == 0);
            Expect("load_many com pool", !RouteClass.IsSqlRead(com) && !RouteClass.IsSqlLoad(Kinds.Find("sale_order")));
            Expect("load_many sql pool", RouteClass.IsSqlRead(Item(LoadMany.Path, "purchase_invoice", null))
                && RouteClass.IsSqlRead(Item(LoadMany.Path, "bom", null)) && RouteClass.IsSqlLoad(Kinds.Find("qm_incoming_check")));
            Expect("load_many perm", PermRegistry.IsRead(LoadMany.Path) && PermRegistry.Find(com) != null
                && PermRegistry.Find(com).Key == "voucher:sale_order");
            CheckArcRouting();
        }

        static void CheckArcRouting()
        {
            WorkItem arc = Item(ArcGetMany.Path, null, Codes("department", new object[] { "D01" }));
            Expect("get_many perm", PermRegistry.IsRead(ArcGetMany.Path) && PermRegistry.Find(arc) != null
                && PermRegistry.Find(arc).Key == "archive:department");
            Expect("get_many pool", RouteClass.IsSqlRead(arc) && DocLocks.KeysOf(arc).Length == 0 && !WriteGate.IsWrite(arc));
        }

        // 档案编码的解析要读 U8 安装目录里的 EAI 字段对照表；没有 U8 的机器上跳过。
        static void CheckCodes()
        {
            if (!Paths.U8HomePresent)
            {
                return;
            }
            Expect("get_many ok", ArcGetMany.Parse(Codes("department", new object[] { "D01", "D02" })).Count == 2);
            Expect("get_many route", Requests.OpOf(ArcGetMany.Path) == ArcGetMany.Op);
            CodeRefused("dup", Codes("department", new object[] { "d01", "D01" }), "codes.1");
            CodeRefused("text", Codes("department", new object[] { 7 }), "codes.0");
            CodeRefused("space", Codes("department", new object[] { " D01" }), "codes.0");
            CodeRefused("empty", Codes("department", new object[0]), "codes");
            CodeRefused("archive", Codes("no_such_archive", new object[] { "D01" }), "archive");
            object[] many = new object[ArcGetMany.MaxCodes + 1];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = "D" + i.ToString(System.Globalization.CultureInfo.InvariantCulture);
            }
            CodeRefused("21", Codes("department", many), "codes");
        }

        static void Refused(string name, VoucherKind kind, Dictionary<string, object> body, string field, string hint)
        {
            try
            {
                LoadManyReq.Parse(kind, body);
            }
            catch (BridgeException ex)
            {
                Expect("load_many refuse " + name, ex.Status == 400 && ex.Field == field && (hint == null || ex.Hint == hint));
                return;
            }
            throw new InvalidOperationException("load_many refuse " + name);
        }

        static void CodeRefused(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                ArcGetMany.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect("get_many refuse " + name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("get_many refuse " + name);
        }

        static Dictionary<string, object> Ids(int n)
        {
            object[] ids = new object[n];
            for (int i = 0; i < n; i++)
            {
                ids[i] = i + 1;
            }
            return Body(ids);
        }

        static Dictionary<string, object> Body(object[] ids)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ids"] = ids;
            return body;
        }

        static Dictionary<string, object> Codes(string archive, object[] codes)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = archive;
            body["codes"] = codes;
            return body;
        }

        static WorkItem Item(string path, string type, Dictionary<string, object> body)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = path;
            item.Type = type == null ? null : Kinds.Find(type);
            item.Body = body;
            item.Date = "2026-01-31";
            return item;
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
