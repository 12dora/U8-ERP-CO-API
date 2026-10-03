using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的红字冲销部分（GlReverse）：只测纯逻辑（请求校验、锁键、摘要前缀、红字分录取负、原凭证闸门、回读核对、
    // 报文里的负数量与原样复制的原币、路由登记），不连库、不建 COM。
    internal static class GlReverseSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckKeys();
            CheckDigest();
            CheckDraft();
            CheckGate();
            CheckCompare();
            CheckXml();
            Expect("write gate", WriteGate.IsWrite(GlReverseReq.Path));
        }

        static Dictionary<string, object> Body()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["period"] = 12;
            body["sign"] = "转";
            body["no"] = 9;
            return body;
        }

        static void CheckParse()
        {
            GlReverseAsk ask = GlReverseReq.Parse(Body(), 2026);
            Expect("parse", ask.Year == 2026 && ask.Period == 12 && ask.Sign == "转" && ask.No == 9 && ask.Date == "");
            Dictionary<string, object> body = Body();
            body["fiscal_year"] = 2025;
            body["voucher_date"] = "2026-01-31";
            ask = GlReverseReq.Parse(body, 2026);
            Expect("cross year", ask.Year == 2025 && ask.Date == "2026-01-31" && ask.Source().Year == 2025);
            Expect("pre login", GlReverseReq.Parse(Body(), 0).Year == 0);
            Dictionary<string, object> later = Body();
            later["fiscal_year"] = 2027;
            Bad("later year", later, 2026, "fiscal_year");
            Dictionary<string, object> date = Body();
            date["voucher_date"] = "2025-12-31";
            Bad("date year", date, 2026, "voucher_date");
            date["voucher_date"] = "2026/01/31";
            Bad("date format", date, 0, "voucher_date");
            Dictionary<string, object> no = Body();
            no.Remove("no");
            Bad("no missing", no, 0, "no");
            Dictionary<string, object> text = Body();
            text["no"] = "9";
            Bad("no text", text, 0, "no");
        }

        static void CheckKeys()
        {
            string[] keys = GlReverseReq.LockKeys(Body(), "2026");
            Expect("keys", keys.Length == 3 && keys[0] == "gl:2026:12:转:9" && keys[1] == "new:gl:转" && keys[2] == "gl:post");
            Dictionary<string, object> body = Body();
            body["fiscal_year"] = 2025;
            Expect("keys year", GlReverseReq.LockKeys(body, "2026")[0] == "gl:2025:12:转:9");
        }

        static void CheckDigest()
        {
            GlKey key = new GlKey();
            key.Sign = "转";
            key.No = 9;
            string prefix = GlReverseSrc.Prefix(key, "2025-11-30");
            Expect("prefix", prefix == "[冲销2025.11.30 转-0009号凭证]");
            Expect("digest", GlReverseSrc.Digest(prefix, "计提费用") == "[冲销2025.11.30 转-0009号凭证]计提费用");
            Expect("digest cut", GlReverseSrc.Digest(prefix, new string('费', 200)).Length == 120);
            key.No = 12345;
            Expect("prefix wide", GlReverseSrc.Prefix(key, "2025-01-31") == "[冲销2025.01.31 转-12345号凭证]");
        }

        internal static Dictionary<string, object> Row(int inid, string code, string md, string mc, string nd)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["inid"] = inid.ToString(System.Globalization.CultureInfo.InvariantCulture);
            row["ccode"] = code;
            row["dg"] = "计提费用";
            row["md"] = md;
            row["mc"] = mc;
            row["md_f"] = "0.0000";
            row["mc_f"] = "0.0000";
            row["nd"] = nd;
            row["nc"] = "0.000000";
            row["rate"] = "0.0000000000";
            row["sup"] = inid == 2 ? "S001" : "";
            row["op"] = inid == 2 ? "-" : "";
            row["defs"] = "0";
            row["idoc"] = "-1";
            return row;
        }

        static List<Dictionary<string, object>> Source()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Row(1, "51019901", "1234.5000", "0.0000", "100.000000"));
            rows.Add(Row(2, "22029902", "0.0000", "1234.5000", "0.000000"));
            return rows;
        }

        static void CheckDraft()
        {
            GlKey key = new GlKey();
            key.Sign = "转";
            key.No = 9;
            List<Dictionary<string, object>> cash = new List<Dictionary<string, object>>();
            Dictionary<string, object> flow = new Dictionary<string, object>();
            flow["inid"] = "2";
            flow["item"] = "04";
            flow["md"] = "0.0000";
            flow["mc"] = "1234.5000";
            cash.Add(flow);
            GlDraft draft = GlReverseSrc.Draft(key, "2025-11-30", Source(), cash);
            GlLine one = draft.Lines[0];
            GlLine two = draft.Lines[1];
            Expect("draft debit", one.Debit == -1234.50m && one.Credit == 0);
            Expect("draft debit qty", one.Qty == -100m && one.Flows.Count == 0);
            Expect("draft credit", two.Credit == -1234.50m && two.Debit == 0);
            Expect("draft credit aux", two.Qty == 0 && two.Supplier == "S001");
            Expect("draft flow", two.Flows.Count == 1 && two.Flows[0].Credit == -1234.50m);
            Expect("draft head", draft.Sign == "转" && draft.Attachments == 0);
            Expect("draft digest", one.Digest.StartsWith("[冲销2025.11.30 转-0009号凭证]", StringComparison.Ordinal));
            GlCarry carry = GlReverseSrc.Carry(Source());
            Expect("carry", carry.At(2)[2] == "-");
        }

        static void CheckGate()
        {
            GlReverseSrc.Gate(Source());
            List<Dictionary<string, object>> red = Source();
            red[1]["blue"] = "GL0000000900001";
            Refused("gate red", red);
            List<Dictionary<string, object>> defs = Source();
            defs[0]["defs"] = "1";
            Refused("gate defs", defs);
            List<Dictionary<string, object>> side = Source();
            side[0]["nc"] = "3.000000";
            Refused("gate qty side", side);
            List<Dictionary<string, object>> one = Source();
            one.RemoveAt(1);
            Refused("gate one line", one);
            List<Dictionary<string, object>> fc = Source();
            fc[0]["exch"] = "美元";
            fc[0]["md_f"] = "100.0000";
            Refused("gate fc without rate", fc);
            fc[0]["rate"] = "7.1000000000";
            GlReverseSrc.Gate(fc);
        }

        static void CheckCompare()
        {
            GlReversePlan plan = new GlReversePlan();
            plan.SourceRows = Source();
            plan.BlueNo = "GL0000000900001";
            plan.RedNo = "GL0000000900002";
            List<Dictionary<string, object>> red = new List<Dictionary<string, object>>();
            red.Add(Row(1, "51019901", "-1234.5000", "0.0000", "-100.000000"));
            red.Add(Row(2, "22029902", "0.0000", "-1234.5000", "0.000000"));
            foreach (Dictionary<string, object> row in red)
            {
                row["blue"] = plan.BlueNo;
                row["outno"] = plan.RedNo;
            }
            Expect("compare ok", GlReverseBack.Compare(plan, red) == null);
            red[0]["nd"] = "100.000000";
            Expect("compare qty", GlReverseBack.Compare(plan, red) != null);
            red[0]["nd"] = "-100.000000";
            red[1]["blue"] = "";
            Expect("compare link", GlReverseBack.Compare(plan, red) != null);
            red.RemoveAt(1);
            Expect("compare count", GlReverseBack.Compare(plan, red) != null);
        }

        // 负数量照写、单价为正；原样复制的原币不按汇率重算。
        static void CheckXml()
        {
            GlKey key = new GlKey();
            key.Year = 2026;
            key.Period = 1;
            key.Sign = "转";
            GlDraft draft = new GlDraft();
            draft.Sign = "转";
            GlLine line = new GlLine();
            line.Account = "51019901";
            line.Digest = "x";
            line.Debit = -1234.50m;
            line.Qty = -100m;
            line.Currency = "美元";
            line.Rate = 7.1m;
            line.Fc = -173.87m;
            draft.Lines.Add(line);
            string xml = GlXml.Envelope(true, key, draft, "2026-01-31", "张三", null);
            Expect("xml qty", xml.Contains("<debit_quantity>-100</debit_quantity>") && xml.Contains("<unit_price>12.345"));
            Expect("xml fc", xml.Contains("<primary_debit_amount>-173.87</primary_debit_amount>"));
            Expect("xml amount", xml.Contains("<natural_debit_currency>-1234.50</natural_debit_currency>"));
        }

        static void Refused(string name, List<Dictionary<string, object>> rows)
        {
            try
            {
                GlReverseSrc.Gate(rows);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409 && ex.Code == "state_mismatch");
                return;
            }
            throw new InvalidOperationException("gl reverse " + name + " 未拒绝");
        }

        static void Bad(string name, Dictionary<string, object> body, int year, string field)
        {
            try
            {
                GlReverseReq.Parse(body, year);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("gl reverse " + name + " 未拒绝");
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("gl reverse " + name);
            }
        }
    }
}
