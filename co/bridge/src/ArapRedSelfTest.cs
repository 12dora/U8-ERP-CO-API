using System;
using System.Collections.Generic;
using System.Text;
using System.Xml;

namespace U8Co
{
    // --selftest 的红票对冲部分（arap/red_offset）：请求校验、锁键、号的形状、符号规则（余额、发票累计）、分摊、
    // AP_JZ_Red 报文的形状（测试账套实测：canceldata 根 + redvouch / bluevouch，金额不带符号、两位小数）、9N 行的期望与比对、
    // SQL 占位符个数。只跑纯函数，不连库、不建 COM。
    internal static class ArapRedSelfTest
    {
        const decimal Tol = 0.005m;

        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckNo();
            CheckRules();
            CheckSpread();
            CheckXml();
            CheckExpected();
            CheckSql();
        }

        static void CheckParse()
        {
            RedAsk ask = ArapRedReq.Parse(Body("{\"flag\":\"AR\",\"partner\":\" C0001 \",\"red\":[{\"type\":\"26\",\"id\":\"SI0002\","
                + "\"line_id\":31,\"amount\":10}],\"blue\":[{\"type\":\"26\",\"id\":\"SI0001\",\"amount\":6},"
                + "{\"type\":\"R0\",\"id\":\"YS0001\",\"amount\":4}],\"digest\":\"红票对冲\"}"));
            Expect("red parse", ask.Flag == "AR" && ask.Partner == "C0001" && ask.Currency == "" && ask.Sum == 10m);
            Expect("red lines", ask.Red.Count == 1 && ask.Red[0].Line == 31 && ask.Blue.Count == 2 && ask.Blue[1].Line == 0);
            string[] keys = ArapRedReq.LockKeysOf(ArapRedReq.Path, Body("{\"flag\":\"AP\",\"partner\":\"S1\",\"red\":[{\"type\":\"P0\","
                + "\"id\":\"YF1\",\"amount\":1}],\"blue\":[{\"type\":\"01\",\"id\":\"PI1\",\"amount\":1}]}"));
            Expect("red keys", string.Join(",", keys) == "arap:writeoff:AP");
            Expect("red keys bad", ArapRedReq.LockKeysOf(ArapRedReq.Path, Body("{\"flag\":\"AP\"}")).Length == 0);
            Expect("red keys other", ArapRedReq.LockKeysOf(ArapMergeReq.Path, Body("{}")) == null);
            Expect("red spec", ArapRedReq.Spec[0] == ArapRedReq.Path && Array.IndexOf(ArapRedReq.Spec, "date") < 0);
        }

        static void CheckBad()
        {
            const string Red = "\"red\":[{\"type\":\"26\",\"id\":\"SI2\",\"amount\":10}]";
            const string Blue = "\"blue\":[{\"type\":\"26\",\"id\":\"SI1\",\"amount\":10}]";
            Bad("red flag", "{\"flag\":\"GL\",\"partner\":\"C1\"," + Red + "," + Blue + "}", "flag");
            Bad("red partner", "{\"flag\":\"AR\"," + Red + "," + Blue + "}", "partner");
            Bad("red empty", "{\"flag\":\"AR\",\"partner\":\"C1\",\"red\":[]," + Blue + "}", "red");
            Bad("red sum", "{\"flag\":\"AR\",\"partner\":\"C1\"," + Red + ",\"blue\":[{\"type\":\"26\",\"id\":\"SI1\",\"amount\":9}]}", "blue");
            Bad("red side type", "{\"flag\":\"AP\",\"partner\":\"S1\"," + Red + "," + Blue + "}", "red.0.type");
            Bad("red 48", "{\"flag\":\"AR\",\"partner\":\"C1\",\"red\":[{\"type\":\"48\",\"id\":\"SK1\",\"amount\":10}]," + Blue + "}",
                "red.0.type");
            Bad("red bill line", "{\"flag\":\"AR\",\"partner\":\"C1\",\"red\":[{\"type\":\"R0\",\"id\":\"YS1\",\"line_id\":3,\"amount\":10}],"
                + Blue + "}", "red.0.line_id");
            Bad("red cents", "{\"flag\":\"AR\",\"partner\":\"C1\",\"red\":[{\"type\":\"26\",\"id\":\"SI2\",\"amount\":10.001}]," + Blue + "}",
                "red.0.amount");
            Bad("red dup", "{\"flag\":\"AR\",\"partner\":\"C1\"," + Red + ",\"blue\":[{\"type\":\"26\",\"id\":\"SI2\",\"amount\":10}]}",
                "blue.0");
            Bad("red digest", "{\"flag\":\"AR\",\"partner\":\"C1\"," + Red + "," + Blue + ",\"digest\":5}", "digest");
        }

        // 号、符号：红字余额 -10 对冲 4 后 -6（+4），蓝字 10 对冲 4 后 6（-4）；发票累计红字 -4、蓝字 +4（实测 iExchSum 蓝 +10 / 红 -10）。
        static void CheckNo()
        {
            Expect("red no", ArapRedRule.NoValid("AR", "HRAR0000000000001") && ArapRedRule.NoValid("AP", "HPAP0000000000001"));
            Expect("red no side", !ArapRedRule.NoValid("AP", "HRAR0000000000001") && !ArapRedRule.NoValid("AR", "HRAR"));
            Expect("red no shape", !ArapRedRule.NoValid("AR", "HXAR0000000000001") && !ArapRedRule.NoValid("AR", null));
            Expect("red no tail", !ArapRedRule.NoValid("AR", "HRAR1 "));
        }

        static void CheckRules()
        {
            Expect("red delta", ArapRedRule.Delta(true, 4m) == 4m && ArapRedRule.Delta(false, 4m) == -4m);
            Expect("red acc", ArapRedRule.AccDelta(true, 4m) == -4m && ArapRedRule.AccDelta(false, 4m) == 4m);
            Expect("red open red", ArapRedRule.Open(true, -10m) == 10m && ArapRedRule.Open(true, 10m) == 0m);
            Expect("red open blue", ArapRedRule.Open(false, 10m) == 10m && ArapRedRule.Open(false, -10m) == 0m);
            Expect("red native", ArapRedRule.Native(10.005m, true, 7m) == 10.005m && ArapRedRule.Native(3.33m, false, 7.2008m) == 23.98m);
        }

        // 红字发票两行 -6 / -8，对冲 10：先小行号；超过可对冲量返回 null；符号不对的行跳过。
        static void CheckSpread()
        {
            List<RedSlot> slots = new List<RedSlot>();
            slots.Add(Slot(32, -8m));
            slots.Add(Slot(31, -6m));
            slots.Add(Slot(33, 5m));
            List<RedPiece> pieces = ArapRedRule.Spread(true, slots, 10m, true, 1m);
            Expect("red spread", pieces != null && pieces.Count == 2 && pieces[0].Line == 31 && pieces[0].F == 6m && pieces[1].F == 4m
                && pieces[1].BeforeF == -8m && pieces[1].N == 4m);
            Expect("red spread over", ArapRedRule.Spread(true, slots, 14.01m, true, 1m) == null && ArapRedRule.Available(true, slots) == 14m);
            Expect("red spread blue", ArapRedRule.Spread(false, slots, 5m, true, 1m).Count == 1 && ArapRedRule.Available(false, slots) == 5m);
        }

        static void CheckXml()
        {
            RedPlan plan = Sample();
            XmlDocument doc = new XmlDocument();
            doc.LoadXml(ArapRedRule.Xml(plan));
            XmlElement root = doc.DocumentElement;
            Attrs("red xml root", root, "canceldata", "cdwcode=C0001", "cexch_name=人民币", "iexchrate=1", "dhxdate=2026-10-02");
            XmlNodeList red = root.SelectNodes("redvouch");
            XmlNodeList blue = root.SelectNodes("bluevouch");
            Expect("red xml nodes", red.Count == 1 && blue.Count == 2);
            Attrs("red xml red", (XmlElement)red[0], "redvouch", "cvouchtype=26", "cvouchcode=SI0002", "iinvid=31", "icancelamount_f=10.00",
                "icancelamount=10.00", "dvouchdate=2026-09-30", "cdeptcode=D01", "cexchname=人民币", "cperson=", "cinvcode=");
            Attrs("red xml bill", (XmlElement)blue[1], "bluevouch", "cvouchtype=R0", "iinvid=0", "icancelamount_f=4.00");
        }

        // 元素名对，且每个「名=值」属性都在、值相同（值为空也要有这个属性）。
        static void Attrs(string name, XmlElement e, string tag, params string[] pairs)
        {
            Expect(name + " tag", e.Name == tag);
            foreach (string pair in pairs)
            {
                int at = pair.IndexOf('=');
                string key = pair.Substring(0, at);
                Expect(name + " " + key, e.HasAttribute(key) && e.GetAttribute(key) == pair.Substring(at + 1));
            }
        }

        // 期望：键（类型|单号|行）→ 带符号变化；与实际比对（缺键、多键、金额差都报键）。
        static void CheckExpected()
        {
            RedPlan plan = Sample();
            Dictionary<string, decimal[]> want = ArapRedRule.Expected(plan);
            Expect("red want", want.Count == 3 && want["26|SI0002|31"][0] == 10m && want["26|SI0001|21"][0] == -6m
                && want["R0|YS0001|0"][1] == -4m);
            Dictionary<string, decimal[]> got = new Dictionary<string, decimal[]>(want);
            Expect("red diff ok", ArapRedRule.Diff(want, got, Tol) == null);
            got["R0|YS0001|0"] = new decimal[] { -4m, -4.01m };
            Expect("red diff amount", ArapRedRule.Diff(want, got, Tol) == "R0|YS0001|0");
            got.Remove("R0|YS0001|0");
            Expect("red diff missing", ArapRedRule.Diff(want, got, Tol) == "R0|YS0001|0");
            got = new Dictionary<string, decimal[]>(want);
            got["26|SI0009|1"] = new decimal[] { 0m, 0m };
            Expect("red diff extra", ArapRedRule.Diff(want, got, Tol) == "26|SI0009|1");
        }

        static void CheckSql()
        {
            Expect("red slot sql", Marks(ArapRedSql.SlotText("AP")) == 4 && ArapRedSql.SlotText("AP").IndexOf("Ap_Detail", StringComparison.Ordinal) > 0
                && ArapRedSql.SlotText("AR").IndexOf("isnull(d.iDAmount_f,0)-isnull(d.iCAmount_f,0)", StringComparison.Ordinal) > 0);
            Expect("red rows sql", Marks(ArapRedSql.RowsText("AR")) == 2 && ArapRedSql.RowsText("AR").IndexOf("N'9N'", StringComparison.Ordinal) > 0);
            Expect("red cancel fill", Marks(ArapRedSql.CancelSaleFill) == 2 && Marks(ArapRedSql.CancelPurFill) == 2
                && ArapRedSql.CancelSaleFill.IndexOf("#ap_SaleBillVouchHXdata", StringComparison.Ordinal) > 0
                && ArapRedSql.CancelPurFill.IndexOf("#ap_PurBillVouchHXdata", StringComparison.Ordinal) > 0);
        }

        // 实测形状：应收，客户 C0001，红字发票 SI0002 行 31 对冲 10；蓝字发票 SI0001 行 21 对冲 6、应收单 YS0001 对冲 4。
        static RedPlan Sample()
        {
            RedPlan plan = new RedPlan();
            plan.Flag = "AR";
            plan.Date = "2026-10-02";
            plan.Partner = "C0001";
            plan.Currency = "人民币";
            plan.Home = true;
            plan.Sum = 10m;
            plan.Red.Add(Doc(true, "26", "SI0002", 31, 10m));
            plan.Blue.Add(Doc(false, "26", "SI0001", 21, 6m));
            plan.Blue.Add(Doc(false, "R0", "YS0001", 0, 4m));
            return plan;
        }

        static RedDoc Doc(bool red, string type, string code, int line, decimal amount)
        {
            TransferDoc core = new TransferDoc();
            core.Ask = new TransferAskLine();
            core.Ask.Type = type;
            core.Ask.Code = code;
            core.Ask.Line = line;
            core.Ask.Amount = amount;
            core.Side = "AR";
            core.Rate = 1m;
            core.Head = new Dictionary<string, object>();
            core.Head["vdate"] = "2026-09-30";
            RedPiece p = new RedPiece();
            p.Line = line;
            p.F = amount;
            p.N = amount;
            p.Sign = new Dictionary<string, object>();
            p.Sign["dept"] = "D01";
            RedDoc d = new RedDoc();
            d.Core = core;
            d.Red = red;
            d.Pieces.Add(p);
            return d;
        }

        static RedSlot Slot(int line, decimal signed)
        {
            RedSlot s = new RedSlot();
            s.Line = line;
            s.SignedF = signed;
            s.SignedN = signed;
            return s;
        }

        static int Marks(string sql)
        {
            return ProcCancelSql.Marks(sql);
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                ArapRedReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
