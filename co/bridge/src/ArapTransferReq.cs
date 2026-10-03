using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 转账请求里的一张单据（一侧的一项）。Code 是单据号；Line 为 0 表示没给 line_id（按行主键从小到大依次分摊）。
    internal sealed class TransferAskLine
    {
        public string Type;
        public string Code;
        public int Line;
        public decimal Amount;
    }

    // arap/transfer 的请求：应收冲应付（flag AR，9I）/ 应付冲应收（flag AP，9J）。Currency 为空表示本位币；Digest 为空用缺省摘要。
    internal sealed class TransferAsk
    {
        public string Flag;
        public string Customer;
        public string Vendor;
        public string Currency;
        public string Digest;
        public List<TransferAskLine> Ar;
        public List<TransferAskLine> Ap;
        public decimal Sum;
    }

    // arap/transfer在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、customer、vendor、currency、
    // ar_lines、ap_lines [{type, id, line_id?, amount}]、digest。转账日期就是登录日期 date（同核销）。处理在 ArapTransfer。
    // 应收一侧的类型：26 / 27 销售发票、R0 应收单；应付一侧：01 / 02 采购发票、P0 应付单。
    // 收付款单（48 / 49）不收：U8 里它们参与转账会另生成预收 / 预付等单据，本接口不做；收付款单与发票、应收应付单之间用核销。
    internal static class ArapTransferReq
    {
        public const string Action = "transfer";
        public const int MaxLines = 50;
        const int CodeMax = 30;
        const int PartnerMax = 20;
        const int CurrencyMax = 20;
        const decimal AmountMax = 1000000000000m;
        // 路由的字段表（RequestsP4.P4Specs）。
        internal static readonly string[] Spec = new string[]
        {
            Requests.TransferPath, "flag", "customer", "vendor", "currency", "ar_lines", "ap_lines", "digest"
        };
        static readonly string[] LineKeys = new string[] { "type", "id", "line_id", "amount" };
        static readonly string[] ArTypes = new string[] { "26", "27", "R0" };
        static readonly string[] ApTypes = new string[] { "01", "02", "P0" };

        // meta 的 ops.transfer：参与转账的四种单据（销售、采购发票，应收单、应付单）。
        public static bool Handles(VoucherKind kind)
        {
            return ArapWriteoffReq.FlagOfItem(kind == null ? "" : kind.Name) != null;
        }

        public static TransferAsk Parse(Dictionary<string, object> body)
        {
            TransferAsk ask = new TransferAsk();
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR（应收冲应付）或 AP（应付冲应收）", "flag");
            }
            ask.Customer = Code(Requests.Field(body, "customer"), "customer", "客户编码", PartnerMax);
            ask.Vendor = Code(Requests.Field(body, "vendor"), "vendor", "供应商编码", PartnerMax);
            object cur = Requests.Field(body, "currency");
            ask.Currency = cur == null ? "" : Code(cur, "currency", "币种", CurrencyMax);
            ask.Digest = Digest(Requests.Field(body, "digest"));
            ask.Ar = Lines(Requests.Field(body, "ar_lines"), "ar_lines", ArTypes);
            ask.Ap = Lines(Requests.Field(body, "ap_lines"), "ap_lines", ApTypes);
            decimal ar = Total(ask.Ar);
            if (ar != Total(ask.Ap))
            {
                throw Bad("ar_lines 与 ap_lines 的金额合计必须相等", "ap_lines");
            }
            ask.Sum = ar;
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

        static string Digest(object raw)
        {
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null || HasControl(text))
            {
                throw Bad("digest 必须是不含控制字符的字符串", "digest");
            }
            text = text.Trim();
            if (text.Length > ArapVoucherReq.DigestMax)
            {
                throw Bad("digest 最多 " + ArapVoucherReq.DigestMax.ToString(CultureInfo.InvariantCulture) + " 字", "digest");
            }
            return text;
        }

        // 票据背书的 ap_lines（NotesProcReq）用同一写法。
        internal static List<TransferAskLine> Lines(object raw, string name, string[] types)
        {
            IList list = raw as IList;
            if (list == null || list.Count == 0 || list.Count > MaxLines)
            {
                throw Bad(name + " 必须是 1 到 " + MaxLines.ToString(CultureInfo.InvariantCulture) + " 项", name);
            }
            List<TransferAskLine> lines = new List<TransferAskLine>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
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
                    throw Bad(name + " 里有重复的单据行（同一单据不能既按行又按整单）", FieldPath.Item(name, i));
                }
                lines.Add(line);
            }
            return lines;
        }

        // 同一（类型、单号、行）只出现一次（单号不分大小写，同库的排序规则）；同一单据也不能既有不带 line_id 的一项、又有带 line_id 的项（分摊会重叠）。
        static bool Fresh(HashSet<string> seen, TransferAskLine line)
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
            if (line.Type == "48" || line.Type == "49")
            {
                throw Bad("转账只支持发票和应收单 / 应付单；收付款单请用核销", "type");
            }
            if (Array.IndexOf(types, line.Type) < 0)
            {
                throw Bad(name + "[].type 只能是 " + string.Join("、", types), "type");
            }
            line.Code = Code(Requests.Field(map, "id"), "id", "单据号", CodeMax);
            line.Line = ArapWriteoffReq.Id(Requests.Field(map, "line_id"), name + "[].line_id", false);
            if (line.Line != 0 && ArapTransferRule.Mode(line.Type) == ArapTransferRule.WholeDoc)
            {
                throw Bad("应收单、应付单按整单转账，不能带 line_id", "line_id");
            }
            line.Amount = Amount(Requests.Field(map, "amount"));
            return line;
        }

        // 金额（原币）大于 0、不超过 1000000000000、最多两位小数。
        static decimal Amount(object raw)
        {
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad("amount 必须大于 0、不超过 1000000000000、最多两位小数", "amount");
            }
            return value;
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

        // 锁键：应收、应付两侧的 "arap:writeoff:<AR|AP>"——与两侧的核销、取消核销、自动核销串行（它们改同一批余额）；
        // 单据要按单号查库才知道主键，入队前拿不到。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                Parse(body);
                return new string[] { "arap:writeoff:AP", "arap:writeoff:AR" };
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
}
