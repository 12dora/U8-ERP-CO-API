using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // arap/merge 请求里的一张单据（或它的一行）。Line 为 0 表示没给 line_id；Amount 为 null 表示并入全部余额。
    internal sealed class MergeAskLine
    {
        public string Type;
        public string Code;
        public int Line;
        public decimal? Amount;
    }

    // arap/merge 的请求：把若干单据在往来单位 From 下的余额并到 To 名下（U8「并账」BZ）。
    internal sealed class MergeAsk
    {
        public string Flag;
        public string From;
        public string To;
        public string Digest;
        public List<MergeAskLine> Lines;
    }

    // arap/merge（并账）在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、from、to、lines、digest；
    // 并账日期就是登录日期 date。lines[] 是 {type, id, line_id?, amount?}：type 是 U8 单据类型（应收 26 / 27 / R0，
    // 应付 01 / 02 / P0），id 是单据号，line_id 是发票行主键（= 往来明细 iBVid），应收单 / 应付单按整单不带 line_id；
    // amount 是原币正数，省略即并入该单据（行）在 from 名下的全部余额。处理在 ArapMerge。
    internal static class ArapMergeReq
    {
        public const string Path = "/u8co/v1/arap/merge";
        public const string Action = "merge";
        public const int MaxLines = 50;
        public const int CodeMax = 20;
        public const int DigestMax = 120;
        public const string DefaultDigest = "并账";
        // Requests 的字段表（P4Specs）：路由，然后是 date 等公共字段以外的字段。
        public static readonly string[] Spec = new string[] { Path, "flag", "from", "to", "lines", "digest" };
        const decimal AmountMax = 1000000000000m;
        static readonly string[] LineKeys = new string[] { "type", "id", "line_id", "amount" };
        static readonly string[] ArTypes = new string[] { "26", "27", "R0" };
        static readonly string[] ApTypes = new string[] { "01", "02", "P0" };

        // meta 的 ops.arap_merge：能并账的四种类型。
        public static bool Handles(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            return name == "sale_invoice" || name == "ar_bill" || name == "purchase_invoice" || name == "ap_bill";
        }

        public static string[] TypesOf(string flag)
        {
            return flag == "AP" ? ApTypes : ArTypes;
        }

        // 应收单 R0、应付单 P0 按整单（往来明细 iBVid 为 0）。
        public static bool IsBill(string type)
        {
            return type == "R0" || type == "P0";
        }

        public static MergeAsk Parse(Dictionary<string, object> body)
        {
            MergeAsk ask = new MergeAsk();
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            string label = ask.Flag == "AP" ? "供应商" : "客户";
            ask.From = Code(Requests.Field(body, "from"), "from", label);
            ask.To = Code(Requests.Field(body, "to"), "to", label);
            if (string.Equals(ask.From, ask.To, StringComparison.OrdinalIgnoreCase))
            {
                throw Bad("from 与 to 不能是同一个" + label, "to");
            }
            ask.Digest = Digest(Requests.Field(body, "digest"));
            ask.Lines = Lines(Requests.Field(body, "lines"), ask.Flag);
            return ask;
        }

        static string Code(object raw, string field, string label)
        {
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > CodeMax)
            {
                throw Bad(field + " 必须是 1 到 20 个字符的" + label + "编码", field);
            }
            return text;
        }

        static string Digest(object raw)
        {
            if (raw == null)
            {
                return DefaultDigest;
            }
            string text = raw as string;
            if (text == null || text.Trim().Length == 0 || text.Trim().Length > DigestMax)
            {
                throw Bad("digest 必须是 1 到 120 个字符的文本", "digest");
            }
            return text.Trim();
        }

        static List<MergeAskLine> Lines(object raw, string flag)
        {
            IList list = raw as IList;
            if (list == null || list.Count == 0 || list.Count > MaxLines)
            {
                throw Bad("lines 必须是 1 到 " + MaxLines.ToString(CultureInfo.InvariantCulture) + " 项", "lines");
            }
            List<MergeAskLine> lines = new List<MergeAskLine>();
            for (int i = 0; i < list.Count; i++)
            {
                MergeAskLine line;
                try
                {
                    line = Line(list[i], flag);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Under(ex, FieldPath.Item("lines", i));
                }
                string clash = Clash(lines, line);
                if (clash != null)
                {
                    throw Bad(clash, FieldPath.Item("lines", i));
                }
                lines.Add(line);
            }
            return lines;
        }

        // 同一单据（行）只能出现一次；同一张单据不能既整单又按行。返回 null 表示没有冲突。
        internal static string Clash(List<MergeAskLine> seen, MergeAskLine line)
        {
            foreach (MergeAskLine one in seen)
            {
                if (one.Type != line.Type || !string.Equals(one.Code, line.Code, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (one.Line == line.Line)
                {
                    return "lines 里有重复的单据行";
                }
                if (one.Line == 0 || line.Line == 0)
                {
                    return "同一张单据不能既整单又按行并账";
                }
            }
            return null;
        }

        static MergeAskLine Line(object raw, string flag)
        {
            Dictionary<string, object> map = ArapWriteoffReq.Obj(raw, "lines[]");
            ArapWriteoffReq.Only(map, LineKeys, "");
            MergeAskLine line = new MergeAskLine();
            line.Type = ArapWriteoffReq.Text(map, "type");
            if (Array.IndexOf(TypesOf(flag), line.Type) < 0)
            {
                throw Bad(flag == "AP" ? "type 只能是 01、02、P0（采购发票、应付单）" : "type 只能是 26、27、R0（销售发票、应收单）", "type");
            }
            string code = ArapWriteoffReq.Text(map, "id").Trim();
            if (code.Length == 0 || code.Length > 30)
            {
                throw Bad("id 必须是 1 到 30 个字符的单据号", "id");
            }
            line.Code = code;
            line.Line = ArapWriteoffReq.Id(Requests.Field(map, "line_id"), "lines[].line_id", false);
            if (line.Line != 0 && IsBill(line.Type))
            {
                throw Bad("应收单、应付单按整单并账，不能带 line_id", "line_id");
            }
            line.Amount = Amount(Requests.Field(map, "amount"));
            return line;
        }

        // 省略或 JSON null：并入全部余额。给了就大于 0、不超过 1000000000000、最多两位小数（原币）。
        static decimal? Amount(object raw)
        {
            if (raw == null)
            {
                return null;
            }
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad("amount 必须大于 0、不超过 1000000000000、最多两位小数", "amount");
            }
            return value;
        }

        // 锁键 "arap:writeoff:<AR|AP>"：单据主键入队前查不到，与同一侧的核销、取消核销串行（并账改的正是核销读的余额）。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                return new string[] { "arap:writeoff:" + Parse(body).Flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        // DocLocks.ArapKeys 的最后一档：不是 arap/merge 返回 null。
        public static string[] LockKeysOf(string path, Dictionary<string, object> body)
        {
            return path == Path ? LockKeys(body) : null;
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
