using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // notes/process 的请求。票据按单号 NoteCode 或主键 NoteId（AP_Note.Auto_ID）之一指定。
    // Amount 为 null 表示按缺省（背书取 ap_lines 合计，其余取票据余额或分包的整段可用区间；非分包票据的金额必须等于余额）；
    // SubStart / SubEnd 为 0 表示没给子票区间。
    internal sealed class NotesProcAsk
    {
        public string Flag;
        public string Op;
        public string NoteCode;
        public int NoteId;
        public decimal? Amount;
        public long SubStart;
        public long SubEnd;
        public string BankCode;
        public string BankName;
        public decimal Expense;
        public decimal Interest;
        public decimal Rate;
        public string Vendor;
        public List<TransferAskLine> ApLines;
        public string Digest;
    }

    // notes/process（票据处理）在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、op、note、amount、
    // sub_start / sub_end（分包票据的子票区间）、bank_code / bank_name（结算、贴现）、expense / interest / rate（贴现）、
    // vendor / ap_lines（背书）、digest。处理日期就是登录日期 date。处理在 NotesProc。
    // op：settle 托收 / 结算（9A）、discount 贴现（9D）、endorse 背书冲应付（9E）、return 退回（9C，另生成一张应收单，见 NotesProcReturn）；
    // 退回不收银行、贴现、背书的字段。
    // 应付票据（flag AP）只开放结算（9A，PJJAP）和退回（9C，CLAP，另生成一张应付单 P0）：写法按应收票据
    // 对称推断、未经实测，属第二级写入，只对测试账套开放（TestAccountGate，登录前和入队后各查一次）；贴现、背书没有样本，400（ApNotesOps）。
    internal static class NotesProcReq
    {
        public const string Path = "/u8co/v1/notes/process";
        public const string Action = "notes_process";
        const int NoteMax = 120;
        const int BankMax = 40;
        const int NameMax = 100;
        const int PartnerMax = 20;
        const decimal AmountMax = 1000000000000m;
        // 应付票据的贴现、背书（及其取消、制单）不开放时的统一用语（arap/process/cancel、arap/process/voucher 同用）。
        internal const string ApNotesOps = "应付票据只支持结算（settle）、退回（return），贴现、背书请在 U8 客户端处理";
        internal const string ApTestOnly = "应付票据处理只对配置为测试账套的账套开放（应付票据写入只在测试账套上验证过）";
        // 路由的字段表（RequestsP4.P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[]
        {
            Path, "flag", "op", "note", "amount", "sub_start", "sub_end", "bank_code", "bank_name", "expense", "interest", "rate",
            "vendor", "ap_lines", "digest"
        };
        static readonly string[] Ops = new string[] { "settle", "discount", "endorse", "return" };
        static readonly string[] ApTypes = new string[] { "01", "02", "P0" };

        public static bool IsPath(string path)
        {
            return path == Path;
        }

        public static NotesProcAsk Parse(Dictionary<string, object> body)
        {
            NotesProcAsk ask = new NotesProcAsk();
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR（应收票据）或 AP（应付票据）", "flag");
            }
            ask.Op = OpOf(Requests.Field(body, "op"));
            if (ask.Flag == "AP" && ask.Op != "settle" && ask.Op != "return")
            {
                throw Bad(ApNotesOps, "op");
            }
            Note(ask, Requests.Field(body, "note"));
            object amount = Requests.Field(body, "amount");
            ask.Amount = amount == null ? (decimal?)null : Money(amount, "amount", false);
            Range(ask, body);
            Bank(ask, body);
            Discount(ask, body);
            Endorse(ask, body);
            ask.Digest = Digest(Requests.Field(body, "digest"));
            return ask;
        }

        static string OpOf(object raw)
        {
            string op = raw as string;
            if (op == null || Array.IndexOf(Ops, op) < 0)
            {
                throw Bad("op 只能是 settle（托收 / 结算）、discount（贴现）、endorse（背书）或 return（退回）", "op");
            }
            return op;
        }

        // note：票据号（字符串）或票据主键（整数）。
        static void Note(NotesProcAsk ask, object raw)
        {
            if (raw is int || raw is long)
            {
                ask.NoteId = ArapWriteoffReq.Id(raw, "note", true);
                return;
            }
            ask.NoteCode = Code(raw, "note", "票据号或票据主键", NoteMax, true);
        }

        // sub_start / sub_end 成对出现，start ≤ end；同时给了 amount 时区间的张数（每 0.01 元一个号）必须与金额一致。
        static void Range(NotesProcAsk ask, Dictionary<string, object> body)
        {
            object start = Requests.Field(body, "sub_start");
            object end = Requests.Field(body, "sub_end");
            if (start == null && end == null)
            {
                return;
            }
            if (start == null || end == null)
            {
                throw Bad("sub_start 与 sub_end 要同时给出", start == null ? "sub_start" : "sub_end");
            }
            ask.SubStart = Serial(start, "sub_start");
            ask.SubEnd = Serial(end, "sub_end");
            if (ask.SubStart > ask.SubEnd)
            {
                throw Bad("sub_start 不能大于 sub_end", "sub_end");
            }
            if (ask.Amount != null && NotesProcRule.RangeAmount(ask.SubStart, ask.SubEnd) != ask.Amount.Value)
            {
                throw Bad("amount 与子票区间的金额不一致（区间每个号 0.01 元）", "amount");
            }
        }

        static long Serial(object raw, string field)
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
                throw Bad(field + " 必须是整数", field);
            }
            if (value < 1 || value > 999999999999999L)
            {
                throw Bad(field + " 无效", field);
            }
            return value;
        }

        // bank_code（结算 / 贴现必填）、bank_name（可选，缺省取科目上级的名称）；其他 op 不收。
        static void Bank(NotesProcAsk ask, Dictionary<string, object> body)
        {
            object code = Requests.Field(body, "bank_code");
            object name = Requests.Field(body, "bank_name");
            bool need = ask.Op == "settle" || ask.Op == "discount";
            if (!need)
            {
                Absent(code, "bank_code", "结算、贴现");
                Absent(name, "bank_name", "结算、贴现");
                return;
            }
            ask.BankCode = Code(code, "bank_code", "结算银行科目", BankMax, true);
            ask.BankName = name == null ? "" : Code(name, "bank_name", "银行名称", NameMax, true);
        }

        // 贴现：expense（贴现息、手续费，≥0）、interest（票据利息，≥0）、rate（贴现率 %，≥0）；净额 = 金额 + interest - expense（NotesProcRule.Net）。
        static void Discount(NotesProcAsk ask, Dictionary<string, object> body)
        {
            object expense = Requests.Field(body, "expense");
            object interest = Requests.Field(body, "interest");
            object rate = Requests.Field(body, "rate");
            if (ask.Op != "discount")
            {
                Absent(expense, "expense", "贴现");
                Absent(interest, "interest", "贴现");
                Absent(rate, "rate", "贴现");
                return;
            }
            ask.Expense = expense == null ? 0m : Money(expense, "expense", true);
            ask.Interest = interest == null ? 0m : Money(interest, "interest", true);
            if (rate != null)
            {
                decimal value;
                if (!ArapWriteoffReq.TryNum(rate, out value) || value < 0m || value > 100m || decimal.Round(value, 6) != value)
                {
                    throw Bad("rate 必须在 0 到 100 之间、最多六位小数", "rate");
                }
                ask.Rate = value;
            }
        }

        // 背书：vendor（被背书的供应商）、ap_lines（要冲的应付单据 01 / 02 / P0，同转账的写法）；合计必须等于背书金额。
        static void Endorse(NotesProcAsk ask, Dictionary<string, object> body)
        {
            object vendor = Requests.Field(body, "vendor");
            object lines = Requests.Field(body, "ap_lines");
            if (ask.Op != "endorse")
            {
                Absent(vendor, "vendor", "背书");
                Absent(lines, "ap_lines", "背书");
                return;
            }
            ask.Vendor = Code(vendor, "vendor", "供应商编码", PartnerMax, true);
            IList list = lines as IList;
            if (list != null)
            {
                foreach (object item in list)
                {
                    Dictionary<string, object> map = item as Dictionary<string, object>;
                    string type = map == null ? null : Requests.Field(map, "type") as string;
                    if (type == "49")
                    {
                        throw Bad("背书不支持冲付款单（49），请在 U8 客户端处理", "ap_lines");
                    }
                }
            }
            ask.ApLines = ArapTransferReq.Lines(lines, "ap_lines", ApTypes);
            decimal sum = Total(ask.ApLines);
            if (ask.Amount != null && ask.Amount.Value != sum)
            {
                throw Bad("ap_lines 的金额合计必须等于背书金额 amount", "ap_lines");
            }
            if (ask.SubStart > 0 && NotesProcRule.RangeAmount(ask.SubStart, ask.SubEnd) != sum)
            {
                throw Bad("ap_lines 的金额合计必须等于子票区间的金额", "ap_lines");
            }
        }

        internal static decimal Total(List<TransferAskLine> lines)
        {
            decimal sum = 0m;
            foreach (TransferAskLine line in lines)
            {
                sum += line.Amount;
            }
            return sum;
        }

        static void Absent(object raw, string field, string ops)
        {
            if (raw != null)
            {
                throw Bad(field + " 只用于" + ops, field);
            }
        }

        // 金额：最多两位小数、不超过 1000000000000；zero 为 true 时可以是 0，否则必须大于 0。
        static decimal Money(object raw, string field, bool zero)
        {
            decimal value;
            bool ok = ArapWriteoffReq.TryNum(raw, out value) && value <= AmountMax && decimal.Round(value, 2) == value
                && (zero ? value >= 0m : value > 0m);
            if (!ok)
            {
                throw Bad(field + (zero ? " 必须不小于 0" : " 必须大于 0") + "、不超过 1000000000000、最多两位小数", field);
            }
            return value;
        }

        static string Code(object raw, string field, string title, int max, bool required)
        {
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 && required)
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
            if (text == null || HasControl(text) || text.Trim().Length > ArapVoucherReq.DigestMax)
            {
                throw Bad("digest 必须是不含控制字符、最多 " + ArapVoucherReq.DigestMax.ToString(CultureInfo.InvariantCulture)
                    + " 字的字符串", "digest");
            }
            return text.Trim();
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

        // 锁键："arap:writeoff:<flag>"（与该侧的核销、转账、并账、取消处理串行，取消票据处理也持这个键）；
        // 背书另加 "arap:writeoff:AP"（扣应付单据余额）；退回另加 "new:ar_bill" / "new:ap_bill"（新应收 / 应付单取号，与新增应收 / 应付单串行）。
        // 请求体不合法时（登录前已 400）返回空。不是本路由返回 null。
        public static string[] LockKeysOf(string path, Dictionary<string, object> body)
        {
            if (!IsPath(path))
            {
                return null;
            }
            try
            {
                NotesProcAsk ask = Parse(body);
                if (ask.Op == "endorse")
                {
                    return new string[] { "arap:writeoff:AP", "arap:writeoff:AR" };
                }
                if (ask.Op == "return")
                {
                    return new string[] { "arap:writeoff:" + ask.Flag, "new:" + BillKind(ask.Flag) };
                }
                return new string[] { "arap:writeoff:" + ask.Flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        // 退回生成的单据类型：应收票据生成应收单 ar_bill（R0），应付票据生成应付单 ap_bill（P0）。
        public static string BillKind(string flag)
        {
            return flag == "AP" ? "ap_bill" : "ar_bill";
        }

        // 应付票据是第二级写入：账套不在测试账套名单里 403 test_account_only。应收票据什么也不做。
        public static void TestGate(WorkItem item, string flag)
        {
            if (flag == "AP")
            {
                TestAccountGate.Require(item, ApTestOnly);
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // notes/process 接到请求分派（Requests：挂在核销一组，不带 type、id）上的部分。
    internal static partial class Requests
    {
        static bool IsNotesProc(string path)
        {
            return NotesProcReq.IsPath(path);
        }

        // 是票据处理路由时校验并定登录子系统（= flag），返回 true；否则返回 false。
        static bool ApplyNotesProc(Dictionary<string, object> body, WorkItem item)
        {
            if (!NotesProcReq.IsPath(item.Path))
            {
                return false;
            }
            NotesProcAsk ask = NotesProcReq.Parse(body);
            NotesProcReq.TestGate(item, ask.Flag);
            item.SubId = ask.Flag;
            CoRows.Note(item, NotesProcRule.Title(ask.Op) + " " + (ask.NoteCode ?? ask.NoteId.ToString(CultureInfo.InvariantCulture)));
            return true;
        }
    }

    // 票据处理（notes/process）。功能权限按 U8「票据处理」AR2402 / AP2402 下的子项分操作登记（按 U8 授权目录核对），
    // 另收「票据录入」AR0504 / AP0504：应收结算 AR240203（另收票据收款 AR240201）、贴现 AR240205、背书 AR240206、
    // 退回 AR240207（票据退票）或 AR240202（票据转出，退回即把余额转回应收单）；应付结算 AP240203（另收票据付款 AP240201）、
    // 退回 AP240202（票据转出）。应付不开放贴现、背书（解析时已拒绝）。
    // 数据权限：票据的客户（AP_Note.cEndorser）/ 供应商、部门、业务员；背书另按供应商规则查每张应付单据（同转账）。
    internal static partial class PermRegistry
    {
        public static string NotesProcKey(string flag, string op)
        {
            return "write:notes:process:" + (flag == "AP" ? "ap" : "ar") + ":" + op;
        }

        static PermRule[] NotesProcRules()
        {
            return new PermRule[]
            {
                NoteProc("AR", "settle", A("AR240203", "AR240201", "AR0504")),
                NoteProc("AR", "discount", A("AR240205", "AR0504")),
                NoteProc("AR", "endorse", A("AR240206", "AR0504")),
                NoteProc("AR", "return", A("AR240207", "AR240202", "AR0504")),
                NoteProc("AP", "settle", A("AP240203", "AP240201", "AP0504")),
                NoteProc("AP", "return", A("AP240202", "AP0504"))
            };
        }

        static PermRule NoteProc(string flag, string op, string[] auths)
        {
            bool ap = flag == "AP";
            return R(NotesProcKey(flag, op), (ap ? "应付" : "应收") + NotesProcRule.Title(op), auths,
                WriteoffObjs(ap ? PermObj.Vendor : PermObj.Customer));
        }
    }
}
