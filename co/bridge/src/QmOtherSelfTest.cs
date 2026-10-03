using System;

namespace U8Co
{
    // --selftest 的其他报检单（QM11）、其他检验单（QM15）读取部分：类型登记、列表行不带审批流列、读线程池、
    // 读取权限、单据模板。不连库、不建 COM。
    internal static class QmOtherSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckList();
            CheckWiring();
            CheckTrace();
            // 写入（K10b）：其他报检单新增、其他检验单生单 / 审核 / 删除。
            QmOthWriteSelfTest.Run();
        }

        static void CheckKinds()
        {
            VoucherKind inspect = Kinds.Find("qm_other_inspect");
            VoucherKind check = Kinds.Find("qm_other_check");
            Expect("oth kinds", inspect != null && check != null);
            Expect("oth inspect", inspect.BizObjectId == "QM11" && inspect.HeadTable == "QMINSPECTVOUCHER"
                && inspect.LineIdColumn == "AUTOID" && QmInspect.Handles(inspect));
            Expect("oth check", check.BizObjectId == "QM15" && check.HeadTable == "QMCHECKVOUCHER" && !QmInspect.Handles(check));
            // 两类都不接审批流：workflow/* 不收、WfState 不登记、读取不附 wf。
            Expect("oth no wf", !inspect.Workflow && !check.Workflow);
        }

        static void CheckList()
        {
            ListKind inspect = ListKinds.Find("qm_other_inspect");
            ListKind check = ListKinds.Find("qm_other_check");
            Expect("oth list", inspect != null && check != null);
            Expect("oth list cond", inspect[ListKind.Cond] == "h.CVOUCHTYPE = N'QM11'"
                && check[ListKind.Cond] == "h.CVOUCHTYPE = N'QM15'");
            Expect("oth list body ufts", inspect.HasBodyUfts && !check.HasBodyUfts);
            foreach (string key in new string[] { "wf", "wf_state", "current_auditor" })
            {
                Expect("oth list no " + key, Array.IndexOf(inspect.ExtraKeys, key) < 0 && Array.IndexOf(check.ExtraKeys, key) < 0);
            }
            Expect("oth list extras", Array.IndexOf(inspect.ExtraKeys, "check_type") >= 0
                && Array.IndexOf(check.ExtraKeys, "inspect_id") >= 0 && Array.IndexOf(check.ExtraKeys, "inspect_code") >= 0);
            Expect("oth search", VoucherSearchSql.HasInventory(inspect) && VoucherSearchSql.HasInventory(check)
                && VoucherSearchDefines.Supports(inspect) && VoucherSearchDefines.Supports(check));
        }

        static void CheckWiring()
        {
            foreach (string name in new string[] { "qm_other_inspect", "qm_other_check" })
            {
                VoucherKind kind = Kinds.Find(name);
                Expect("oth sql load " + name, RouteClass.IsSqlLoad(kind));
                Expect("oth read rule " + name, PermRegistry.ForKey("voucher:" + name) != null);
            }
            string[] inspect = MetaFieldsVt.Plan(Kinds.Find("qm_other_inspect"));
            string[] check = MetaFieldsVt.Plan(Kinds.Find("qm_other_check"));
            Expect("oth vt", inspect[0] == "QM11" && inspect[1] == "361" && check[0] == "QM15" && check[1] == "365");
        }

        // 单据追溯：两类节点已登记，报检 → 检验按 INSPECTAUTOID 关联（不是 SOURCEAUTOID）。
        static void CheckTrace()
        {
            TraceNode inspect = ReportsTraceMap.Node("qm_other_inspect");
            TraceNode check = ReportsTraceMap.Node("qm_other_check");
            Expect("oth trace nodes", inspect != null && check != null
                && inspect.Head == "QMINSPECTVOUCHER" && inspect.Cond == " AND h.CVOUCHTYPE=N'QM11'"
                && check.Head == "QMCHECKVOUCHER" && check.Cond == " AND h.CVOUCHTYPE=N'QM15'");
            int found = 0;
            foreach (TraceEdge e in ReportsTraceMap.All())
            {
                if (e.From == "qm_other_inspect" && e.To == "qm_other_check")
                {
                    found++;
                    Expect("oth trace edge", e.Source.IndexOf("d.INSPECTAUTOID=u.AUTOID", StringComparison.Ordinal) >= 0
                        && e.Source.IndexOf("SOURCEAUTOID", StringComparison.Ordinal) < 0
                        && e.Cond == " AND d.CVOUCHTYPE=N'QM15'");
                }
            }
            Expect("oth trace edge count", found == 1);
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
