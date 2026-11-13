using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的销售发票多来源部分：sources 的解析与拒绝（SaleInvSources）、锁键、多张来源的同客户等核对
    // （SaleGen.SameParty）、回签字段 cbdefine1–3 的白名单（只给蓝字，红字不收）、收集、不进 DOM、
    // 保存后发票行核对（SalePins.MatchInvoice）、补写 SQL 形状。不连库。
    internal static class SaleInvSourcesSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckApply();
            CheckRejects();
            CheckLocks();
            CheckParty();
            CheckWhitelist();
            CheckPins();
            CheckMatch();
            CheckClean();
        }

        // 浮点扩展自定义项的规整（SaleExtraRead.Clean，同数量、单价）与扩展表覆盖 DOM 原始文本（MergeRow overwrite）。
        static void CheckClean()
        {
            Expect("clean float", SaleExtraRead.Clean("2.1499999999999999", "float") == "2.15");
            Expect("clean real", SaleExtraRead.Clean("0.95000000000000007", "REAL") == "0.95");
            Expect("clean int-ish", SaleExtraRead.Clean("118.000000", "float") == "118");
            Expect("clean text kept", SaleExtraRead.Clean("2.1499999999999999", "nvarchar") == "2.1499999999999999");
            Expect("clean empty", SaleExtraRead.Clean("", "float") == "" && SaleExtraRead.Clean("x", "float") == "x");
            Dictionary<string, object> line = Map("cbdefine1", "2.1499999999999999", "cmemo", "m");
            SaleExtraRead.MergeRow(line, Map("AutoID", "9", "cbdefine1", "2.15"), "AutoID", true);
            Expect("merge overwrite", (string)line["cbdefine1"] == "2.15" && (string)line["cmemo"] == "m");
            SaleExtraRead.MergeRow(line, Map("cbdefine1", "3"), "AutoID");
            Expect("merge keeps", (string)line["cbdefine1"] == "2.15");
            CheckLoadLines();
        }

        // vouchers/load 的销售发票：组件 DOM 行带浮点原始文本，扩展表读回的也是原始文本时，并入后必须是 2.15（测试通道实测的失败）。
        static void CheckLoadLines()
        {
            List<object> lines = new List<object>();
            lines.Add(Map("autoid", "9001", "idlsid", "11", "cbdefine1", "2.1499999999999999", "iquantity", "2"));
            lines.Add(Map("autoid", "9002", "idlsid", "21"));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Map("AutoID", "9001", "cbdefine1", "2.1499999999999999", "cbdefine9", "x"));
            rows.Add(Map("AutoID", "9002", "cbdefine1", "0.95000000000000007"));
            HashSet<string> floats = new HashSet<string>(StringComparer.Ordinal);
            floats.Add("cbdefine1");
            SaleExtraRead.MergeExtraLines(lines, rows, floats, "AutoID");
            Dictionary<string, object> first = (Dictionary<string, object>)lines[0];
            Dictionary<string, object> second = (Dictionary<string, object>)lines[1];
            Expect("load line clean", (string)first["cbdefine1"] == "2.15" && (string)second["cbdefine1"] == "0.95");
            Expect("load line others", (string)first["cbdefine9"] == "x" && (string)first["iquantity"] == "2");
            Expect("invoice load merges", SaleExtraRead.Find(Kinds.Find("sale_invoice"))[4] == "SaleBillVouchs_extradefine");
        }

        static void CheckParse()
        {
            VoucherKind inv = Kinds.Find("sale_invoice");
            object raw = Sources(Source(801, Line(11, 2)), Source(802, Line(21, 1), Line(22, 3)));
            InvSource[] got = SaleInvSources.Parse(raw, inv);
            Expect("src count", got.Length == 2 && got[0].Id == 801 && got[1].Id == 802);
            Expect("src lines", got[0].Lines.Length == 1 && got[1].Lines.Length == 2 && SaleInvSources.LineCount(got) == 3);
            object[] ids = SaleInvSources.Ids(got);
            Expect("src ids", ids.Length == 2 && (int)ids[0] == 801 && (int)ids[1] == 802);
            InvSource[] one = SaleInvSources.Of(new WorkItem(), 7, new object[] { Line(1, 1) });
            Expect("src single", one.Length == 1 && one[0].Id == 7 && one[0].Lines.Length == 1);
        }

        static void CheckApply()
        {
            VoucherKind inv = Kinds.Find("sale_invoice");
            object raw = Sources(Source(801, Line(11, 2)), Source(802, Line(21, 1), Line(22, 3)));
            WorkItem item = new WorkItem();
            item.Type = inv;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[SaleInvSources.Field] = raw;
            SaleInvSources.Apply(body, item, new AuditDraft());
            Expect("src apply", item.HasId && item.Id == 801 && item.GenSources.Length == 2
                && object.ReferenceEquals(SaleInvSources.Of(item, 1, null), item.GenSources));
        }

        static void CheckRejects()
        {
            VoucherKind inv = Kinds.Find("sale_invoice");
            Bad("src not list", "sources", delegate { SaleInvSources.Parse("x", inv); });
            Bad("src empty", "sources", delegate { SaleInvSources.Parse(new ArrayList(), inv); });
            Bad("src dup", "sources.1.id", delegate { SaleInvSources.Parse(Sources(Source(5, Line(1, 1)), Source(5, Line(2, 1))), inv); });
            Bad("src no id", "sources.0.id", delegate { SaleInvSources.Parse(Sources(Source(0, Line(1, 1))), inv); });
            Dictionary<string, object> extra = Source(5, Line(1, 1));
            extra["head"] = new Dictionary<string, object>();
            Bad("src extra key", "sources.0.head", delegate { SaleInvSources.Parse(Sources(extra), inv); });
            Dictionary<string, object> text = Source(5, Line(1, 1));
            text["id"] = "5";
            Bad("src id text", "sources.0.id", delegate { SaleInvSources.Parse(Sources(text), inv); });
            Dictionary<string, object> noLines = Source(5);
            Bad("src no lines", "sources.0.lines", delegate { SaleInvSources.Parse(Sources(noLines), inv); });
            Dictionary<string, object> noQty = Source(5, Line(1, 1));
            ((Dictionary<string, object>)((ArrayList)noQty["lines"])[0]).Remove("quantity");
            Bad("src line field", "sources.0.lines.0.quantity", delegate { SaleInvSources.Parse(Sources(noQty), inv); });
            ArrayList many = new ArrayList();
            for (int i = 0; i < 101; i++)
            {
                many.Add(Source(1000 + i, Line(2 * i + 1, 1), Line(2 * i + 2, 1)));
            }
            Bad("src over 200 lines", "sources", delegate { SaleInvSources.Parse(many, inv); });
            WorkItem disp = new WorkItem();
            disp.Type = Kinds.Find("dispatch");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[SaleInvSources.Field] = Sources(Source(5, Line(1, 1)));
            Bad("src other kind", "sources", delegate { SaleInvSources.Apply(body, disp, null); });
            WorkItem item = new WorkItem();
            item.Type = Kinds.Find("sale_invoice");
            body["id"] = 5;
            Bad("src with id", "sources", delegate { SaleInvSources.Apply(body, item, null); });
            item.GenSources = new InvSource[] { new InvSource() };
            item.Source = Kinds.Find("sale_return");
            Bad("src red source", "sources", delegate { SaleInvSources.CheckSource(item); });
            item.Source = Kinds.Find("dispatch");
            SaleInvSources.CheckSource(item);
        }

        static void CheckLocks()
        {
            WorkItem item = new WorkItem();
            item.GenSources = SaleInvSources.Parse(Sources(Source(801, Line(1, 1)), Source(802, Line(2, 1))), Kinds.Find("sale_invoice"));
            string[] keys = SaleInvSources.LockKeys(item, "sale_invoice");
            Expect("src locks", keys.Length == 3 && keys[0] == "dispatch:801" && keys[1] == "dispatch:802" && keys[2] == "new:sale_invoice");
        }

        static void CheckParty()
        {
            Dictionary<string, object> a = Party("C900001", "普通销售", "人民币", "1", "13");
            SaleGen.SameParty(a, Party("c900001 ", "普通销售", "人民币", "1.000000", "13.00"), 1);
            Bad("party customer", "sources.1.id", delegate { SaleGen.SameParty(a, Party("C900002", "普通销售", "人民币", "1", "13"), 1); });
            Bad("party bus", "sources.2.id", delegate { SaleGen.SameParty(a, Party("C900001", "分期收款", "人民币", "1", "13"), 2); });
            Bad("party currency", "sources.1.id", delegate { SaleGen.SameParty(a, Party("C900001", "普通销售", "美元", "1", "13"), 1); });
            Bad("party rate", "sources.1.id", delegate { SaleGen.SameParty(a, Party("C900001", "普通销售", "人民币", "7.1", "13"), 1); });
            Bad("party tax", "sources.1.id", delegate { SaleGen.SameParty(a, Party("C900001", "普通销售", "人民币", "1", "9"), 1); });
        }

        static void CheckWhitelist()
        {
            for (int n = 1; n <= 3; n++)
            {
                Expect("inv line bdefine" + n, SaleGen.MetaInvoice("cbdefine" + n, false));
                Expect("inv line bdefine" + n + " off dom", SalePins.OffDom(SalePins.Invoice, "cbdefine" + n, false, true));
            }
            Expect("inv line bdefine4 refused", !SaleGen.MetaInvoice("cbdefine4", false));
            Expect("inv head bdefine refused", !SaleGen.MetaInvoice("cbdefine1", true));
            Expect("inv line memo", SaleGen.MetaInvoice("cmemo", false) && SaleGen.MetaInvoice("cdefine22", false));
            Expect("inv key", SalePins.KeyOf("SaleBillVouchs_extradefine") == "AutoID");
        }

        static void CheckPins()
        {
            List<InvPinLine> lines = new List<InvPinLine>();
            lines.Add(PinLine(11, 2m, Map("source_line_id", 11, "quantity", 2, "cbdefine1", "2.15", "cMemo", "m")));
            lines.Add(PinLine(21, 1m, Map("source_line_id", 21, "quantity", 1)));
            lines.Add(PinLine(22, 3m, Map("source_line_id", 22, "quantity", 3, "CBDEFINE3", 39.5m, "cbdefine2", "")));
            SalePins pins = SalePins.Collect(SalePins.Invoice, lines);
            CheckPinItems(pins);
            Dictionary<string, object> dom = SalePins.DomOnly(SalePins.Invoice, lines[0].Fields);
            Expect("dom only", dom.Count == 3 && dom.ContainsKey("cMemo") && !dom.ContainsKey("cbdefine1")
                && lines[0].Fields.ContainsKey("cbdefine1"));
            Expect("pins sql", SalePins.WriteSql(pins.Items[0]).StartsWith(
                "IF EXISTS (SELECT 1 FROM [SaleBillVouchs_extradefine] WHERE [AutoID]=?) UPDATE [SaleBillVouchs_extradefine] SET [cbdefine1]=?",
                StringComparison.Ordinal));
        }

        static void CheckPinItems(SalePins pins)
        {
            Expect("pins count", pins.Items.Count == 2 && pins.InvLines.Count == 3);
            SalePin first = pins.Items[0];
            Expect("pins first path", first.Path == "lines.0.cbdefine1" && first.SrcLine == 11);
            Expect("pins first table", first.Table == "SaleBillVouchs_extradefine" && first.KeyColumn == "AutoID" && first.Explicit);
            SalePin third = pins.Items[1];
            Expect("pins third", third.Path == "lines.2.cbdefine3" && third.SrcLine == 22 && third.Value == "39.5");
        }

        static void CheckMatch()
        {
            List<InvPinLine> want = new List<InvPinLine>();
            want.Add(PinLine(11, 2m, null));
            want.Add(PinLine(21, 0.5m, null));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Map("AutoID", "9001", "iDLsID", "11", "q", "2.000000"));
            rows.Add(Map("AutoID", "9002", "iDLsID", "21", "q", "0.500000"));
            Dictionary<int, int> map = new Dictionary<int, int>();
            Expect("match ok", SalePins.MatchInvoice(rows, want, map) == "" && map[11] == 9001 && map[21] == 9002);
            rows[1]["q"] = "1.000000";
            Expect("match qty", SalePins.MatchInvoice(rows, want, new Dictionary<int, int>()).Length > 0);
            rows[1]["q"] = "0.500000";
            rows.Add(Map("AutoID", "9003", "iDLsID", "22", "q", "1"));
            Expect("match extra row", SalePins.MatchInvoice(rows, want, new Dictionary<int, int>()).Length > 0);
            rows.RemoveAt(2);
            rows[1]["iDLsID"] = "11";
            Expect("match dup src", SalePins.MatchInvoice(rows, want, new Dictionary<int, int>()).Length > 0);
        }

        static InvPinLine PinLine(int src, decimal qty, Dictionary<string, object> fields)
        {
            InvPinLine line = new InvPinLine();
            line.SrcLine = src;
            line.Qty = qty;
            line.Fields = fields ?? new Dictionary<string, object>();
            return line;
        }

        static Dictionary<string, object> Party(string cus, string bus, string cur, string rate, string tax)
        {
            return Map("cCusCode", cus, "cBusType", bus, "cexch_name", cur, "iExchRate", rate, "iTaxRate", tax);
        }

        static ArrayList Sources(params Dictionary<string, object>[] items)
        {
            return new ArrayList(items);
        }

        static Dictionary<string, object> Source(int id, params Dictionary<string, object>[] lines)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["id"] = id;
            if (lines.Length > 0)
            {
                map["lines"] = new ArrayList(lines);
            }
            return map;
        }

        static Dictionary<string, object> Line(int src, int qty)
        {
            return Map("source_line_id", src, "quantity", qty);
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        delegate void Act();

        static void Bad(string name, string field, Act act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " field=" + ex.Field, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("sale_inv_sources " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("sale_inv_sources " + name);
            }
        }
    }
}
