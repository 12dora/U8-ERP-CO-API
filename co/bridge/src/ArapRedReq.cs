using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // arap/red_offset 的请求：同一往来单位、同一币种的红字单据（行）对冲蓝字单据（行）（U8「红票对冲」，处理方式 9N）。
    // Red / Blue 的每一项沿用转账的 TransferAskLine（类型、单号、行、原币金额，金额是正数）；Currency 为空表示本位币。
    internal sealed class RedAsk
    {
        public string Flag;
        public string Partner;
        public string Currency;
        public List<TransferAskLine> Red;
        public List<TransferAskLine> Blue;
        public decimal Sum;
    }

    // arap/red_offset在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、partner（客户 / 供应商编码）、
    // currency、red、blue [{type, id, line_id?, amount}]、digest。对冲日期就是登录日期 date（同核销、转账）。
    // 类型：应收 26 / 27 销售发票、R0 应收单；应付 01 / 02 采购发票、P0 应付单。红字一侧是余额为负、审核行 cSign=F 的单据，
    // 蓝字一侧是余额为正、cSign=Z 的单据（闸门在 ArapRedGate）。red 与 blue 的金额合计必须相等。
    // digest 只做校验、不使用：U8 的 AP_JZ_Red 报文没有摘要，处理行的摘要由 U8 从审核行复制（实测）。处理在 ArapRed。
    internal static class ArapRedReq
    {
        public const string Path = "/u8co/v1/arap/red_offset";
        public const string Action = "red_offset";
        public const int MaxLines = 50;
        const int CodeMax = 30;
        const int PartnerMax = 20;
        const int CurrencyMax = 20;
        const decimal AmountMax = 1000000000000m;
        // 路由的字段表（RequestsP4.P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[] { Path, "flag", "partner", "currency", "red", "blue", "digest" };
        static readonly string[] LineKeys = new string[] { "type", "id", "line_id", "amount" };

        public static bool IsPath(string path)
        {
            return path == Path;
        }

        public static RedAsk Parse(Dictionary<string, object> body)
        {
            RedAsk ask = new RedAsk();
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR（应收红票对冲）或 AP（应付红票对冲）", "flag");
            }
            ask.Partner = Code(Requests.Field(body, "partner"), "partner", ask.Flag == "AP" ? "供应商编码" : "客户编码", PartnerMax);
            object cur = Requests.Field(body, "currency");
            ask.Currency = cur == null ? "" : Code(cur, "currency", "币种", CurrencyMax);
            Digest(Requests.Field(body, "digest"));
            string[] types = ArapMergeReq.TypesOf(ask.Flag);
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            ask.Red = Lines(Requests.Field(body, "red"), "red", types, seen);
            ask.Blue = Lines(Requests.Field(body, "blue"), "blue", types, seen);
            decimal red = Total(ask.Red);
            if (red != Total(ask.Blue))
            {
                throw Bad("red 与 blue 的金额合计必须相等", "blue");
            }
            ask.Sum = red;
            return ask;
        }

        static decimal Total(List<TransferAskLine> lines)
        {
            decimal sum = 0m;
            foreach (TransferAskLine line in lines)
            {
                sum += line.Amount;
            }
            return sum;
        }

        static string Code(object raw, string field, string title, int max)
        {
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0)
            {
                throw Bad("缺少 " + field + "（" + title + "）", field);
            }
            if (text.Length > max || HasControl(text))
            {
                throw Bad(field + " 无效", field);
            }
            return text;
        }

        // 只校验形状（U8 不收摘要，见类注释）。
        static void Digest(object raw)
        {
            if (raw == null)
            {
                return;
            }
            string text = raw as string;
            if (text == null || HasControl(text) || text.Trim().Length > ArapVoucherReq.DigestMax)
            {
                throw Bad("digest 必须是不含控制字符、最多 " + ArapVoucherReq.DigestMax.ToString(CultureInfo.InvariantCulture)
                    + " 字的字符串", "digest");
            }
        }

        // seen 跨 red、blue 共用：同一单据（行）不能出现两次，同一单据也不能既按行又按整单，也不能一侧红字一侧蓝字。
        static List<TransferAskLine> Lines(object raw, string name, string[] types, HashSet<string> seen)
        {
            IList list = raw as IList;
            if (list == null || list.Count == 0 || list.Count > MaxLines)
            {
                throw Bad(name + " 必须是 1 到 " + MaxLines.ToString(CultureInfo.InvariantCulture) + " 项", name);
            }
            List<TransferAskLine> lines = new List<TransferAskLine>();
            for (int i = 0; i < list.Count; i++)
            {
                TransferAskLine line;
                try
                {
                    line = Line(list[i], name, types);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Under(ex, FieldPath.Item(name, i));
                }
                if (!Fresh(seen, line))
                {
                    throw Bad("red / blue 里有重复的单据行（同一单据不能出现两次，也不能既按行又按整单）", FieldPath.Item(name, i));
                }
                lines.Add(line);
            }
            return lines;
        }

        internal static bool Fresh(HashSet<string> seen, TransferAskLine line)
        {
            string doc = line.Type + "|" + line.Code;
            string key = doc + "|" + line.Line.ToString(CultureInfo.InvariantCulture);
            bool overlap = line.Line == 0 ? seen.Contains(doc + "|line") : seen.Contains(doc + "|0");
            if (overlap || !seen.Add(key))
            {
                return false;
            }
            if (line.Line != 0)
            {
                seen.Add(doc + "|line");
            }
            return true;
        }

        static TransferAskLine Line(object raw, string name, string[] types)
        {
            Dictionary<string, object> map = ArapWriteoffReq.Obj(raw, name + "[]");
            ArapWriteoffReq.Only(map, LineKeys, "");
            TransferAskLine line = new TransferAskLine();
            line.Type = ArapWriteoffReq.Text(map, "type");
            if (Array.IndexOf(types, line.Type) < 0)
            {
                throw Bad(name + "[].type 只能是 " + string.Join("、", types), "type");
            }
            line.Code = Code(Requests.Field(map, "id"), "id", "单据号", CodeMax);
            line.Line = ArapWriteoffReq.Id(Requests.Field(map, "line_id"), name + "[].line_id", false);
            if (line.Line != 0 && ArapMergeReq.IsBill(line.Type))
            {
                throw Bad("应收单、应付单按整单对冲，不能带 line_id", "line_id");
            }
            decimal value;
            if (!ArapWriteoffReq.TryNum(Requests.Field(map, "amount"), out value) || value <= 0 || value > AmountMax
                || decimal.Round(value, 2) != value)
            {
                throw Bad("amount 必须大于 0、不超过 1000000000000、最多两位小数", "amount");
            }
            line.Amount = value;
            return line;
        }

        static bool HasControl(string text)
        {
            foreach (char c in text)
            {
                if (char.IsControl(c))
                {
                    return true;
                }
            }
            return false;
        }

        // 锁键："arap:writeoff:<AR|AP>"（与该侧的核销、取消核销、自动核销、转账、并账、取消处理串行：它们改同一批余额）；
        // 单据要按单号查库才知道主键，入队前拿不到。请求体不合法时（登录前已 400）返回空。不是本路由返回 null。
        public static string[] LockKeysOf(string path, Dictionary<string, object> body)
        {
            if (!IsPath(path))
            {
                return null;
            }
            try
            {
                return new string[] { "arap:writeoff:" + Parse(body).Flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // arap/red_offset 接到请求分派（Requests：挂在核销一组，不带 type、id）上的部分。
    internal static partial class Requests
    {
        static bool IsRedOffset(string path)
        {
            return ArapRedReq.IsPath(path);
        }

        // 是红票对冲路由时校验并定登录子系统（= flag），返回 true；否则返回 false。
        static bool ApplyRedOffset(Dictionary<string, object> body, WorkItem item)
        {
            if (!ArapRedReq.IsPath(item.Path))
            {
                return false;
            }
            RedAsk ask = ArapRedReq.Parse(body);
            item.SubId = ask.Flag;
            CoRows.Note(item, "红票对冲 " + ask.Partner);
            return true;
        }
    }

    // 红票对冲（arap/red_offset）。功能权限已按 U8 授权目录核对：「转账 → 红票对冲」AR050503 / AP050503，
    // 上级「转账」AR0505 / AP0505 同样放行。
    // 数据权限：每张单据表头的往来单位、部门、业务员（应收按客户规则、应付按供应商规则）。
    internal static partial class PermRegistry
    {
        public static string RedOffsetKey(string flag)
        {
            return "write:arap:red_offset:" + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] RedOffsetRules()
        {
            return new PermRule[]
            {
                R(RedOffsetKey("AR"), "应收红票对冲", A("AR050503", "AR0505"), WriteoffObjs(PermObj.Customer)),
                R(RedOffsetKey("AP"), "应付红票对冲", A("AP050503", "AP0505"), WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
