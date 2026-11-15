using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的应收 / 应付制单部分（合并制单）：请求校验（id / ids 互斥、ids 的形状和个数、类型一致、不重复）、锁键，
    // 以及分录的排列与回写对应（只在本单内合并、借方在前、每行记来源单据、票据登记行跟本单同科目的结算行）。不连库、不建 COM。
    internal static class ArapVoucherSelfTest
    {
        const string Head = "{\"flag\":\"AR\",\"type\":\"ar_receipt\"";

        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckLines();
        }

        static void CheckParse()
        {
            VoucherAsk one = ArapVoucherReq.Parse(Body(",\"id\":5}"));
            Expect("av single", !one.Merge && one.Id == 5 && one.Ids.Count == 1 && one.Ids[0] == 5);
            Dictionary<string, object> body = Body(",\"ids\":[{\"type\":\"ar_receipt\",\"id\":7},{\"type\":\"ar_receipt\",\"id\":3}]}");
            VoucherAsk two = ArapVoucherReq.Parse(body);
            Expect("av merge", two.Merge && two.Id == 7 && two.Ids.Count == 2 && two.Ids[1] == 3);
            Expect("av merging", ArapVoucherReq.Merging(body) && !ArapVoucherReq.Merging(Body(",\"id\":5}"))
                && !ArapVoucherReq.Merging(Body(",\"id\":5,\"ids\":null}")));
            CheckKeys(body);
        }

        static void CheckKeys(Dictionary<string, object> body)
        {
            string keys = string.Join("|", ArapVoucherReq.LockKeys(body, false));
            Expect("av merge keys", keys.StartsWith("ar_receipt:7|ar_receipt:3|new:gl:", StringComparison.Ordinal)
                && keys.EndsWith("|arap:voucher:AR", StringComparison.Ordinal));
            Expect("av fields", Array.IndexOf(ArapVoucherReq.Fields(false), "ids") >= 0
                && Array.IndexOf(ArapVoucherReq.Fields(true), "ids") < 0);
        }

        static void CheckBad()
        {
            string pair = "{\"type\":\"ar_receipt\",\"id\":1},{\"type\":\"ar_receipt\",\"id\":2}";
            Bad("av both", ",\"id\":1,\"ids\":[" + pair + "]}", "ids");
            Bad("av one", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":1}]}", "ids");
            Bad("av not list", ",\"ids\":{\"type\":\"ar_receipt\",\"id\":1}}", "ids");
            Bad("av bare id", ",\"ids\":[1,2]}", "ids.0");
            Bad("av extra key", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":1,\"x\":1},{\"type\":\"ar_receipt\",\"id\":2}]}", "ids.0");
            Bad("av mixed", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":1},{\"type\":\"ar_bill\",\"id\":2}]}", "ids.1.type");
            Bad("av dup", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":1},{\"type\":\"ar_receipt\",\"id\":1}]}", "ids.1");
            Bad("av bad id", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":0},{\"type\":\"ar_receipt\",\"id\":2}]}", "ids.0.id");
            Bad("av str id", ",\"ids\":[{\"type\":\"ar_receipt\",\"id\":\"1\"},{\"type\":\"ar_receipt\",\"id\":2}]}", "ids.0.id");
            StringBuilder many = new StringBuilder();
            for (int i = 1; i <= ArapVoucherReq.MergeMax + 1; i++)
            {
                many.Append(i == 1 ? "" : ",").Append("{\"type\":\"ar_receipt\",\"id\":").Append(i).Append("}");
            }
            Bad("av too many", ",\"ids\":[" + many + "]}", "ids");
            Expect("av bad keys", ArapVoucherReq.LockKeys(Body(",\"ids\":[1,2]}"), false).Length == 0);
        }

        // 两张收款单：各自 银行（借）+ 应收（贷），同客户同科目。合并后 4 行（不跨单据合并），借方在前，来源单据 0,1,0,1。
        static void CheckLines()
        {
            List<VoucherRow> rows = new List<VoucherRow>();
            rows.AddRange(ArapVoucherPlan.Merge(Bill(0, "1", 100m)));
            rows.AddRange(ArapVoucherPlan.Merge(Bill(1, "3", 50m)));
            rows = ArapVoucherPlan.Order(rows);
            VoucherPlan plan = new VoucherPlan();
            plan.Docs.Add(new VoucherDoc());
            plan.Docs.Add(new VoucherDoc());
            ArapVoucherPlan.Fill(plan, rows);
            Expect("av bills", string.Join(",", plan.Bills.ConvertAll<string>(Text).ToArray()) == "0,1,0,1");
            Expect("av order", plan.Draft.Lines[0].Debit == 100m && plan.Draft.Lines[1].Debit == 50m
                && plan.Draft.Lines[2].Credit == 100m && plan.Draft.Lines[3].Credit == 50m);
            Expect("av attachments", plan.Draft.Attachments == 2);
            Expect("av entries", plan.EntryOf(0, Row("2", "0", "1122")) == 3 && plan.EntryOf(1, Row("4", "0", "1122")) == 4);
            // 票据登记行（iFlag 3）跟本单同科目的第一行：第二张单据的登记行回写分录 2，不是第一张的分录 1。
            Expect("av note row", plan.EntryOf(1, Row("9", "3", "1002")) == 2 && plan.EntryOf(0, Row("8", "3", "1002")) == 1);
            Expect("av cash row", plan.EntryOf(0, Row("7", "6", "1001")) == 0);
            List<VoucherRow> same = new List<VoucherRow>();
            same.AddRange(Bill(0, "1", 100m));
            same.AddRange(Bill(0, "5", 20m));
            same = ArapVoucherPlan.Merge(same);
            Expect("av merge one bill", same.Count == 2 && same[0].Line.Debit == 120m && same[1].Aids.Count == 2);
        }

        // 一张收款单的两行：结算行（非现金的银行科目、回写）和往来行（回写）；aid 是结算行的 Auto_ID，往来行 +1。
        static List<VoucherRow> Bill(int bill, string aid, decimal amount)
        {
            List<VoucherRow> rows = new List<VoucherRow>();
            rows.Add(Line(bill, aid, "1002", amount, true));
            rows.Add(Line(bill, (int.Parse(aid) + 1).ToString(), "1122", amount, false));
            return rows;
        }

        static VoucherRow Line(int bill, string aid, string account, decimal amount, bool debit)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Debit = debit ? amount : 0;
            line.Credit = debit ? 0 : amount;
            line.Dept = "";
            line.Person = "";
            line.Customer = debit ? "" : "C001";
            line.Supplier = "";
            line.ItemClass = "";
            line.Item = "";
            line.Settle = "";
            line.DocNo = "";
            line.DocDate = "";
            VoucherRow row = new VoucherRow();
            row.Line = line;
            row.Bill = bill;
            row.Marked = true;
            row.Aids.Add(aid);
            return row;
        }

        static Dictionary<string, object> Row(string aid, string flag, string account)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["aid"] = aid;
            row["iflag"] = flag;
            row["cCode"] = account;
            return row;
        }

        static Dictionary<string, object> Body(string tail)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(Head + tail));
        }

        static void Bad(string name, string tail, string field)
        {
            try
            {
                ArapVoucherReq.Parse(Body(tail));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static string Text(int value)
        {
            return value.ToString(System.Globalization.CultureInfo.InvariantCulture);
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
