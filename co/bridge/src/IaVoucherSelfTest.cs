using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的存货核算制单、取消制单部分（ia/voucher、ia/voucher/delete）：请求校验、锁键、路由登记（写闸门、预演模式、
    // 权限、写入分类），外部业务号格式，科目匹配的排序（存货科目的级别、对方科目按 U8 的升序取最后一行），摘要、分录排列与闸门。
    // 不连库、不建 COM。
    internal static class IaVoucherSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckRoutes();
            CheckHead();
            CheckOpp();
            CheckLines();
            CheckBill();
            CheckKeys();
            IaVoucherPairSelfTest.Run();
        }

        // 合并键（业务员、存货方另分组）、排序稳定、业务类型前两个字、回冲单来源、导入被拒时的重试判断、按分类又按仓库的存货科目行。
        static void CheckKeys()
        {
            GlLine probe = Line("1405", 0, 0).Line;
            string a = IaVoucherLines.KeyOf(probe, true, "P900001", true);
            Expect("iv key person", All(a != IaVoucherLines.KeyOf(probe, true, "", true), a != IaVoucherLines.KeyOf(probe, true, "P900001", false),
                a == IaVoucherLines.KeyOf(probe, true, "P900001", true)));
            IaLine x = Line("1405", 5, 0);
            IaLine y = Line("1405", 5, 0);
            x.Seq = 1;
            Expect("iv stable", All(IaVoucherLines.CompareLines(x, y) > 0, IaVoucherLines.CompareLines(y, x) < 0));
            Expect("iv pair prefix", All(IaType.PairOf("09", "组装出库") == "组装", IaType.PairOf("08", "调拨入库") == "调拨",
                IaType.PairOf("11", "调拨出库") == "", IaType.PairOf("09", "其他出库") == "", IaType.PairOf("09", "") == ""));
            Expect("iv source", All(IaType.Find("24").SourceOk(""), IaType.Find("30").SourceOk("01"), !IaType.Find("30").SourceOk("70"),
                IaType.Find("09").SourceOk("70")));
            Expect("iv retry gl", All(IaVoucherSave.SystemRefused("x " + IaVoucherSave.SystemRejected + " y"), !IaVoucherSave.SystemRefused("科目不存在"),
                !IaVoucherSave.SystemRefused(null)));
            try
            {
                IaVoucherAcct.HeadRank(Head("W1", "01", ""), Row("09", "1001", "0101", "W1"));
                throw new InvalidOperationException("iv head class wh");
            }
            catch (BridgeException ex)
            {
                Expect("iv head class wh", ex.Status == 409);
            }
            Expect("iv pz year", IaVoucherBack.PzYear("2026IA0000000000001") == 2026);
        }

        static void CheckParse()
        {
            IaVoucherAsk ids = IaVoucherReq.Parse(Body("{\"ids\":[{\"type\":\"01\",\"id\":5},{\"type\":\"3201\",\"id\":7}],\"merge\":\"all\"}"));
            Expect("iv ids", All(ids.Month == 0, ids.Ids.Count == 2, ids.Ids[1].Type == "3201", ids.Ids[1].Id == 7, ids.All));
            IaVoucherAsk month = IaVoucherReq.Parse(Body("{\"month\":9,\"types\":[\"09\",\"11\",\"09\"],\"sign\":\"转\",\"digest\":\" 文本A \","
                + "\"voucher_date\":\"2026-09-30\"}"));
            Expect("iv month", All(month.Month == 9, month.Types.Count == 2, !month.All, month.Sign == "转",
                month.Digest == "文本A", month.Date == "2026-09-30"));
            string keys = string.Join("|", IaVoucherReq.LockKeys(IaVoucherReq.Path, Body("{\"month\":9}")));
            Expect("iv keys", keys == "period:ia|ia:voucher|new:gl:转|new:gl:记|new:gl:收|new:gl:付");
            keys = string.Join("|", IaVoucherReq.LockKeys(IaVoucherReq.Path, Body("{\"month\":9,\"sign\":\"记\"}")));
            Expect("iv keys sign", keys == "period:ia|ia:voucher|new:gl:记");
            keys = string.Join("|", IaVoucherReq.LockKeys(IaVoucherReq.DropPath, Body("{\"pz_id\":\"2026IA0000000000001\"}")));
            Expect("iv drop keys", keys == "period:ia|ia:voucher");
            Expect("iv bad keys", IaVoucherReq.LockKeys(IaVoucherReq.Path, Body("{\"month\":0}")).Length == 0);
            Expect("iv drop parse", IaVoucherReq.ParseDrop(Body("{\"pz_id\":\"2026IA0000000000001\"}")).PzId == "2026IA0000000000001");
            Expect("iv pz id", IaVoucherNo.PzId(2026, 1) == "2026IA0000000000001");
            Expect("iv actions", All(IaVoucherReq.ActionOf(IaVoucherReq.Path) == "ia_voucher",
                IaVoucherReq.ActionOf(IaVoucherReq.DropPath) == "ia_voucher_delete"));
        }

        static void CheckBad()
        {
            Bad("iv none", "{}", "ids");
            Bad("iv both", "{\"ids\":[{\"type\":\"01\",\"id\":1}],\"month\":1}", "month");
            Bad("iv types ids", "{\"ids\":[{\"type\":\"01\",\"id\":1}],\"types\":[\"01\"]}", "types");
            Bad("iv month range", "{\"month\":13}", "month");
            Bad("iv type shape", "{\"ids\":[{\"type\":\"sale_out\",\"id\":1}]}", "ids.0.type");
            Bad("iv id zero", "{\"ids\":[{\"type\":\"01\",\"id\":0}]}", "ids.0.id");
            Bad("iv extra", "{\"ids\":[{\"type\":\"01\",\"id\":1,\"x\":1}]}", "ids.0");
            Bad("iv dup", "{\"ids\":[{\"type\":\"01\",\"id\":1},{\"type\":\"01\",\"id\":1}]}", "ids.1");
            Bad("iv merge", "{\"month\":1,\"merge\":\"one\"}", "merge");
            Bad("iv digest", "{\"month\":1,\"digest\":\"\"}", "digest");
            Bad("iv date", "{\"month\":1,\"voucher_date\":\"2026/01/31\"}", "voucher_date");
            Bad("iv too many", "{\"ids\":[" + Ids(IaVoucherReq.BillMax + 1) + "],\"merge\":\"all\"}", "ids");
            Bad("iv per bill max", "{\"ids\":[" + Ids(IaVoucherReq.PerBillMax + 1) + "]}", "ids");
            try
            {
                IaVoucherReq.ParseDrop(Body("{\"pz_id\":\"AP0000000000001\"}"));
                throw new InvalidOperationException("iv drop bad");
            }
            catch (BridgeException ex)
            {
                Expect("iv drop bad", All(ex.Status == 400, ex.Field == "pz_id"));
            }
        }

        static void CheckRoutes()
        {
            string[] paths = new string[] { IaVoucherReq.Path, IaVoucherReq.DropPath };
            string[] routes = new string[] { "ia/voucher", "ia/voucher/delete" };
            string[] modes = new string[] { DryRunModes.Validate, DryRunModes.Rollback };
            string[] ops = new string[] { "voucher", "other" };
            for (int i = 0; i < paths.Length; i++)
            {
                string path = paths[i];
                Expect("iv write " + path, All(WriteGate.IsWrite(path), IdemReq.Supports(path), DryRunReq.Accepts(path)));
                Expect("iv dry mode " + path, DryRunModes.Lookup(path, "", "", "") == modes[i]);
                Expect("iv meta dry " + path, (string)MetaDryRun.Routes()[routes[i]] == modes[i]);
                Expect("iv not read " + path, !PermRegistry.IsRead(path));
                Expect("iv perm " + path, PermRegistry.ForKey(IaVoucherReq.RuleKey(i == 1)) != null);
                WriteRequestInfo info = WriteClass.Of(path, new Dictionary<string, object>());
                Expect("iv classified " + path, info != null);
                Expect("iv class " + path, All(info.Type == "ia", info.Op == ops[i]));
            }
        }

        // 存货科目：按存货的行优先于按分类的行，分类长的优先，仓库不符的不算；对方科目列取法。
        static void CheckHead()
        {
            IaRow row = Row("09", "1001", "0101", "W1");
            Expect("iv head inv", Rank(Head("", "", "1001"), row)[0] == 3);
            Expect("iv head class", All(Rank(Head("", "01", ""), row)[0] == 2, Rank(Head("", "0101", ""), row)[1] == 4));
            Expect("iv head wh", All(Rank(Head("W1", "", ""), row)[0] == 1, IaVoucherAcct.HeadRank(Head("W2", "", ""), row) == null));
            Expect("iv head miss", All(IaVoucherAcct.HeadRank(Head("", "02", ""), row) == null, Rank(Head("", "", ""), row)[0] == 0));
            IaRow est = Row("01", "1001", "0101", "W1");
            est.BFlag = "1";
            Expect("iv opp col est", IaVoucherAcct.OppColumn(est) == "o_est");
            est.BFlag = "0";
            Expect("iv opp col opp", All(IaVoucherAcct.OppColumn(est) == "o_opp", IaVoucherAcct.OppColumn(Row("24", "", "", "")) == "o_est",
                IaVoucherAcct.OppColumn(Row("3201", "", "", "")) == "h_out", IaVoucherAcct.OppColumn(Row("11", "", "", "")) == "o_opp"));
        }

        // 对方科目：填了的键要一致；U8 升序排取最后一行 = 逐键填了的大、分类前缀长的大。
        static void CheckOpp()
        {
            IaRow row = Row("11", "1001", "0101", "W1");
            row.Rd = "103";
            row.Dept = "D01";
            string[] byRd = IaVoucherAcct.OppKeys(Opp("103", "", "", ""), row);
            string[] byRdClass = IaVoucherAcct.OppKeys(Opp("103", "", "01", ""), row);
            string[] byRdLong = IaVoucherAcct.OppKeys(Opp("103", "", "0101", "D01"), row);
            string[] byInv = IaVoucherAcct.OppKeys(Opp("", "1001", "", ""), row);
            Expect("iv opp match", All(byRd != null, byRdClass != null, byRdLong != null, byInv != null));
            Expect("iv opp miss", All(IaVoucherAcct.OppKeys(Opp("101", "", "", ""), row) == null,
                IaVoucherAcct.OppKeys(Opp("103", "", "", "D02"), row) == null));
            Expect("iv opp rd wins", IaVoucherAcct.CompareKeys(byRd, byInv) > 0);
            Expect("iv opp class", All(IaVoucherAcct.CompareKeys(byRdClass, byRd) > 0, IaVoucherAcct.CompareKeys(byRdLong, byRdClass) > 0));
            Expect("iv opp same", IaVoucherAcct.CompareKeys(byRd, IaVoucherAcct.OppKeys(Opp("103", "", "", ""), row)) == 0);
        }

        // 摘要（备注优先，没有备注时单据类型名，调用方给了就用它）；分录借方在前再按科目；合计为 0、借贷不平拒绝。
        static void CheckLines()
        {
            IaRow row = Row("09", "", "", "");
            Expect("iv digest name", IaVoucherPlan.Digest("", row) == "其他出库单");
            row.Memo = " 文本A ";
            Expect("iv digest memo", All(IaVoucherPlan.Digest("", row) == "文本A", IaVoucherPlan.Digest("DEMO-001", row) == "DEMO-001"));
            List<IaLine> lines = new List<IaLine>();
            lines.Add(Line("6401", 0, 10));
            lines.Add(Line("1405", 10, 0));
            lines.Add(Line("1403", -5, 0));
            lines.Add(Line("5001", 0, -5));
            lines.Sort(IaVoucherLines.CompareLines);
            Expect("iv order", All(lines[0].Line.Account == "1403", lines[1].Line.Account == "1405", lines[2].Line.Account == "5001"));
            IaVoucherLines.Check(lines);
            Expect("iv max text", All(IaText.MaxCode("CG0002", "cg0010") == "cg0010", IaText.MaxCode("", "01") == "01"));
            Refused("iv one line", new List<IaLine>(new IaLine[] { Line("1405", 10, 0) }));
            Refused("iv unbalanced", new List<IaLine>(new IaLine[] { Line("1405", 10, 0), Line("6401", 0, 9) }));
        }

        // 单据闸门：已制单、差异、未算成本（调拨等成对业务不在单张单据上拒绝）。
        static void CheckBill()
        {
            IaBill bill = new IaBill();
            bill.Type = IaType.Find("09");
            bill.Id = 1;
            bill.Code = "DEMO-001";
            bill.Rows.Add(Row("09", "", "", ""));
            Expect("iv bill ok", IaVoucherLoad.Problem(bill) == null);
            bill.Rows[0].Business = "调拨出库";
            bill.Rows[0].Pair = "调拨";
            Expect("iv bill pair", IaVoucherLoad.Problem(bill) == null);
            bill.Rows[0].Pair = "";
            bill.Rows[0].Business = "其他出库";
            bill.Rows[0].NoCost = true;
            Expect("iv bill cost", IaVoucherLoad.Problem(bill) != null);
            bill.Rows[0].NoCost = false;
            IaRow done = Row("09", "", "", "");
            done.Pz = "2026IA0000000000001";
            bill.Rows.Add(done);
            Expect("iv bill partly done", All(IaVoucherLoad.Problem(bill) == null, bill.Rows.Count == 1));
            bill.Rows[0].Pz = "2026IA0000000000001";
            Expect("iv bill done", All(IaVoucherLoad.Problem(bill) != null, bill.Rows.Count == 0));
            Expect("iv types", All(!IaType.Find("32").En, IaType.Find("3201").En, IaType.Find("01").MarkHead,
                !IaType.Find("24").MarkHead, !IaType.Find("21").In, IaType.Find("20").Table == "just"));
            Expect("iv sql tables", All(IaVoucherLoad.Sql(IaType.Find("3201")).IndexOf("RdRecords32", StringComparison.Ordinal) > 0,
                IaVoucherLoad.Sql(IaType.Find("21")).IndexOf("JustInVouchs", StringComparison.Ordinal) > 0));
        }

        static void Refused(string name, List<IaLine> lines)
        {
            try
            {
                IaVoucherLines.Check(lines);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static IaLine Line(string account, decimal debit, decimal credit)
        {
            IaLine line = new IaLine();
            line.Line = new GlLine();
            line.Line.Account = account;
            line.Line.Debit = debit;
            line.Line.Credit = credit;
            line.Line.Dept = "";
            line.Line.Person = "";
            line.Line.Customer = "";
            line.Line.Supplier = "";
            line.Line.ItemClass = "";
            line.Line.Item = "";
            return line;
        }

        static int[] Rank(Dictionary<string, object> head, IaRow row)
        {
            int[] rank = IaVoucherAcct.HeadRank(head, row);
            Expect("iv head rank", rank != null);
            return rank;
        }

        static IaRow Row(string type, string inv, string invClass, string wh)
        {
            IaRow row = new IaRow();
            row.Type = IaType.Find(type);
            row.Inv = inv;
            row.InvClass = invClass;
            row.Wh = wh;
            return row;
        }

        static Dictionary<string, object> Head(string wh, string invc, string inv)
        {
            Dictionary<string, object> h = new Dictionary<string, object>();
            h["wh"] = wh;
            h["invc"] = invc;
            h["inv"] = inv;
            return h;
        }

        static Dictionary<string, object> Opp(string rd, string inv, string invc, string dep)
        {
            Dictionary<string, object> o = new Dictionary<string, object>();
            o["rd"] = rd;
            o["inv"] = inv;
            o["invc"] = invc;
            o["dep"] = dep;
            o["obj"] = "";
            o["itm"] = "";
            o["itc"] = "";
            return o;
        }

        static string Ids(int count)
        {
            StringBuilder many = new StringBuilder();
            for (int i = 1; i <= count; i++)
            {
                many.Append(i == 1 ? "" : ",").Append("{\"type\":\"09\",\"id\":").Append(i).Append("}");
            }
            return many.ToString();
        }

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                IaVoucherReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, All(ex.Status == 400, ex.Code == "bad_request", ex.Field == field));
                return;
            }
            throw new InvalidOperationException(name);
        }

        // 多个条件：逗号分开，不在调用处用 &&（圈复杂度）。
        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
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
