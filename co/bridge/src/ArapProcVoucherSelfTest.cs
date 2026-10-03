using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的处理制单部分（arap/process/voucher）：请求校验（批次号前缀 → 处理类型、与 flag 一致、同一类、pl_code 只给 9M）、
    // 锁键、取数 SQL 的参数个数、批次闸门，以及分录的拼法（方向随明细、红字照记负数不换方向、iFlag=6 不出分录、9M 配汇兑损益行（借方，贷 - 借）、合并、往来借方在前、
    // coutid、回写分录号）。不连库、不建 COM。
    internal static class ArapProcVoucherSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckBatch();
            CheckTransfer();
            CheckMergeBz();
            CheckGain();
            CheckRed();
            // 票据处理制单（PJ，ArapProcVoucherNotes）。
            ArapProcVoucherNotesSelfTest.Run();
        }

        static void CheckParse()
        {
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"YCFAP000000000001\",\"YCFAP000000900002\"],"
                + "\"voucher_date\":\"2026-08-31\",\"digest\":\" 应收冲应付 \"}"));
            Expect("pv 9I", ask.Style == "9I" && ask.CancelNos.Count == 2 && ask.Date == "2026-08-31");
            Expect("pv 9I opt", ask.Digest == "应收冲应付" && ask.Sign == "" && ask.PlCode == "" && !ask.ExchangeGain);
            Expect("pv out sign", Join(ArapProcVoucherReq.OutSign, "9I", "9J", "BZ", "9M") == "ZZ,ZZ,BZ,SY");
            Expect("pv styles", Join(ArapProcVoucherReq.StyleOf, "FCYAR0000000005", "SYPAP0000000001", "HXAR0000000001", "YCFAP", "ycfap1")
                == "9J,9M,,,");
            Expect("pv flag of", ArapProcVoucherReq.FlagOf("BZAP2") == "AP");
            ProcVoucherAsk gain = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"SYRAR0000900003\"],\"pl_code\":\"660399\",\"sign\":\"转\"}"));
            Expect("pv 9M", gain.ExchangeGain && gain.PlCode == "660399" && gain.Sign == "转");
            string keys = string.Join("|", ArapProcVoucherReq.LockKeys(Body("{\"flag\":\"AP\",\"cancel_nos\":[\"BZAP1\",\"BZAP2\"]}")));
            Expect("pv keys", keys == "arap:proc:BZAP1|arap:proc:BZAP2|new:gl:转|arap:voucher:AP");
            Expect("pv bad keys", ArapProcVoucherReq.LockKeys(Body("{\"flag\":\"AP\",\"cancel_nos\":[]}")).Length == 0);
            Expect("pv spec", ArapProcVoucherReq.Spec[0] == ArapProcVoucherReq.Path && Array.IndexOf(ArapProcVoucherReq.Spec, "pl_code") > 0);
            Expect("pv spec date", Array.IndexOf(ArapProcVoucherReq.Spec, "date") < 0);
            string sql = ArapProcVoucherLoad.RowSql(3);
            Expect("pv row sql", Count(sql, '?') == 6 && sql.IndexOf("UPDLOCK", StringComparison.Ordinal) > 0);
        }

        static void CheckBad()
        {
            Bad("pv flag", "{\"flag\":\"GL\",\"cancel_nos\":[\"BZAR1\"]}", "flag");
            Bad("pv no list", "{\"flag\":\"AR\",\"cancel_nos\":\"BZAR1\"}", "cancel_nos");
            Bad("pv empty", "{\"flag\":\"AR\",\"cancel_nos\":[]}", "cancel_nos");
            Bad("pv prefix", "{\"flag\":\"AR\",\"cancel_nos\":[\"HXAR0000000001\"]}", "cancel_nos.0");
            Bad("pv not str", "{\"flag\":\"AR\",\"cancel_nos\":[1]}", "cancel_nos.0");
            Bad("pv dup", "{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\",\"BZAR1\"]}", "cancel_nos.1");
            Bad("pv mixed", "{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\",\"YCFAP1\"]}", "cancel_nos.1");
            Bad("pv side", "{\"flag\":\"AP\",\"cancel_nos\":[\"YCFAP1\"]}", "cancel_nos.0");
            Bad("pv pl missing", "{\"flag\":\"AR\",\"cancel_nos\":[\"SYRAR1\"]}", "pl_code");
            Bad("pv pl space", "{\"flag\":\"AR\",\"cancel_nos\":[\"SYRAR1\"],\"pl_code\":\"6604 04\"}", "pl_code");
            Bad("pv pl extra", "{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\"],\"pl_code\":\"660399\"}", "pl_code");
            Bad("pv date", "{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\"],\"voucher_date\":\"2026/08/31\"}", "voucher_date");
            StringBuilder many = new StringBuilder();
            for (int i = 1; i <= ArapProcVoucherReq.Max + 1; i++)
            {
                many.Append(i == 1 ? "" : ",").Append("\"BZAR").Append(i).Append('"');
            }
            Bad("pv too many", "{\"flag\":\"AR\",\"cancel_nos\":[" + many + "]}", "cancel_nos");
        }

        // 批次闸门：不存在 404；类型不符、已制单、应收冲应付只有一方、并账在另一张表也有行 409。
        static void CheckBatch()
        {
            ProcVoucherAsk ask = Ask("AR", "9I", "YCFAP1");
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            rows.Add(Credit(Row("AR", "1", "R0", "YS1", "112299", 0), 100m, 0));
            Status("pv one side", 409, delegate { ArapProcVoucherLoad.CheckBatch(ask, "YCFAP1", rows); });
            Status("pv missing", 404, delegate { ArapProcVoucherLoad.CheckBatch(ask, "YCFAP9", rows); });
            rows.Add(Row("AP", "2", "01", "P1", "220299", 100m));
            ArapProcVoucherLoad.CheckBatch(ask, "YCFAP1", rows);
            rows[1].Pz = "AR0000000004541";
            Status("pv vouchered", 409, delegate { ArapProcVoucherLoad.CheckBatch(ask, "YCFAP1", rows); });
            rows[1].Pz = "";
            rows[1].Style = "9P";
            Status("pv style", 409, delegate { ArapProcVoucherLoad.CheckBatch(ask, "YCFAP1", rows); });
            ProcVoucherAsk bz = Ask("AR", "BZ", "YCFAP1");
            rows[1].Style = "BZ";
            rows[0].Style = "BZ";
            Status("pv bz other", 409, delegate { ArapProcVoucherLoad.CheckBatch(bz, "YCFAP1", rows); });
        }

        // 应收冲应付：应收 R0 贷 100000；应付同一张采购发票三行借（合并成一行，coutid 是发票号）；
        // 49 的 iFlag=6 留底行没有科目，不出分录。凭证两行：借 220299（供应商）、贷 112299（客户）。
        static void CheckTransfer()
        {
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            rows.Add(Credit(Row("AR", "29141", "R0", "YS0000000001", "112299", 0), 100000m, 0));
            rows.Add(Row("AP", "4869", "01", "25932", "220299", 60000m));
            rows.Add(Row("AP", "4870", "01", "25932", "220299", 4000m));
            rows.Add(Row("AP", "4871", "01", "25932", "220299", 36000m));
            rows.Add(Credit(Row("AP", "4872", "49", "FK1", "", 0), 100000m, 6));
            ProcPlan plan = Plan("9I", "YCFAP000000000001", rows, "");
            Expect("pv 9I lines", plan.Gl.Draft.Lines.Count == 2);
            GlLine dr = plan.Gl.Draft.Lines[0];
            GlLine cr = plan.Gl.Draft.Lines[1];
            Expect("pv 9I debit", dr.Account == "220299" && dr.Debit == 100000m && dr.Supplier == "S1");
            Expect("pv 9I credit", cr.Account == "112299" && cr.Credit == 100000m && cr.Customer == "C1");
            Expect("pv 9I aux", dr.Customer == "" && cr.Supplier == "");
            Expect("pv 9I digest", dr.Digest == "应收冲应付" && plan.Gl.Operators[1] == "-");
            TransferSources(plan);
            NoCode(rows);
        }

        // 每行来源单据、明细行 → 分录号（三行采购发票明细都回写分录 1，留底行不回写）、附单据数 = 批次数。
        static void TransferSources(ProcPlan plan)
        {
            Expect("pv 9I sources", plan.Sources[0][0] == "01" && plan.Sources[0][1] == "25932" && plan.Sources[1][1] == "YS0000000001");
            Expect("pv 9I entries", plan.Gl.Entries["AP:4870"] == 1 && plan.Gl.Entries["AR:29141"] == 2);
            Expect("pv 9I left", !plan.Gl.Entries.ContainsKey("AP:4872") && plan.Gl.Draft.Attachments == 1);
        }

        // 不是 iFlag=6 留底行却没有科目：409。
        static void NoCode(List<ProcVoucherRow> rows)
        {
            rows.Add(Row("AP", "4873", "01", "25933", "", 1m));
            Status("pv no code", 409, delegate { ArapProcVoucherParts.Build(rows, "9I", "", ""); });
        }

        // 并账：同一张应收单从 C1 移到 C2，来源行借方 -100（红字照记借方负数）、
        // 目标行借方 +100，凭证合计 0；合并不跨往来单位。
        static void CheckMergeBz()
        {
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            rows.Add(Row("AR", "1", "R0", "YS0000000002", "112201", -100m));
            ProcVoucherRow target = Row("AR", "2", "R0", "YS0000000002", "112201", 100m);
            target.Dw = "C2";
            rows.Add(target);
            ProcPlan plan = Plan("BZ", "BZAR0000000001", rows, "并账 C1 → C2");
            GlLine from = plan.Gl.Draft.Lines[0];
            GlLine to = plan.Gl.Draft.Lines[1];
            Expect("pv bz", plan.Gl.Draft.Lines.Count == 2 && to.Customer == "C2" && to.Debit == 100m && to.Credit == 0);
            Expect("pv bz red", from.Customer == "C1" && from.Debit == -100m && from.Credit == 0 && from.Digest == "并账 C1 → C2");
            Status("pv unbalanced", 409, delegate
            {
                List<ProcLine> one = new List<ProcLine>();
                one.Add(Line(ArapProcVoucherParts.Build(rows, "BZ", "", "")[0]));
                one.Add(Line(ArapProcVoucherParts.Build(rows, "BZ", "", "")[0]));
                ArapProcVoucherParts.Order(one);
            });
        }

        // 汇兑损益：外币发票两行明细借方 -1.26 / -1.27 → 112202 借方合并 -2.53（红字照记）、660399 借方合并 +2.53
        // （汇兑损益科目总在借方、金额 = 贷 - 借）；往来行在前；coutid 是「单号 分录号」，汇兑损益科目行不回写明细。
        static void CheckGain()
        {
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            rows.Add(Row("AR", "17876", "27", "XS0000000001", "112202", -1.26m));
            rows.Add(Row("AR", "17877", "27", "XS0000000001", "112202", -1.27m));
            ProcPlan plan = Plan("9M", "SYRAR0000900004", rows, "");
            GlLine dw = plan.Gl.Draft.Lines[0];
            GlLine pl = plan.Gl.Draft.Lines[1];
            Expect("pv 9M lines", plan.Gl.Draft.Lines.Count == 2 && pl.Account == "660399" && pl.Debit == 2.53m && pl.Credit == 0);
            Expect("pv 9M dw", dw.Account == "112202" && dw.Debit == -2.53m && dw.Credit == 0);
            Expect("pv 9M aux", pl.Customer == "" && dw.Customer == "C1");
            Expect("pv 9M coutid", plan.Sources[0][1] == "XS0000000001 1" && plan.Sources[1][1] == "XS0000000001 2");
            Expect("pv 9M entries", plan.Gl.Entries.Count == 2 && plan.Gl.Entries["AR:17876"] == 1 && plan.Gl.Entries["AR:17877"] == 1);
            Expect("pv 9M digest", pl.Digest == "汇兑损益");
            CheckGainCredit();
        }

        // 收款单（48）贷方 4.20 的 9M 行：往来贷方 4.20，汇兑损益借方 4.20（贷 - 借）。
        static void CheckGainCredit()
        {
            List<ProcVoucherRow> gain = new List<ProcVoucherRow>();
            gain.Add(Credit(Row("AR", "1", "48", "SK1", "112202", 0), 4.20m, 0));
            List<ProcPart> parts = ArapProcVoucherParts.Build(gain, "9M", "660399", "");
            Expect("pv 9M credit row", parts.Count == 2 && !parts[0].Debit && parts[0].Amount == 4.20m && parts[1].Debit && parts[1].Amount == 4.20m);
        }

        // 红票对冲（测试账套实测）：蓝字发票行借方 -10、红字发票行借方 +10，都记借方带符号，
        // 合计 0；摘要缺省「红票对冲」；coutsign 是制单系统本身。全为 0 时拒绝。
        static void CheckRed()
        {
            Expect("pv 9N style", ArapProcVoucherReq.StyleOf("HRAR0000000000001") == "9N" && ArapProcVoucherReq.FlagOf("HPAP1") == "AP");
            Expect("pv 9N out sign", ArapProcVoucherReq.OutSign("9N", "AR") == "AR" && ArapProcVoucherReq.OutSign("9N", "AP") == "AP"
                && ArapProcVoucherReq.OutSign("BZ", "AP") == "BZ");
            Bad("pv 9N side", "{\"flag\":\"AP\",\"cancel_nos\":[\"HRAR1\"]}", "cancel_nos.0");
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            ProcVoucherRow blue = Row("AR", "31", "26", "SI2", "112201", -10m);
            blue.Digest = "销售发票";
            rows.Add(blue);
            rows.Add(Row("AR", "32", "26", "SI3", "112201", 10m));
            ProcPlan plan = Plan("9N", "HRAR0000000000001", rows, "");
            GlLine b = plan.Gl.Draft.Lines[0];
            GlLine r = plan.Gl.Draft.Lines[1];
            Expect("pv 9N lines", plan.Gl.Draft.Lines.Count == 2 && b.Debit == -10m && r.Debit == 10m && b.Credit == 0 && r.Credit == 0);
            Expect("pv 9N digest", b.Digest == "红票对冲" && plan.Sources[0][1] == "SI2" && plan.Sources[1][0] == "26");
            Status("pv zero", 409, delegate { ArapProcVoucherParts.Order(new List<ProcLine>()); });
        }

        // 不连库的计划：拼分录 → 按表填往来单位（应收客户、应付供应商，代替 ArapProcVoucherLines 的查科目）→ 合并 → 排序 → 写进计划。
        static ProcPlan Plan(string style, string no, List<ProcVoucherRow> rows, string digest)
        {
            ProcPlan plan = new ProcPlan();
            plan.Ask = Ask(style == "9J" ? "AP" : "AR", style, no);
            plan.Rows = rows;
            plan.Gl.Key.Sign = "转";
            plan.Gl.Date = "2026-08-31";
            List<ProcLine> lines = new List<ProcLine>();
            foreach (ProcPart part in ArapProcVoucherParts.Build(rows, style, "660399", digest))
            {
                lines.Add(Line(part));
            }
            lines = ArapProcVoucherParts.Order(ArapProcVoucherParts.Merge(lines));
            ArapProcVoucherParts.Fill(plan, lines);
            return plan;
        }

        static ProcLine Line(ProcPart part)
        {
            GlLine gl = new GlLine();
            gl.Account = part.Account;
            gl.Digest = part.Digest;
            gl.Debit = part.Debit ? part.Amount : 0;
            gl.Credit = part.Debit ? 0 : part.Amount;
            bool dw = !part.Pl;
            gl.Customer = dw && part.Row.Ledger == "AR" ? part.Row.Dw : "";
            gl.Supplier = dw && part.Row.Ledger == "AP" ? part.Row.Dw : "";
            gl.Dept = "";
            gl.Person = "";
            gl.ItemClass = "";
            gl.Item = "";
            ProcLine line = new ProcLine();
            line.Line = gl;
            line.VType = part.Row.VType;
            line.VId = part.Row.VId;
            line.Pl = part.Pl;
            line.Op = dw ? "-" : "";
            if (dw)
            {
                line.Rows.Add(part.Row.Key);
            }
            return line;
        }

        // 借方 dm；贷方行用 Credit 改。
        static ProcVoucherRow Row(string ledger, string aid, string vtype, string vid, string code, decimal dm)
        {
            ProcVoucherRow row = new ProcVoucherRow();
            row.Ledger = ledger;
            row.Aid = aid;
            row.VType = vtype;
            row.VId = vid;
            row.Code = code;
            row.Dm = dm;
            row.Dw = ledger == "AR" ? "C1" : "S1";
            row.Style = "9I";
            row.CancelNo = "YCFAP1";
            return row;
        }

        static ProcVoucherRow Credit(ProcVoucherRow row, decimal cm, int iflag)
        {
            row.Cm = cm;
            row.IFlag = iflag;
            return row;
        }

        static ProcVoucherAsk Ask(string flag, string style, string no)
        {
            ProcVoucherAsk ask = new ProcVoucherAsk();
            ask.Flag = flag;
            ask.Style = style;
            ask.CancelNos.Add(no);
            ask.Sign = "";
            ask.Date = "";
            ask.Digest = "";
            ask.PlCode = style == "9M" ? "660399" : "";
            return ask;
        }

        delegate void Act();

        static void Status(string name, int status, Act act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Status.ToString(CultureInfo.InvariantCulture) + ")", ex.Status == status);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                ArapProcVoucherReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        delegate string Map(string value);

        static string Join(Map map, params string[] values)
        {
            List<string> list = new List<string>();
            foreach (string value in values)
            {
                list.Add(map(value) ?? "");
            }
            return string.Join(",", list.ToArray());
        }

        static int Count(string text, char c)
        {
            int n = 0;
            foreach (char one in text)
            {
                n += one == c ? 1 : 0;
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
