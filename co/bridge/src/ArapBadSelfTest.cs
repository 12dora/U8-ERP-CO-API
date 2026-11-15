using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // --selftest 的坏账处理部分：arap/bad_debt 的请求校验、锁键、审计 action；坏账发生的分摊、汇率、处理号形状；
    // 坏账准备参数行、收款单表头 / 表体的拒绝原因；脚本拒绝编号到中文、两段脚本与拒绝表一致；预演、权限、写闸门已登记。
    // 只跑纯函数和内嵌资源，不连库、不建 COM。计提坏账准备另见 ArapBadProvisionSelfTest。
    internal static class ArapBadSelfTest
    {
        // 脚本里只用于防御的编号（参数缺失、分摊表为空），按内部错误处理，不进中文表。
        static readonly int[] Internal = new int[] { 50101, 50111 };

        public static void Run()
        {
            CheckOccurParse();
            CheckRecoverParse();
            CheckRefuse();
            CheckRules();
            CheckSpread();
            CheckPara();
            CheckRecoverGate();
            CheckBalance();
            CheckRefusals();
            CheckScripts();
            CheckWiring();
            TestAccountGateSelfTest.Check("bad_debt", ArapBadReq.TestOnly);
        }

        static void CheckOccurParse()
        {
            Dictionary<string, object> body = Occur();
            ArapBadAsk ask = ArapBadReq.Parse(body);
            Expect("bad parse occur", ask.Action == "occur" && ask.Date == new DateTime(2026, 8, 31) && ask.Customer == "C0003");
            Expect("bad parse occur defaults", ask.Currency == "" && ask.Digest == "" && ask.Lines.Count == 2);
            Expect("bad parse occur lines", ask.Lines[0].LineId == 0 && ask.Lines[1].Type == "R0" && ask.Lines[1].Amount == 12.5m);
            Expect("bad title occur", ArapBadReq.Title(ask) == "坏账发生 客户 C0003 2 项 2026-08-31");
            Expect("bad audit occur", ArapBadReq.AuditOf(ask) == "bad_debt_occur");
            string[] keys = ArapBadReq.LockKeys(body);
            Expect("bad lock keys", keys.Length == 2 && keys[0] == "arap:writeoff:AR" && keys[1] == "arap:bad:AR");
            CheckOccurFull(body);
        }

        static void CheckOccurFull(Dictionary<string, object> body)
        {
            body["flag"] = "AR";
            body["currency"] = " 美元 ";
            body["dept"] = "01";
            body["person"] = "P01";
            body["digest"] = " 客户破产 ";
            ArapBadAsk ask = ArapBadReq.Parse(body);
            Expect("bad parse occur full", ask.Currency == "美元" && ask.Dept == "01" && ask.Person == "P01" && ask.Digest == "客户破产");
            Dictionary<string, object> lined = Occur();
            lined["lines"] = new List<object> { Line("26", "INV00001", 1001, 10m), Line("26", "INV00001", 1002, 5m) };
            ask = ArapBadReq.Parse(lined);
            Expect("bad parse lines", ask.Lines.Count == 2 && ask.Lines[1].LineId == 1002);
            Dictionary<string, object> prov = Base("provision");
            ask = ArapBadReq.Parse(prov);
            Expect("bad parse provision", ask.Action == "provision" && ask.Lines.Count == 0);
            Expect("bad title provision", ArapBadReq.Title(ask) == "计提坏账准备 2026-08-31");
        }

        static void CheckRecoverParse()
        {
            Dictionary<string, object> body = Recover();
            ArapBadAsk ask = ArapBadReq.Parse(body);
            Expect("bad parse recover", ask.Action == "recover" && ask.Customer == "C0003" && ask.Receipt == "0000000123"
                && ask.Amount == 300m && ArapBadReq.AuditOf(ask) == "bad_debt_recover");
        }

        static void CheckRefuse()
        {
            Refuse("bad action", Occur(), "action", "lost", "action");
            Refuse("bad flag", Occur(), "flag", "AP", "flag");
            Refuse("bad date", Occur(), "date", "2026/08/31", "date");
            Refuse("bad customer", Occur(), "customer", " ", "customer");
            Refuse("bad occur receipt", Occur(), "receipt", "0000000123", "receipt");
            Refuse("bad recover lines", Recover(), "lines", new List<object>(), "lines");
            Refuse("bad provision customer", Base("provision"), "customer", "C1", "customer");
            Refuse("bad recover amount", Recover(), "amount", 0, "amount");
            Refuse("bad recover receipt", Recover(), "receipt", null, "receipt");
            Refuse("bad lines empty", Occur(), "lines", new List<object>(), "lines");
            Refuse("bad line type rz", Occur(), "lines", new List<object> { Line("RZ", "X1", 0, 1m) }, "lines.0.type");
            Refuse("bad line type 48", Occur(), "lines", new List<object> { Line("48", "X1", 0, 1m) }, "lines.0.type");
            Refuse("bad line r0 line_id", Occur(), "lines", new List<object> { Line("R0", "X1", 7, 1m) }, "lines.0.line_id");
            Refuse("bad line amount", Occur(), "lines", new List<object> { Line("26", "X1", 0, 1.001m) }, "lines.0.amount");
            Refuse("bad line dup", Occur(), "lines", new List<object> { Line("26", "X1", 0, 1m), Line("26", "X1", 0, 2m) }, "lines.1");
            Refuse("bad line overlap", Occur(), "lines", new List<object> { Line("26", "X1", 5, 1m), Line("26", "X1", 0, 2m) },
                "lines.1");
            Dictionary<string, object> extra = Line("26", "X1", 0, 1m);
            extra["memo"] = "x";
            Refuse("bad line unknown", Occur(), "lines", new List<object> { extra }, "lines.0.memo");
            Expect("bad lock keys bad", ArapBadReq.LockKeys(Base("lost")).Length == 0);
        }

        static void CheckRules()
        {
            Expect("bad pickable", ArapBadRule.Pickable("26") && ArapBadRule.Pickable("29") && ArapBadRule.Pickable("R9"));
            Expect("bad not pickable", !ArapBadRule.Pickable("RZ") && !ArapBadRule.Pickable("48") && !ArapBadRule.Pickable("R")
                && !ArapBadRule.Pickable(null));
            Expect("bad whole", ArapBadRule.WholeDoc("R0") && !ArapBadRule.WholeDoc("27") && ArapBadRule.KindOf("R3") == "ar_bill"
                && ArapBadRule.KindOf("28") == "sale_invoice");
            CheckNumbers();
            CheckRates();
        }

        static void CheckNumbers()
        {
            Expect("bad no", ArapBadRule.NoValid("HZAR0000000000001") && !ArapBadRule.NoValid("HZAR0000000013"));
            Expect("bad no shape", !ArapBadRule.NoValid("SYRAR000000000013") && !ArapBadRule.NoValid("HZAR000000000001X")
                && !ArapBadRule.NoValid(null));
        }

        static void CheckRates()
        {
            List<BadDoc> docs = new List<BadDoc> { Doc(6.5m), Doc(6.5m) };
            Expect("bad same rate", ArapBadRule.SameRate(docs, false));
            docs.Add(Doc(7m));
            Expect("bad other rate", !ArapBadRule.SameRate(docs, false) && ArapBadRule.SameRate(docs, true));
            Expect("bad remain problem", ArapBadOccur.RemainProblem(-10m, -10m) == null
                && ArapBadOccur.RemainProblem(5m, -10m) == "坏账准备余额是 5.00，应为 -10.00");
            Expect("bad slot sql", ArapBadOccurSql.SlotQuery(true).Contains("'0' as line")
                && !ArapBadOccurSql.SlotQuery(true).Contains("group by")
                && ArapBadOccurSql.SlotQuery(false).EndsWith("group by isnull(d.iBVid,0)", StringComparison.Ordinal));
        }

        // 按行主键从小到大分摊，跳过余额不大于 0 的行；一行全部处理时本币取该行本币余额，否则按汇率折算。
        static void CheckSpread()
        {
            BadDoc doc = Doc(6.5m);
            List<BadSlot> slots = new List<BadSlot> { Slot(30, 50m, 325.10m), Slot(10, -5m, -32.5m), Slot(20, 40m, 260.20m) };
            List<BadPiece> pieces = ArapBadRule.Spread(doc, slots, 60m, false);
            Expect("bad spread order", pieces != null && pieces.Count == 2 && pieces[0].Line == 20 && pieces[0].F == 40m
                && pieces[0].N == 260.20m && pieces[1].Line == 30 && pieces[1].F == 20m && pieces[1].N == 130m && pieces[1].BeforeF == 50m);
            Expect("bad spread short", ArapBadRule.Spread(doc, slots, 90.01m, false) == null);
            pieces = ArapBadRule.Spread(doc, slots, 90m, true);
            Expect("bad spread home", pieces != null && pieces[1].N == 50m);
            Expect("bad native round", ArapBadRule.Native(1.01m, Slot(1, 5m, 32.5m), false, 6.785m) == 6.85m);
        }

        static void CheckPara()
        {
            Expect("bad para none", ArapBad.ParaRefusal(null, 2026) == "未设置 2026 年坏账准备参数（应收款管理 › 设置 › 坏账准备）");
            Dictionary<string, object> row = Para("5", "2025", "3");
            string why = ArapBad.ParaRefusal(row, 2026);
            Expect("bad para year", why != null && why.EndsWith("最新一行坏账准备参数是 2025 年的", StringComparison.Ordinal));
            Expect("bad para style", ArapBad.ParaRefusal(Para("5", "2026", "4"), 2026).StartsWith("不支持的坏账计提方法", StringComparison.Ordinal));
            Expect("bad para ok", ArapBad.ParaRefusal(Para("5", "2026", "1"), 2026) == null);
            Expect("bad closed text", ArapBad.ClosedText(2026, 8) == "应收 2026 年 8 月已结账");
        }

        static void CheckRecoverGate()
        {
            ArapBadAsk ask = ArapBadReq.Parse(Recover());
            Dictionary<string, object> head = Receipt();
            Expect("bad receipt ok", ArapBadRecoverGate.HeadRefusal(head, ask, "人民币", "人民币", "2026-08-31") == null);
            Expect("bad receipt date", ArapBadRecoverGate.HeadRefusal(head, ask, "人民币", "人民币", "2026-08-01") != null);
            Expect("bad receipt cur", ArapBadRecoverGate.HeadRefusal(head, ask, "美元", "人民币", "2026-08-31").Contains("币种"));
            head["auditor"] = "张三";
            Expect("bad receipt audited", ArapBadRecoverGate.HeadRefusal(head, ask, "人民币", "人民币", "2026-08-31").Contains("已审核"));
            head = Receipt();
            head["note_no"] = "BP001";
            Expect("bad receipt note", ArapBadRecoverGate.HeadRefusal(head, ask, "人民币", "人民币", "2026-08-31").Contains("手工录入"));
            head = Receipt();
            head["cDwCode"] = "C9";
            Expect("bad receipt partner", ArapBadRecoverGate.HeadRefusal(head, ask, "人民币", "人民币", "2026-08-31").Contains("C9"));
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>> { ReceiptLine("0", "300.00", "300.00") };
            Expect("bad receipt line ok", ArapBadRecoverGate.LinesRefusal(lines, ask) == null);
            lines[0]["rem_f"] = "100.00";
            lines[0]["rem"] = "100.00";
            Expect("bad receipt partial", ArapBadRecoverGate.LinesRefusal(lines, ask).Contains("部分核销"));
            lines = new List<Dictionary<string, object>> { ReceiptLine("1", "300.00", "300.00") };
            Expect("bad receipt prepay", ArapBadRecoverGate.LinesRefusal(lines, ask).Contains("预收款"));
            lines = new List<Dictionary<string, object>> { ReceiptLine("0", "250.00", "250.00") };
            Expect("bad receipt amount", ArapBadRecoverGate.LinesRefusal(lines, ask) == "坏账收回金额须等于收款单金额 250.00");
            lines.Add(ReceiptLine("0", "50.00", "50.00"));
            Expect("bad receipt multi", ArapBadRecoverGate.LinesRefusal(lines, ask).StartsWith("坏账收回只支持单行收款单", StringComparison.Ordinal));
        }

        // 坏账收回后收款单上的往来明细（审核行贷方 + 9H 借方）本币、原币都要相抵为 0；不为 0 时消息带合计。
        static void CheckBalance()
        {
            Expect("bad net zero", ArapBadRecover.NetProblem("0000000056", new decimal[] { 0m, 0m }) == null);
            string off = ArapBadRecover.NetProblem("0000000056", new decimal[] { 300m, 0m });
            Expect("bad net native", off != null && off.Contains("0000000056") && off.Contains("300.00"));
            Expect("bad net foreign", ArapBadRecover.NetProblem("0000000056", new decimal[] { 0m, 50m }) != null);
        }

        static void CheckRefusals()
        {
            BridgeException closed = ArapBad.Refused(new ScriptRefusal(50102, "AR period already closed (GL_mend)", null),
                ArapBadOccurSql.Texts);
            Expect("bad refuse closed", closed.Status == 409 && closed.Code == "state_mismatch" && closed.Message == "登记日期所在期间应收已结账");
            BridgeException used = ArapBad.Refused(new ScriptRefusal(50104, "cancel no already used: HZAR0000000000014", null),
                ArapBadRecover.Texts);
            Expect("bad refuse used", used.Message == "处理号已被占用，请检查 Ap_CancelNo（HZ / AR）：HZAR0000000000014");
            BridgeException rem = ArapBad.Refused(new ScriptRefusal(50113, "9G remainder changed: 26 INV00001", null), ArapBadOccurSql.Texts);
            Expect("bad refuse remain", rem.Status == 409 && rem.Message == "单据余额在处理中被改动，已回滚，请重试：26 INV00001");
            Expect("bad refuse internal", ArapBad.Refused(new ScriptRefusal(50101, "x", null), ArapBadOccurSql.Texts).Status == 500
                && ArapBad.Refused(new ScriptRefusal(50121, "x", null), ArapBadOccurSql.Texts).Status == 500
                && ArapBad.Refused(new ScriptRefusal(50121, "x", null), ArapBadRecover.Texts).Status == 409);
        }

        // 两段脚本都在 SqlScript 名单里、没有 GO；THROW 编号都在 50101–50139（与计提坏账准备的 50160 段错开）且是拒绝编号，
        // 防御性的以外都有中文；编号 13 位、只改核对过的那一行坏账准备参数。
        static void CheckScripts()
        {
            CheckScript(ArapBadOccurSql.Script, ArapBadOccurSql.Texts);
            CheckScript(ArapBadRecover.Script, ArapBadRecover.Texts);
            string occur = SqlScript.Text(ArapBadOccurSql.Script);
            string recover = SqlScript.Text(ArapBadRecover.Script);
            foreach (string text in new string[] { occur, recover })
            {
                Has("bad script common", text, "RIGHT(REPLICATE(N'0', 13) + CONVERT(nvarchar(13), @n), 13)", "WHERE autoid = @pid;",
                    "cType = N'HZ' AND cFlag = N'AR'", "ORDER BY autoid DESC");
            }
            Has("bad script occur", occur, "N'9G', @no", "iRemainAmount = ISNULL(iRemainAmount, 0) - @sum_n", "#ap_SaleBillVouchHXdata");
            Has("bad script recover", recover, "N'9H', @no", "iRemainAmount = ISNULL(iRemainAmount, 0) + @amt_n",
                "SET iRAmt = 0, iRAmt_f = 0, iRAmt_s = 0", "SET @own = N'AR48' + @code;", "cCheckMan, N'')), N'') IS NOT NULL",
                "NULL, b.ID, b.cKm,");
            // 收款单由 U8 审核组件审核（ArapBadRecover），脚本不再用 SQL 填审核人。
            Expect("bad script recover no audit", !recover.Contains("UPDATE Ap_CloseBill SET cCheckMan"));
            Expect("bad prep tables", ArapBadOccurSql.PrepSql.Contains("#bad_args") && ArapBadOccurSql.PrepSql.Contains("#bad_pick")
                && ArapBadOccurSql.DropSql.Contains("#bad_rem"));
        }

        static void CheckScript(string name, Dictionary<int, string> texts)
        {
            Expect("bad script listed " + name, Array.IndexOf(SqlScript.Names, name) >= 0);
            string text = SqlScript.Text(name);
            Expect("bad script no GO " + name, !SqlScriptSelfTest.HasGo(text));
            Regex thrown = new Regex("THROW (5[0-9]{4})", RegexOptions.CultureInvariant);
            foreach (Match m in thrown.Matches(text))
            {
                int number = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
                Expect("bad script range " + number, SqlScript.IsRefusal(number) && number >= 50101 && number <= 50139);
                if (Array.IndexOf(Internal, number) < 0)
                {
                    Expect("bad script text " + number, ArapBad.Refused(new ScriptRefusal(number, "x: y", null), texts).Status == 409);
                }
            }
        }

        static void CheckWiring()
        {
            Expect("bad dry run", DryRunModes.Lookup("arap/bad_debt", "", "", "") == DryRunModes.Rollback
                && DryRunModes.Lookup(ArapBadReq.Path, "", "", "") == DryRunModes.Rollback);
            Expect("bad perm", PermRegistry.ForKey(PermRegistry.BadDebtKey("occur")) != null
                && PermRegistry.ForKey(PermRegistry.BadDebtKey("recover")) != null
                && PermRegistry.ForKey(PermRegistry.BadDebtKey("provision")) != null
                && PermRegistry.BadDebtKey("occur") == "write:arap:bad_debt:occur");
            Expect("bad paths", ArapBadReq.IsPath(ArapBadReq.Path) && !ArapBadReq.IsPath(ArapExGainReq.Path)
                && WriteGate.IsWrite(ArapBadReq.Path));
        }

        static Dictionary<string, object> Base(string action)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["action"] = action;
            body["date"] = "2026-08-31";
            body["year"] = "2026";
            return body;
        }

        static Dictionary<string, object> Occur()
        {
            Dictionary<string, object> body = Base("occur");
            body["customer"] = " C0003 ";
            body["lines"] = new List<object> { Line("26", "INV00001", 0, 100m), Line("R0", "QTYS0001", 0, 12.5m) };
            return body;
        }

        static Dictionary<string, object> Recover()
        {
            Dictionary<string, object> body = Base("recover");
            body["customer"] = "C0003";
            body["receipt"] = "0000000123";
            body["amount"] = 300;
            return body;
        }

        static Dictionary<string, object> Line(string type, string id, int line, decimal amount)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["type"] = type;
            map["id"] = id;
            if (line > 0)
            {
                map["line_id"] = line;
            }
            map["amount"] = amount;
            return map;
        }

        static BadDoc Doc(decimal rate)
        {
            BadDoc d = new BadDoc();
            d.Rate = rate;
            return d;
        }

        static BadSlot Slot(int line, decimal f, decimal n)
        {
            BadSlot s = new BadSlot();
            s.Line = line;
            s.RemainF = f;
            s.RemainN = n;
            return s;
        }

        static Dictionary<string, object> Para(string id, string year, string style)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["id"] = id;
            row["y"] = year;
            row["style"] = style;
            row["remain"] = "0.00";
            return row;
        }

        static Dictionary<string, object> Receipt()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["id"] = "88";
            head["cDwCode"] = "C0003";
            head["vdate"] = "2026-08-20";
            head["cur"] = "人民币";
            head["src_flag"] = "A";
            return head;
        }

        static Dictionary<string, object> ReceiptLine(string itype, string amt, string rem)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["line"] = "901";
            line["itype"] = itype;
            line["amt"] = amt;
            line["amt_f"] = amt;
            line["rem"] = rem;
            line["rem_f"] = rem;
            return line;
        }

        static void Has(string name, string text, params string[] parts)
        {
            foreach (string part in parts)
            {
                Expect(name + " " + part, text.Contains(part));
            }
        }

        static void Refuse(string name, Dictionary<string, object> body, string key, object value, string field)
        {
            body[key] = value;
            bool ok = false;
            try
            {
                ArapBadReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                ok = ex.Status == 400 && ex.Field == field;
            }
            Expect(name, ok);
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
