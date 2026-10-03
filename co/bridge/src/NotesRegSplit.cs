using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 分包（子票区间）应收票据的登记与删除规则（纯函数，--selftest 核对）。依据 U8 自己登记的分包票据：
    // AP_Note.bsubpackage=1、csubnostart / csubnoend 是子票区间，号从 1 起、每个号 0.01 元，(止 - 起 + 1) = 票面 × 100；
    // 登记时 Ap_Note_AvailRange 写一行整段可用区间；生成的收款单（48）的 cNoteNo、cCoVouchID 是「票据号-起-止」（不补零）。
    // U8 也有补零到 12 位的写法，按收款单找票据时起止按数值比较。
    internal static class NoteSplit
    {
        const long SerialMax = 999999999999999L;
        const int SerialDigits = 15;

        // sub_start / sub_end：同时给出或都不给；1 到 15 位正整数（与 API 一致），止不小于起，区间张数 = 票面 × 100。没给时两者为 0。
        public static void Parse(NoteRegAsk ask, Dictionary<string, object> body)
        {
            object start = Requests.Field(body, "sub_start");
            object end = Requests.Field(body, "sub_end");
            if (start == null && end == null)
            {
                return;
            }
            if (start == null || end == null)
            {
                throw BridgeException.BadField(start == null ? "sub_start" : "sub_end", "分包票据的 sub_start 与 sub_end 要同时给出");
            }
            ask.SubStart = Serial(start, "sub_start", "子票区间开始");
            ask.SubEnd = Serial(end, "sub_end", "子票区间结束");
            if (ask.SubEnd < ask.SubStart)
            {
                throw BridgeException.BadField("sub_end", "子票区间结束不能小于开始");
            }
            long count = ask.SubEnd - ask.SubStart + 1;
            if (count != NotesProcRule.Count(ask.Amount) || NotesProcRule.RangeAmount(ask.SubStart, ask.SubEnd) != ask.Amount)
            {
                throw BridgeException.BadField("sub_end", "子票区间与金额不符：区间 " + count.ToString(CultureInfo.InvariantCulture)
                    + " 张 = " + NotesProcRule.Money(NotesProcRule.RangeAmount(ask.SubStart, ask.SubEnd)) + " 元，票面 "
                    + NotesProcRule.Money(ask.Amount) + "（每张子票 0.01 元）");
            }
        }

        static long Serial(object raw, string field, string title)
        {
            long value;
            if (raw is int)
            {
                value = (int)raw;
            }
            else if (raw is long)
            {
                value = (long)raw;
            }
            else
            {
                throw BridgeException.BadField(field, field + "（" + title + "）必须是整数");
            }
            if (value < 1 || value > SerialMax)
            {
                throw BridgeException.BadField(field, field + "（" + title + "）必须是 1 到 15 位的正整数");
            }
            return value;
        }

        // 票据与登记请求的分包标志、子票区间一致。
        public static bool SameRange(NoteRow note, NoteRegAsk ask)
        {
            return note.Split == ask.Split && (!ask.Split || (note.Start == ask.SubStart && note.End == ask.SubEnd));
        }

        // 收款单的票据号（cNoteNo / cCoVouchID）：分包票据是「票据号-起-止」（不补零），否则就是票据号。start 为 0 表示非分包。
        public static string CoId(string no, long start, long end)
        {
            return NotesProcRule.DetailId(no, start, end);
        }

        public static string CoId(NoteRow note)
        {
            return note.Split ? CoId(note.Code, note.Start, note.End) : note.Code;
        }

        // 收款单上的票据号是否就是这张票据：非分包须与票据号相同；分包须是「票据号-起-止」且起止（按数值，允许补零）与票据的子票区间相同。
        public static bool Matches(string coId, NoteRow note)
        {
            if (note == null || string.IsNullOrEmpty(coId))
            {
                return false;
            }
            if (!note.Split)
            {
                return string.Equals(coId, note.Code, StringComparison.Ordinal);
            }
            long start;
            long end;
            return TrySuffix(coId, note.Code, out start, out end) && start == note.Start && end == note.End;
        }

        // text 是否为「no-起-止」（起、止都是 1 到 17 位数字，允许补零）。
        public static bool TrySuffix(string text, string no, out long start, out long end)
        {
            start = 0;
            end = 0;
            if (text == null || string.IsNullOrEmpty(no) || text.Length <= no.Length + 1
                || !text.StartsWith(no + "-", StringComparison.Ordinal))
            {
                return false;
            }
            string[] parts = text.Substring(no.Length + 1).Split('-');
            return parts.Length == 2 && Number(parts[0], out start) && Number(parts[1], out end);
        }

        static bool Number(string text, out long value)
        {
            value = 0;
            if (text.Length == 0 || text.Length > SerialDigits + 2)
            {
                return false;
            }
            foreach (char c in text)
            {
                if (c < '0' || c > '9')
                {
                    return false;
                }
            }
            return long.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value);
        }

        // 登记查重用的 LIKE 模式：「票据号-%-%」，票据号里的通配符按转义符 ! 转义（SQL 写 escape N'!'）。
        public static string SuffixPattern(string no)
        {
            StringBuilder sb = new StringBuilder();
            foreach (char c in no ?? "")
            {
                if (c == '!' || c == '%' || c == '_' || c == '[')
                {
                    sb.Append('!');
                }
                sb.Append(c);
            }
            return sb.Append("-%-%").ToString();
        }

        // 可用区间是否仍是登记时的样子：非分包没有可用区间；分包恰好一段 = 票据的子票区间，且区间金额 = 票面。
        // 不符返回原因（接在「票据 号 」之后），符合返回空串。
        public static string RangeRefusal(List<NoteRange> avail, NoteRow note)
        {
            if (!note.Split)
            {
                return avail.Count == 0 ? "" : "不是分包票据却有可用子票区间";
            }
            if (note.Start < 1 || note.End < note.Start)
            {
                return "是分包票据但子票区间不完整";
            }
            if (NotesProcRule.RangeAmount(note.Start, note.End) != note.Amount)
            {
                return "子票区间与票面金额不符";
            }
            if (avail.Count != 1 || avail[0].Start != note.Start || avail[0].End != note.End)
            {
                return "可用子票区间已不是登记时的整段（已部分处理或被修改）";
            }
            return "";
        }

        // 响应里的子票区间：非分包为 null。
        public static void Put(Dictionary<string, object> body, bool split, long start, long end)
        {
            body["sub_start"] = split ? (object)start : null;
            body["sub_end"] = split ? (object)end : null;
        }
    }
}
