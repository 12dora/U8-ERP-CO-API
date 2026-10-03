using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的固定资产写入与设备台账部分（FaCardReq、FaCardEai、FaPeriod、FaCardSql、ArcEq）：
    // 只测纯逻辑（档案登记、请求校验、EAI 报文、期间与撤销闸门、预演模式、写入分类、权限登记），不连库、不建 COM。
    internal static class FaWriteSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckOtherKinds();
            CheckCardReq();
            CheckEnvelopes();
            CheckGates();
            CheckWiring();
        }

        static void CheckKinds()
        {
            ArcKind fa = ArcKind.Find(ArcFa.Name);
            Expect("fa kind", !fa.ReadOnly && fa.RoRead && fa.NoUpdate && !fa.NoDelete);
            Expect("fa map", !Paths.U8HomePresent
                || (ArcMap.Of(fa).Canon("original_value") != null && ArcMap.Of(fa).Canon("asset_num") == null));
        }

        static void CheckOtherKinds()
        {
            ArcKind eq = ArcKind.Find(ArcEq.Name);
            Expect("eq kind", !eq.ReadOnly && eq.RoRead && eq.NoUpdate && eq.NoDelete && eq.Ts == "ufts" && eq.Table == "EQ_EQData");
            Expect("eq map", !Paths.U8HomePresent
                || (ArcMap.Of(eq).Canon("CEQTYPECODE") != null && ArcMap.Of(eq).Canon("cmaker") == null));
        }

        static void CheckCardReq()
        {
            ArcReq req = Arc(ArcFa.Name, "create", "900001", Card());
            FaCardPlan plan = FaCardReq.Plan(req);
            Expect("fa plan", plan.AssetNum == "900001" && plan.Dept == "0101" && plan.Value == 1200m && !plan.Tags.ContainsKey("deptno")
                && plan.Tags["life"] == "60" && plan.Tags["originalvalue"] == "1200" && plan.Currency == null);
            Rejected("fa update", ArcFa.Name, "update", "00001", Fields("name", "x"), "变动单");
            Rejected("fa no type", ArcFa.Name, "create", "900001", Without(Card(), "type_code"), "type_code");
            Rejected("fa bad date", ArcFa.Name, "create", "900001", With(Card(), "start_date", "2026/08/01"), "yyyy-mm-dd");
            Rejected("fa depr cap", ArcFa.Name, "create", "900001", With(Card(), "accumulated_depreciation", 1300), "原值");
            Rejected("fa rate", ArcFa.Name, "create", "900001", With(Card(), "net_salvage_rate", 1), "小于 1");
            Rejected("fa zero", ArcFa.Name, "create", "900001", With(Card(), "original_value", 0), "大于 0");
            Rejected("fa life", ArcFa.Name, "create", "900001", With(Card(), "useful_life_months", 12000), "整数");
            Rejected("fa unknown", ArcFa.Name, "create", "900001", With(Card(), "asset_num", "1"), "未知字段");
            Dictionary<string, object> body = Body(ArcFa.Name, "900001", Card());
            body["template"] = "00001";
            Expect("fa template", Status(delegate { ArcReq.Parse("create", body); }) == 400);
            Expect("fa delete ok", ArcReq.Parse("delete", Body(ArcFa.Name, "00508", null)).Code == "00508");
            Rejected("eq update", ArcEq.Name, "update", "ZZ01", Fields("name", "x"), "只支持新增");
            Rejected("eq delete", ArcEq.Name, "delete", "ZZ01", null, "不支持删除");
            Rejected("eq date", ArcEq.Name, "create", "ZZ01", Fields("name", "设备", "dtgmdate", "2026-13-01"), "yyyy-mm-dd");
            Rejected("eq int", ArcEq.Name, "create", "ZZ01", Fields("name", "设备", "intdjnum", "1.5"), "整数");
            Rejected("eq maker", ArcEq.Name, "create", "ZZ01", Fields("name", "设备", "cmaker", "张三"), "未知字段");
            Expect("eq ok", ArcReq.Parse("create", Body(ArcEq.Name, "ZZ01", Fields("name", "设备", "intsynx", 8.5))).Code == "ZZ01");
        }

        static void CheckEnvelopes()
        {
            FaCardPlan plan = FaCardReq.Plan(Arc(ArcFa.Name, "create", "900001", Card()));
            string add = FaCardEai.Add(plan, "人民币");
            Expect("fa add xml", add.IndexOf("roottag='capitalasserts'", StringComparison.Ordinal) > 0
                && add.IndexOf("proc='add'", StringComparison.Ordinal) > 0
                && add.IndexOf("<header><assetno>900001</assetno><assetname>测试设备 &amp; 夹具</assetname><typeno>50</typeno>"
                    + "<originalvalue>1200</originalvalue><startusedate>2026-08-15</startusedate><currency>人民币</currency>"
                    + "<exchangerate>1</exchangerate>", StringComparison.Ordinal) > 0
                && add.IndexOf("<body><entry><assetno>900001</assetno><deptno>0101</deptno><deptscale>1</deptscale></entry></body>"
                    + "</capitalasserts></ufinterface>", StringComparison.Ordinal) > 0
                && add.IndexOf("<deptno>0101</deptno><deptscale>", StringComparison.Ordinal) > 0);
            string del = FaCardEai.Delete("900001");
            Expect("fa delete xml", del.IndexOf("proc='delete'", StringComparison.Ordinal) > 0
                && del.IndexOf("<capitalasserts><header><assetno>900001</assetno></header></capitalasserts>", StringComparison.Ordinal) > 0);
            ArcReq eq = ArcReq.Parse("create", Body(ArcEq.Name, "ZZ01", Fields("name", "设备", "cdepcode", "01", "cdefine3", "甲")));
            string xml = ArcEq.Envelope(eq.Code, eq.Fields, "张三", "2026-09-15");
            Expect("eq xml", xml.IndexOf("roottag='eqdata'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<eqdata><header><ceqcode>ZZ01</ceqcode><ceqname>设备</ceqname><cdepcode>01</cdepcode>"
                    + "<cmaker>张三</cmaker><dtdate>2026-09-15</dtdate><cdefine3>甲</cdefine3></header></eqdata>", StringComparison.Ordinal) > 0);
        }

        static void CheckGates()
        {
            DateTime login = new DateTime(2026, 9, 15);
            Expect("period ok", FaPeriod.Judge(login, "9", "2026-09-01", "0") == null && FaPeriod.Judge(login, " 9 ", null, null) == null);
            Expect("period none", FaPeriod.Judge(login, null, null, null).IndexOf("没有启用", StringComparison.Ordinal) >= 0);
            Expect("period month", FaPeriod.Judge(login, "8", "2026-08-01", "0").IndexOf("第 8 期", StringComparison.Ordinal) >= 0);
            Expect("period year", FaPeriod.Judge(login, "9", "2025-09-01", "0") != null);
            // 只按登录日期的年月与固定资产当前期间比，请求的账套库年度（year）不参与。
            Expect("period db year", FaPeriod.Judge(new DateTime(2026, 9, 1), "9", "2026-09-01", "0") == null
                && FaPeriod.Of(new DateTime(2026, 9, 1)).Year == 2026);
            Expect("period closed", FaPeriod.Judge(login, "9", "2026-09-01", "1").IndexOf("已结账", StringComparison.Ordinal) >= 0);
            FaPeriod p = FaPeriod.Of(login);
            Expect("period span", p.First == "2026-09-01" && p.Last == "2026-09-30" && p.Label() == "2026 年第 9 期");
            Expect("undo ok", FaCardSql.UndoRefusal(Undo("2026-09-15", null, "0"), p) == null);
            Expect("undo disposed", FaCardSql.UndoRefusal(Undo("2026-09-15", "2026-09-30", "0"), p) != null);
            FaUndoFacts two = Undo("2026-09-15", null, "0");
            two.Versions = 2;
            Expect("undo versions", FaCardSql.UndoRefusal(two, p) != null
                && FaCardSql.UndoRefusal(Undo("2026-09-15", null, "1"), p) != null);
            Expect("undo period", FaCardSql.UndoRefusal(Undo("2026-08-31", null, "0"), p) != null
                && FaCardSql.UndoRefusal(Undo("2026-10-01", null, "0"), p) != null && FaCardSql.UndoRefusal(Undo(null, null, "0"), p) != null);
            CheckUndoMore(p);
        }

        // P2-4 / P2-5：录入方式、制单、本期折旧、资产编号唯一。
        static void CheckUndoMore(FaPeriod p)
        {
            FaUndoFacts f = Undo("2026-09-15", null, "0");
            f.OptType = "3";
            Expect("undo opt", Has(FaCardSql.UndoRefusal(f, p), "不是新增"));
            f.OptType = null;
            Expect("undo opt null", Has(FaCardSql.UndoRefusal(f, p), "不是新增"));
            f = Undo("2026-09-15", null, "0");
            f.Voucher = "1";
            Expect("undo voucher", Has(FaCardSql.UndoRefusal(f, p), "已制单"));
            f.Voucher = "0";
            Expect("undo voucher none", FaCardSql.UndoRefusal(f, p) == null);
            f.OptType = "1";
            Expect("undo original card", FaCardSql.UndoRefusal(f, p) == null);
            f = Undo("2026-09-15", null, "0");
            f.Depr = "1";
            Expect("undo depr", Has(FaCardSql.UndoRefusal(f, p), "已计提折旧"));
            f.Depr = "0";
            Expect("undo depr none", FaCardSql.UndoRefusal(f, p) == null);
            f.SameAsset = "2";
            Expect("undo shared asset", Has(FaCardSql.UndoRefusal(f, p), "其他卡片"));
            f.SameAsset = "1";
            Expect("undo unique asset", FaCardSql.UndoRefusal(f, p) == null);
        }

        static FaUndoFacts Undo(string input, string disposed, string changes)
        {
            FaUndoFacts f = new FaUndoFacts();
            f.Versions = 1;
            f.Input = input;
            f.Disposed = disposed;
            f.Changes = changes;
            f.OptType = "2";
            f.Depr = "0";
            f.SameAsset = "1";
            return f;
        }

        static bool Has(string text, string part)
        {
            return text != null && text.IndexOf(part, StringComparison.Ordinal) >= 0;
        }

        static void CheckWiring()
        {
            Expect("dry fa", DryRunModes.Lookup("archives/create", ArcFa.Name, "", "") == DryRunModes.Validate
                && DryRunModes.Lookup("archives/delete", ArcFa.Name, "", "") == DryRunModes.Validate
                && DryRunModes.Lookup("archives/create", ArcEq.Name, "", "") == DryRunModes.Validate);
            Expect("class fa", WriteClass.Of(Requests.ArcRoot + "create", Body(ArcFa.Name, "1", null)).Type == ArcFa.Name
                && WriteClass.Of(Requests.ArcRoot + "delete", Body(ArcFa.Name, "1", null)).Type == ArcFa.Name
                && WriteClass.Of(Requests.ArcRoot + "create", Body(ArcEq.Name, "1", null)).Type == "archives");
            // 变动单不开放（本机 U8 的 EAI 没有 capitalvouchers 的导入样式表）。
            Expect("no change kind", Kinds.Find("fa_change") == null);
            string[] keys = new string[]
            {
                ArcGuard.RuleKey(ArcFa.Name, "create"), ArcGuard.RuleKey(ArcFa.Name, "delete"),
                "archive:" + ArcEq.Name, ArcGuard.RuleKey(ArcEq.Name, "create")
            };
            for (int i = 0; i < keys.Length; i++)
            {
                Expect("perm " + keys[i], PermRegistry.ForKey(keys[i]) != null);
            }
            Expect("perm ids", Array.IndexOf(PermRegistry.ForKey(ArcGuard.RuleKey(ArcFa.Name, "create")).Auths, "FA1502") >= 0);
        }

        static Dictionary<string, object> Card()
        {
            return Fields("name", "测试设备 & 夹具", "type_code", "50", "original_value", "1200.00", "start_date", "2026-08-15",
                "origin_code", "101", "status_code", "1001", "depreciation_method_code", "3", "dept_code", "0101",
                "useful_life_months", 60, "net_salvage_rate", 0.05);
        }

        static Dictionary<string, object> With(Dictionary<string, object> map, string key, object value)
        {
            map[key] = value;
            return map;
        }

        static Dictionary<string, object> Without(Dictionary<string, object> map, string key)
        {
            map.Remove(key);
            return map;
        }

        static ArcReq Arc(string archive, string op, string code, Dictionary<string, object> fields)
        {
            return ArcReq.Parse(op, Body(archive, code, fields));
        }

        static Dictionary<string, object> Body(string archive, string code, Dictionary<string, object> fields)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = archive;
            body["code"] = code;
            if (fields != null)
            {
                body["fields"] = fields;
            }
            return body;
        }

        static Dictionary<string, object> Fields(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static void Rejected(string name, string archive, string op, string code, Dictionary<string, object> fields, string text)
        {
            try
            {
                Arc(archive, op, code, fields);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && (ex.Message ?? "").IndexOf(text, StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static int Status(Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
            return 0;
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
