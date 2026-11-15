using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的 head_fix「改得了就回滚得了」部分（由 VoucherHeadFixSelfTest.Run 调用）：
    // 正向调用对原值的 not_reversible 检查（地址、业务员存在 / 有效期 / 部门 / 操作员数据权限），
    // 写入前的 500 个上限 too_many，回滚模式表头已是目标值时的 state_mismatch，回滚处理顺序固定（出库单、发票、Ar_Detail 各按 id 升序）。不连库。
    internal static class VoucherHeadFixRevertSelfTest
    {
        public static void Run()
        {
            CheckAddr();
            CheckPerson();
            CheckPermission();
            CheckTooMany();
            CheckIdle();
            CheckOrder();
        }

        static void CheckAddr()
        {
            HeadFixField f = Field("addr_code", "01", "02");
            Expect("addr original gone", HeadFixRevert.Original(f, null, "C900001", "2026-03-01", "D901", null), 409, "not_reversible", "01");
            Expect("addr original exists", HeadFixRevert.Original(f, Map("code", "01"), "C900001", "2026-03-01", "D901", null) == null);
            Expect("addr original empty skipped", HeadFixRevert.Original(Field("addr_code", "", "02"), null, "C900001",
                "2026-03-01", "D901", null) == null);
            BridgeException inner = HeadFixRevert.AddrFault(null, "C900001", "01");
            Expect("addr fault", inner.Status == 409 && inner.Code == "address_not_found");
        }

        static void CheckPerson()
        {
            HeadFixField f = Field("person_code", "P900001", "P900002");
            Expect("person original missing", HeadFixRevert.Original(f, null, "C", "2026-03-01", "D901", null), 409, "not_reversible", "不存在");
            Dictionary<string, object> ended = Map("code", "P900001", "dept", "D901", "vfrom", "2020-01-01", "vto", "2026-02-28");
            Expect("person original ended", HeadFixRevert.Original(f, ended, "C", "2026-03-01", "D901", null), 409, "not_reversible", "已失效");
            Dictionary<string, object> later = Map("code", "P900001", "dept", "D901", "vfrom", "2026-04-01", "vto", "");
            Expect("person valid-from after the date is accepted", HeadFixRevert.PersonFault(later, "P900001", "2026-03-01", "D901") == null);
            Dictionary<string, object> moved = Map("code", "P900001", "dept", "D902", "vfrom", "", "vto", "");
            Expect("person original other dept", HeadFixRevert.Original(f, moved, "C", "2026-03-01", "D901", null), 409,
                "not_reversible", "部门");
            Dictionary<string, object> ok = Map("code", "P900001", "dept", "D901", "vfrom", "2020-01-01", "vto", "");
            Expect("person original ok", HeadFixRevert.Original(f, ok, "C", "2026-03-01", "D901", null) == null);
            Expect("person fault codes", HeadFixRevert.PersonFault(ended, "P900001", "2026-03-01", "D901").Code == "person_invalid"
                && HeadFixRevert.PersonFault(moved, "P900001", "2026-03-01", "D901").Code == "person_department_mismatch");
        }

        // 操作员对原业务员没有数据权限：改了就回滚不了，409 not_reversible。
        static void CheckPermission()
        {
            PermContext p = new PermContext();
            p.On.Add(PermObj.Person);
            p.Codes[PermObj.Person] = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            p.Codes[PermObj.Person].Add("P900002");
            Dictionary<string, object> ok = Map("code", "P900001", "dept", "D901", "vfrom", "", "vto", "");
            HeadFixField f = Field("person_code", "P900001", "P900002");
            Expect("person original no permission", HeadFixRevert.Original(f, ok, "C", "2026-03-01", "D901", p), 409,
                "not_reversible", "权限");
            p.Codes[PermObj.Person].Add("P900001");
            Expect("person original with permission", HeadFixRevert.Original(f, ok, "C", "2026-03-01", "D901", p) == null);
            Expect("perm fault code", HeadFixRevert.PermFault(new PermContext(), "P1") == null);
        }

        static void CheckTooMany()
        {
            HeadFixField addr = Field("addr_code", "01", "02");
            List<HeadFixRow> plan = new List<HeadFixRow>();
            for (int i = 1; i <= HeadFixUndo.MaxIds; i++)
            {
                plan.Add(OutRow(i, addr));
            }
            HeadFixUndo.CheckSize(plan);
            plan.Add(OutRow(HeadFixUndo.MaxIds + 1, addr));
            Throws("too many out rows", delegate { HeadFixUndo.CheckSize(plan); }, 409, "too_many");
            HeadFixRow ar = new HeadFixRow();
            ar.Table = HeadFixRow.ArTable;
            ar.Action = HeadFixRow.Updated;
            ar.Set.Add(Field("person_code", "P1", "P2"));
            for (int i = 1; i <= HeadFixUndo.MaxIds + 1; i++)
            {
                ar.AutoIds.Add(i);
            }
            List<HeadFixRow> arPlan = new List<HeadFixRow>();
            arPlan.Add(ar);
            Throws("too many ar rows", delegate { HeadFixUndo.CheckSize(arPlan); }, 409, "too_many");
            HeadFixUndo.CheckSize(new List<HeadFixRow>());
        }

        static HeadFixRow OutRow(int id, HeadFixField f)
        {
            HeadFixRow row = new HeadFixRow();
            row.Table = "rdrecord32";
            row.Id = id;
            row.Action = HeadFixRow.Updated;
            row.Set.Add(f);
            return row;
        }

        static void CheckIdle()
        {
            HeadFixAsk ask = new HeadFixAsk();
            HeadFixUndo.RefuseIdle(ask);
            ask.HasUndo = true;
            HeadFixUndo.RefuseIdle(ask);
            Dictionary<string, List<int>> tables = new Dictionary<string, List<int>>();
            tables["rdrecord32"] = new List<int>();
            ask.Undo["addr_code"] = tables;
            HeadFixUndo.RefuseIdle(ask);
            tables["rdrecord32"].Add(5);
            Throws("idle undo with ids", delegate { HeadFixUndo.RefuseIdle(ask); }, 409, "state_mismatch");
            ask.HasUndo = false;
            HeadFixUndo.RefuseIdle(ask);
        }

        // 处理顺序与 JSON 里的顺序无关：出库单升序、发票升序、Ar_Detail 升序（同一个 id 两个字段都列时连续处理）。
        static void CheckOrder()
        {
            HeadFixAsk ask = new HeadFixAsk();
            ask.HasUndo = true;
            ask.Fields.Add(Field("addr_code", "02", "01"));
            ask.Fields.Add(Field("person_code", "P2", "P1"));
            ask.Undo["person_code"] = Tables(5, 3, 2, 30, 10);
            ask.Undo["addr_code"] = Tables(9, 3, 8, 0, 0);
            LogSource src = new LogSource();
            List<HeadFixRow> plan = HeadFixUndo.Build(ask, ask.Changing(), src);
            Eq("order log", string.Join(",", src.Log.ToArray()),
                "rdrecord32:3,rdrecord32:3,rdrecord32:5,rdrecord32:9,SaleBillVouch:2,SaleBillVouch:8,Ar_Detail:10,Ar_Detail:30");
            List<string> rows = new List<string>();
            foreach (HeadFixRow r in plan)
            {
                rows.Add(r.Table + r.Id.ToString());
            }
            Eq("order rows", string.Join(",", rows.ToArray()), "rdrecord323,rdrecord325,rdrecord329,SaleBillVouch2,SaleBillVouch8,Ar_Detail0");
        }

        // 每张表给两个或一个 id，故意倒序：person 的是 (出库单 5, 3; 发票 2; Ar 30, 10)，addr 的是 (出库单 9, 3; 发票 8)。
        static Dictionary<string, List<int>> Tables(int out1, int out2, int inv, int ar1, int ar2)
        {
            Dictionary<string, List<int>> tables = new Dictionary<string, List<int>>();
            tables["rdrecord32"] = new List<int>(new int[] { out1, out2 });
            tables["SaleBillVouch"] = new List<int>(new int[] { inv });
            if (ar1 > 0)
            {
                tables["Ar_Detail"] = new List<int>(new int[] { ar1, ar2 });
            }
            return tables;
        }

        // 所有 id 都属于这张发货单、当前值都等于 expect（02 / P2）；记录被取用的顺序。
        sealed class LogSource : IHeadFixSource
        {
            public readonly List<string> Log = new List<string>();

            public HeadFixDoc Doc(string table, int id)
            {
                Log.Add(table + ":" + id.ToString());
                HeadFixDoc doc = VoucherHeadFixSelfTest.Out(id, "D" + id.ToString(), "02", "P2", false);
                doc.Table = table;
                doc.VouchType = "26";
                return doc;
            }

            public HeadFixArRow Ar(int autoId)
            {
                Log.Add("Ar_Detail:" + autoId.ToString());
                HeadFixArRow row = new HeadFixArRow();
                row.AutoId = autoId;
                row.VouchType = "26";
                row.Code = "SO";
                row.Person = "P2";
                return row;
            }
        }

        static HeadFixField Field(string key, string old, string value)
        {
            return VoucherHeadFixSelfTest.Field(key, old, value);
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            return VoucherHeadFixSelfTest.Map(pairs);
        }

        static void Expect(string name, BridgeException ex, int status, string code, string text)
        {
            VoucherHeadFixSelfTest.Expect(name + " (" + (ex == null ? "null" : ex.Status + " " + ex.Code + " " + ex.Message) + ")",
                ex != null && ex.Status == status && ex.Code == code && ex.Message.IndexOf(text, StringComparison.Ordinal) >= 0);
        }

        static void Expect(string name, bool ok)
        {
            VoucherHeadFixSelfTest.Expect(name, ok);
        }

        static void Eq(string name, object got, object want)
        {
            VoucherHeadFixSelfTest.Eq(name, got, want);
        }

        static void Throws(string name, Action act, int status, string code)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                VoucherHeadFixSelfTest.Expect(name, ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException("head_fix " + name);
        }
    }
}
