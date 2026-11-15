using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售出库生单的行：source_line_id、quantity，另可带批号 cbatch、货位 cposition。
    // 同一发货行可以列多次（拆行），每次的批号 / 货位组合不同，数量之和是该发货行的应出数量。
    // 这里只解析和核对请求（StockGenSaleBatchCheck 查存货、仓库和现存量），都在调用 U8 之前抛出。
    internal static partial class StockGen
    {
        const int BatchMax = 60;
        const int PosMax = 20;

        sealed class OutSpec
        {
            public int Line;
            public decimal Qty;
            public string Batch;
            public string Pos;
        }

        sealed class OutAsk
        {
            // 发货行 → 应出数量（同一行各次之和）。
            public Dictionary<int, decimal> Want = new Dictionary<int, decimal>();
            // 发货行 → 请求里的各次（请求顺序）。
            public Dictionary<int, List<OutSpec>> ByLine = new Dictionary<int, List<OutSpec>>();
            // 有没有任何一行带了批号或货位，或者拆了行。没有就只按数量部分出库（原有行为）。
            public bool Any;
        }

        // 每行只收 source_line_id、quantity、cbatch、cposition；行要属于该发货单、未关闭，合计数量不超过 iQuantity − fOutQuantity。
        static OutAsk ParseOut(Dictionary<int, decimal[]> open, object[] lines)
        {
            OutAsk ask = new OutAsk();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < lines.Length; i++)
            {
                OutSpec spec = OutSpecOf(lines[i]);
                decimal[] have;
                if (!open.TryGetValue(spec.Line, out have))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".source_line_id", "明细行不存在");
                }
                if (!seen.Add(spec.Line.ToString(CultureInfo.InvariantCulture) + "\u0001" + spec.Batch + "\u0001" + spec.Pos))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".source_line_id", "明细行重复");
                }
                if (have[2] != 0m)
                {
                    throw new BridgeException(409, "state_mismatch", "明细行已关闭");
                }
                AddSpec(ask, spec);
                if (ask.Want[spec.Line] > have[0] - have[1])
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }
            return ask;
        }

        static void AddSpec(OutAsk ask, OutSpec spec)
        {
            List<OutSpec> list;
            if (!ask.ByLine.TryGetValue(spec.Line, out list))
            {
                list = new List<OutSpec>();
                ask.ByLine[spec.Line] = list;
            }
            list.Add(spec);
            decimal sum;
            ask.Want.TryGetValue(spec.Line, out sum);
            ask.Want[spec.Line] = sum + spec.Qty;
            ask.Any = ask.Any || spec.Batch.Length > 0 || spec.Pos.Length > 0 || list.Count > 1;
        }

        static OutSpec OutSpecOf(object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行不是对象");
            }
            foreach (string key in line.Keys)
            {
                string low = key == null ? "" : key.ToLowerInvariant();
                if (low != "source_line_id" && low != "quantity" && low != "cbatch" && low != "cposition")
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", key), "不能设置字段 " + key);
                }
            }
            OutSpec spec = new OutSpec();
            spec.Line = MfgReq.LineId(line);
            spec.Qty = MfgReq.LineQty(line);
            spec.Batch = OutText(line, "cbatch", BatchMax);
            spec.Pos = OutText(line, "cposition", PosMax);
            return spec;
        }

        // 批号、货位：字符串，去掉两侧空格；null 或空串算没给。
        static string OutText(Dictionary<string, object> line, string name, int max)
        {
            object value = MfgReq.Raw(line, name);
            if (value == null)
            {
                return "";
            }
            string text = value as string;
            if (text == null)
            {
                throw BridgeException.BadField("lines." + name, name + " 必须是字符串");
            }
            text = text.Trim();
            if (text.Length > max)
            {
                throw BridgeException.BadField("lines." + name,
                    name + " 最长 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符");
            }
            return text;
        }
    }
}
