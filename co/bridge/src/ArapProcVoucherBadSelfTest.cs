using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // --selftest 的坏账处理部分（取消 arap/process/cancel 的 HZAR、制单 arap/process/voucher 的 9F / 9G / 9H、
    // arap/process/list 的 9F 摘要项）：请求校验、锁键、处理方式识别、脚本拒绝编号、分录拼法和顺序。只跑纯函数，不连库、不建 COM。
    // 样例按 U8 坏账处理凭证的形状构造（科目 1231 坏账准备、6702 信用减值损失、1122 应收、100201 银行）。
    internal static class ArapProcBadSelfTest
    {
        const string No = "HZAR0000000000001";

        public static void Run()
        {
            CheckCancelParse();
            CheckDetect();
            CheckRefused();
            CheckItems();
            CheckReceipt();
            CheckVoucherParse();
            CheckParts();
            CheckOrder();
            CheckPara();
            CheckReceiptRows();
            CheckList();
        }

        static void CheckCancelParse()
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(CancelBody("AR", No));
            Expect("bad cancel parse", ask.Kind.Bad && ask.Kind.Style == "HZ" && ask.Kind.Ledgers.Length == 1 && !ask.Kind.Notes);
            Status("bad cancel flag", 400, delegate { ArapProcCancelReq.Parse(CancelBody("AP", No)); });
            Status("bad cancel tail", 400, delegate { ArapProcCancelReq.Parse(CancelBody("AR", "HZAR12x")); });
            string[] keys = ArapProcCancelReq.LockKeys(CancelBody("AR", No));
            Expect("bad cancel keys", string.Join(",", keys) == "arap:writeoff:AR,arap:proc:" + No + ",arap:bad:AR");
            Expect("bad cancel other kinds", !ArapProcCancelReq.Parse(CancelBody("AR", "HRAR000000000001")).Kind.Bad);
        }

        static void CheckDetect()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            Expect("bad style none", ArapProcCancelBadSql.StyleOf(rows) == null);
            rows.Add(StyleRow("9G"));
            rows.Add(StyleRow("9G"));
            Expect("bad style 9G", ArapProcCancelBadSql.StyleOf(rows) == "9G");
            rows.Add(StyleRow("9H"));
            Status("bad style mixed", 409, delegate { ArapProcCancelBadSql.StyleOf(rows); });
            Expect("bad not found", ArapProcCancelBadSql.NotFound(No).Status == 404);
        }

        static void CheckRefused()
        {
            Expect("bad refused 404", ArapProcCancelBadSql.Refused(new ScriptRefusal(50146, "x", null), No).Status == 404);
            BridgeException verify = ArapProcCancelBadSql.Refused(new ScriptRefusal(50159, "x", null), No);
            Expect("bad refused verify", verify.Status == 409 && verify.Code == "u8_rejected");
            BridgeException pz = ArapProcCancelBadSql.Refused(new ScriptRefusal(50144, "cPZid set", null), No);
            Expect("bad refused pz", pz.Status == 409 && pz.Message == ArapProcCancelGate.Vouchered);
            BridgeException closed = ArapProcCancelBadSql.Refused(new ScriptRefusal(50141, "AR period closed: 2025-3", null), No);
            Expect("bad refused closed", closed.Message == "应收该期间已结账，不能取消坏账处理：2025-3");
            Expect("bad refused unknown", ArapProcCancelBadSql.Refused(new ScriptRefusal(50199, "x", null), No).Status == 500);
            Expect("bad refused args", ArapProcCancelBadSql.Refused(new ScriptRefusal(50140, "x", null), No).Status == 500);
            BridgeException foreign = ArapProcCancelBadSql.Refused(new ScriptRefusal(50155, "x: 0000000056", null), No);
            Expect("bad refused not ours", foreign.Status == 409
                && foreign.Message == "该坏账收回不是本接口生成的（收款单没有审核行），请在 U8 客户端取消");
            Expect("bad refused para rows", ArapProcCancelBadSql.Refused(new ScriptRefusal(50156, "x: 2025", null), No).Status == 409);
            Expect("bad refused receipt dup", ArapProcCancelBadSql.Refused(new ScriptRefusal(50157, "x: 0000000056", null), No).Status == 409);
            Expect("bad refused period", ArapProcCancelBadSql.Refused(new ScriptRefusal(50154, "x: 2025-03-31", null), No).Status == 409);
            CheckCancelScript();
        }

        // 取消脚本的 THROW 编号都在 50140–50159 且是拒绝编号；50146、50159、50140 以外都有中文；年度、期间按 UA_Period 取，不用日历年月。
        static void CheckCancelScript()
        {
            string text = SqlScript.Text(ArapProcCancelBadSql.Script);
            Regex thrown = new Regex("THROW (5[0-9]{4})", RegexOptions.CultureInvariant);
            foreach (Match m in thrown.Matches(text))
            {
                int number = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
                Expect("bad cancel range " + m.Groups[1].Value, SqlScript.IsRefusal(number) && number >= 50140 && number <= 50159);
                if (number != 50140)
                {
                    Expect("bad cancel text " + m.Groups[1].Value, ArapProcCancelBadSql.Refused(new ScriptRefusal(number, "x", null), No).Status != 500);
                }
            }
            Expect("bad cancel period", text.Contains("UFSYSTEM..UA_Period") && !text.Contains("YEAR(dRegDate)") && !text.Contains("MONTH(@d)"));
            Expect("bad cancel 9F keep row", !text.Contains("DELETE Ar_BadPara") && text.Contains("AND cProcStyle = N'9F' AND dJtDate IS NOT NULL;"));
        }

        // 取消坏账收回：收款单复原的判断；收款单没有审核行的处理脚本直接拒绝（50155），不再用 SQL 清审核人。
        static void CheckReceipt()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["auditor"] = "";
            row["vdate"] = "";
            row["open_lines"] = "0";
            row["details"] = "0";
            Expect("bad receipt restored", ArapProcCancelBadSql.ReceiptRefusal(row) == null);
            Expect("bad receipt missing", ArapProcCancelBadSql.ReceiptRefusal(null) != null);
            row["details"] = "1";
            Expect("bad receipt details", ArapProcCancelBadSql.ReceiptRefusal(row) == "收款单上还有往来明细");
            row["auditor"] = "张三";
            Expect("bad receipt audited", ArapProcCancelBadSql.ReceiptRefusal(row) == "收款单仍是已审核状态");
            ScriptResult r = new ScriptResult();
            Expect("bad receipt id none", ArapProcCancelBadSql.ReceiptId(r) == 0);
            string text = SqlScript.Text(ArapProcCancelBadSql.Script);
            Expect("bad cancel script signed", text.Contains("THROW 50155") && text.Contains("(N'signed', CONVERT(nvarchar(40), @signed))")
                && text.Contains("ISNULL(cCancelNo, N'') = @own") && !text.Contains("SET cCheckMan = NULL"));
        }

        static void CheckItems()
        {
            ScriptResult r = new ScriptResult();
            r.Rows = new List<Dictionary<string, object>>();
            r.Rows.Add(ItemRow("26 SI0001 4869 1001 C001 300.00"));
            r.Rows.Add(ItemRow("48 SK0001 0 2001 C001 500.00"));
            List<object> items = ArapProcCancelBad.Items(r);
            Dictionary<string, object> inv = (Dictionary<string, object>)items[0];
            Dictionary<string, object> rec = (Dictionary<string, object>)items[1];
            Expect("bad items invoice", (string)inv["type"] == "sale_invoice" && (int)inv["id"] == 1001 && (int)inv["line_id"] == 4869
                && (decimal)inv["credit"] == 300m && (decimal)inv["debit"] == 0m);
            Expect("bad items receipt", (string)rec["type"] == "ar_receipt" && rec["line_id"] == null && (decimal)rec["debit"] == 500m
                && (decimal)rec["credit"] == 0m);
        }

        static void CheckVoucherParse()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = "AR";
            body["cancel_nos"] = new List<object> { No, "HZAR0000000000014" };
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(body);
            Expect("bad pv parse", ask.Style == "HZ" && ask.Bad && !ask.ExchangeGain && !ask.Notes && ArapProcVoucherReq.SignOf(ask) == "转");
            Expect("bad pv out sign", ArapProcVoucherReq.OutSign("9G", "AR") == "JT" && ArapProcVoucherReq.OutSign("HZ") == "JT");
            Expect("bad pv titles", ArapProcVoucherReq.Title("9F") == "计提坏账" && ArapProcVoucherReq.Title("9H") == "坏账收回");
            Expect("bad pv pick", ArapProcVoucherBad.Pick(null, "9G", No) == "9G" && ArapProcVoucherBad.Pick("9G", "9G", No) == "9G");
            Status("bad pv pick mixed", 409, delegate { ArapProcVoucherBad.Pick("9F", "9G", No); });
            Expect("bad pv keys", string.Join(",", ArapProcVoucherReq.LockKeys(body))
                == "arap:proc:" + No + ",arap:proc:HZAR0000000000014,new:gl:转,arap:voucher:AR,arap:bad:AR");
            body["flag"] = "AP";
            Status("bad pv flag", 400, delegate { ArapProcVoucherReq.Parse(body); });
            body["flag"] = "AR";
            body["cancel_nos"] = new List<object> { No, "SYRAR0000000001" };
            Status("bad pv mixed", 400, delegate { ArapProcVoucherReq.Parse(body); });
        }

        // 9F 借 6702 贷 1231（都不回写明细）；9G 借 1231（不回写）贷 1122（回写）；9H 借银行（回写）贷 1231（不回写）。
        static void CheckParts()
        {
            List<ProcPart> parts = Parts(Row("9F", "", 1500m, 0m), "9F", "");
            Expect("bad parts 9F", parts.Count == 2 && Is(parts[0], "6702", true, true) && Is(parts[1], "1231", false, true)
                && parts[1].Amount == 1500m);
            parts = Parts(Row("9G", "1122", 0m, 300m), "9G", "");
            Expect("bad parts 9G", parts.Count == 2 && Is(parts[0], "1231", true, true) && Is(parts[1], "1122", false, false)
                && parts[1].Amount == 300m);
            parts = Parts(Row("9H", "100201", 500m, 0m), "9H", "100201");
            Expect("bad parts 9H", parts.Count == 2 && Is(parts[0], "100201", true, false) && Is(parts[1], "1231", false, true));
            Expect("bad parts zero", Parts(Row("9G", "1122", 0m, 0m), "9G", "").Count == 0);
            Status("bad parts no code", 409, delegate { Parts(Row("9G", "", 0m, 300m), "9G", ""); });
        }

        static List<ProcPart> Parts(ProcVoucherRow row, string style, string bank)
        {
            List<ProcPart> parts = new List<ProcPart>();
            ArapProcVoucherBad.RowParts(parts, row, style, new string[] { "1231", "6702", bank }, ArapProcVoucherReq.Title(style));
            return parts;
        }

        static bool Is(ProcPart part, string account, bool debit, bool pl)
        {
            return part.Account == account && part.Debit == debit && part.Pl == pl;
        }

        static void CheckOrder()
        {
            List<ProcLine> lines = new List<ProcLine>();
            lines.Add(Line("1122", 0m, 300m));
            lines.Add(Line("1231", 300m, 0m));
            lines.Add(Line("1122", 0m, 200m));
            lines.Add(Line("1231", 200m, 0m));
            List<ProcLine> ordered = ArapProcVoucherBad.Order(lines);
            Expect("bad order", ordered[0].Line.Account == "1231" && ordered[1].Line.Debit == 200m && ordered[2].Line.Account == "1122"
                && ordered[3].Line.Credit == 200m);
            List<ProcLine> off = new List<ProcLine>();
            off.Add(Line("1231", 300m, 0m));
            off.Add(Line("1122", 0m, 200m));
            Status("bad order unbalanced", 409, delegate { ArapProcVoucherBad.Order(off); });
        }

        static void CheckPara()
        {
            Dictionary<string, object> r = new Dictionary<string, object>();
            r["cno"] = No;
            r["pz"] = "";
            r["jt"] = "1500.00";
            r["rd"] = "2025-03-31";
            r["ry"] = "2025";
            r["per"] = "3";
            r["y"] = "2025";
            ProcVoucherRow row = ArapProcVoucherBad.ParaRow(r);
            Expect("bad para row", row.Style == "9F" && row.VType == "9F" && row.VId == No && row.Aid.Length == 0 && row.Period == 3
                && row.Dm == 1500m && row.ParaYear == 2025);
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            rows.Add(row);
            ArapProcVoucherBad.CheckPara(rows);
            row.Dm = 0m;
            Status("bad para zero", 409, delegate { ArapProcVoucherBad.CheckPara(rows); });
            row.Dm = 1m;
            row.ParaYear = 2026;
            Status("bad para year", 409, delegate { ArapProcVoucherBad.CheckPara(rows); });
        }

        // 9H 制单另回写的收款单审核行数 = 计划单据行数 − 批次里有往来明细的行数；其余处理为 0。取消制单只放行同凭证上有 9H 行的收款单审核行。
        static void CheckReceiptRows()
        {
            ProcPlan plan = new ProcPlan();
            plan.Ask = new ProcVoucherAsk();
            plan.Ask.Style = "9H";
            plan.Rows.Add(Row("9H", "1122", 500m, 0m));
            plan.Gl.Doc = new VoucherDoc();
            plan.Gl.Docs.Add(plan.Gl.Doc);
            Dictionary<string, object> own = new Dictionary<string, object>();
            own["aid"] = "101";
            plan.Gl.Doc.Rows.Add(own);
            Expect("bad receipt rows none", ArapProcVoucherBad.ReceiptRows(plan) == 0);
            Dictionary<string, object> audit = new Dictionary<string, object>();
            audit["aid"] = "100";
            plan.Gl.Doc.Rows.Add(audit);
            Expect("bad receipt rows one", ArapProcVoucherBad.ReceiptRows(plan) == 1);
            plan.Ask.Style = "9G";
            Expect("bad receipt rows 9G", ArapProcVoucherBad.ReceiptRows(plan) == 0);
            Expect("bad receipt drop cond", ArapProcVoucherBad.ReceiptRowCond.Contains("h.cProcStyle=N'9H'")
                && ArapProcVoucherBad.ReceiptRowCond.Contains("h.cVouchID=x.cVouchID"));
        }

        static void CheckList()
        {
            List<int[]> periods = new List<int[]>();
            periods.Add(new int[] { 2025, 3 });
            Dictionary<string, object> r = new Dictionary<string, object>();
            r["code"] = No;
            r["pz"] = "";
            r["jt"] = "1500.00";
            r["y"] = "2025";
            r["p"] = "3";
            Dictionary<string, object> item = ArapProcListBad.Item(r, periods);
            Expect("bad list item", item != null && (string)item["style"] == "9F" && item["pz"] == null
                && (string)item["sum_d_f"] == "1500.00" && (int)item["fiscal_year"] == 2025 && (int)item["rows"] == 0);
            r["p"] = "4";
            Expect("bad list other period", ArapProcListBad.Item(r, periods) == null);
        }

        static ProcVoucherRow Row(string style, string code, decimal dm, decimal cm)
        {
            ProcVoucherRow row = new ProcVoucherRow();
            row.Ledger = "AR";
            row.Aid = style == "9F" ? "" : "101";
            row.Style = style;
            row.CancelNo = No;
            row.VType = style == "9F" ? "9F" : (style == "9H" ? "48" : "26");
            row.VId = style == "9F" ? No : "SI0001";
            row.Code = code;
            row.Dw = "C001";
            row.Dm = dm;
            row.Cm = cm;
            return row;
        }

        static ProcLine Line(string account, decimal debit, decimal credit)
        {
            ProcLine line = new ProcLine();
            line.Line = new GlLine();
            line.Line.Account = account;
            line.Line.Debit = debit;
            line.Line.Credit = credit;
            return line;
        }

        static Dictionary<string, object> StyleRow(string style)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["style"] = style;
            return row;
        }

        // spec：vtype vid line_id doc_id partner amount_f，空格分隔。
        static Dictionary<string, object> ItemRow(string spec)
        {
            string[] p = spec.Split(' ');
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["vtype"] = p[0];
            row["vid"] = p[1];
            row["line_id"] = p[2];
            row["doc_id"] = p[3];
            row["partner"] = p[4];
            row["amount_f"] = p[5];
            row["amount"] = p[5];
            return row;
        }

        static Dictionary<string, object> CancelBody(string flag, string no)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            body["cancel_no"] = no;
            return body;
        }

        static void Status(string name, int status, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " status", ex.Status == status);
                return;
            }
            throw new InvalidOperationException(name);
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
