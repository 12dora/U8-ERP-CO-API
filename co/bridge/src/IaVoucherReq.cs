using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // ia/voucher 的一张单据：存货核算的单据类型（IA_Subsidiary.cVouType，如 01、09、3201）和来源单据表头主键
    // （出入库单 RdRecordNN.ID，回冲单 24 / 30 是原采购入库单的 ID，出入库调整单 JustInVouch.id）。
    internal sealed class IaBillRef
    {
        public string Type = "";
        public int Id;
    }

    // ia/voucher（存货核算制单）的请求。ids 与 month 二选一；types 只和 month 一起用。
    internal sealed class IaVoucherAsk
    {
        public List<IaBillRef> Ids = new List<IaBillRef>();
        // 按月份选单据：该月已记账、未制单的单据；0 表示按 ids 选。
        public int Month;
        // 按月份选单据时只要这些类型；空表示全部支持的类型。
        public List<string> Types = new List<string>();
        // true：全部单据合成一张凭证（U8「合并制单」AllToOne）；false：一张单据一张凭证（OneToOne，缺省）。
        public bool All;
        // 凭证类别；空串表示按账套最近一张存货核算凭证的类别，没有时「转」。
        public string Sign = "";
        // 摘要；空串表示按单据取（单据备注，没有备注时单据类型名，同 U8）。
        public string Digest = "";
        // 制单日期；空串表示登录日期。
        public string Date = "";
    }

    // ia/voucher/delete（存货核算取消制单）的请求：凭证的外部业务号（GL_accvouch.coutno_id = IA_Subsidiary.cPZID）。
    internal sealed class IaVoucherDropAsk
    {
        public string PzId = "";
    }

    // 存货核算制单、取消制单在登录前的校验（400）、锁键和常量。处理在 IaVoucher、IaVoucherDrop。
    // 第二级写入：只对测试账套开放（TestAccountGate）。登录子系统 IA。
    internal static class IaVoucherReq
    {
        public const string Path = "/u8co/v1/ia/voucher";
        public const string DropPath = "/u8co/v1/ia/voucher/delete";
        public const string Action = "ia_voucher";
        public const string DropAction = "ia_voucher_delete";
        public const string TestOnly = "存货核算制单只对配置为测试账套的账套开放";
        public const string DropTestOnly = "存货核算取消制单只对配置为测试账套的账套开放";
        public const string PerBill = "per_bill";
        public const string AllBills = "all";
        // 一次最多的单据数：合并成一张凭证时 BillMax；逐张制单每张单据一次凭证导入，限 PerBillMax 张（HTTP 等待 75 秒内做完）。
        public const int BillMax = 100;
        public const int PerBillMax = 10;
        public const int DigestMax = 120;
        // 存货核算凭证的外部业务号：年度 4 位 + IA + 13 位（Ap_CancelNo 的 PZ / IA 号，同 U8）。
        static readonly Regex PzRe = new Regex("^[0-9]{4}IA[0-9]{13}\\z", RegexOptions.CultureInvariant);
        static readonly Regex TypeRe = new Regex("^[0-9]{2,4}\\z", RegexOptions.CultureInvariant);
        // 新凭证编号锁：没给 sign 时类别要登录后才知道，常见类别都锁（与期间损益结转同一份 GlTransferReq.Signs）；
        // 登录后算出的缺省类别不在其中时 409，请用 sign 指定（IaVoucherPlan.Sign）。
        internal static string[] Signs
        {
            get { return GlTransferReq.Signs; }
        }

        // 登录前的字段名单（RequestsSpecs 的 ExtraSpecs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[]
        {
            Path, "ids", "month", "types", "merge", "sign", "digest", "voucher_date"
        };
        internal static readonly string[] DropSpec = new string[] { DropPath, "pz_id" };

        public static bool IsPath(string path)
        {
            return path == Path || path == DropPath;
        }

        public static string ActionOf(string path)
        {
            return path == DropPath ? DropAction : Action;
        }

        public static string TestOnlyOf(string path)
        {
            return path == DropPath ? DropTestOnly : TestOnly;
        }

        // 权限规则键（PermRegistryIa）。
        public static string RuleKey(bool drop)
        {
            return drop ? "write:ia:voucher_delete" : "write:ia:voucher";
        }

        public static IaVoucherAsk Parse(Dictionary<string, object> body)
        {
            IaVoucherAsk ask = new IaVoucherAsk();
            object ids = Requests.Field(body, "ids");
            object month = Requests.Field(body, "month");
            if ((ids == null) == (month == null))
            {
                throw Bad("ids（按单据）与 month（按月份）必须给且只能给一个", ids == null ? "ids" : "month");
            }
            Select(ask, body, ids, month);
            ask.All = Merge(Requests.Field(body, "merge"));
            if (!ask.All && ask.Ids.Count > PerBillMax)
            {
                throw Bad("逐张制单（merge=per_bill）一次最多 " + Int(PerBillMax) + " 张单据；更多请分批，或 merge=all 合成一张凭证", "ids");
            }
            object sign = Requests.Field(body, "sign");
            ask.Sign = sign == null ? "" : GlReq.SignText(sign, "sign");
            ask.Digest = Digest(Requests.Field(body, "digest"));
            ask.Date = GlReq.OptDate(Requests.Field(body, "voucher_date"), "voucher_date");
            return ask;
        }

        // 选单据：ids，或 month（加可选的 types）。
        static void Select(IaVoucherAsk ask, Dictionary<string, object> body, object ids, object month)
        {
            if (ids != null)
            {
                ask.Ids = Ids(ids);
                if (Requests.Field(body, "types") != null)
                {
                    throw Bad("types 只能和 month 一起用", "types");
                }
                return;
            }
            int value = month is int ? (int)month : 0;
            if (value < 1 || value > 12)
            {
                throw Bad("month 必须是 1 到 12 的整数", "month");
            }
            ask.Month = value;
            ask.Types = Types(Requests.Field(body, "types"));
        }

        public static IaVoucherDropAsk ParseDrop(Dictionary<string, object> body)
        {
            string no = Requests.Field(body, "pz_id") as string;
            if (no == null || !PzRe.IsMatch(no))
            {
                throw Bad("pz_id 必须是存货核算凭证的外部业务号：4 位年度、IA、13 位数字（如 2026IA0000000000001）", "pz_id");
            }
            IaVoucherDropAsk ask = new IaVoucherDropAsk();
            ask.PzId = no;
            return ask;
        }

        // 锁键："period:ia"（与存货核算记账、期末处理、月末结账串行）、"ia:voucher"（制单与取消制单串行），
        // 制单另锁新凭证编号 "new:gl:<类别>"（与 gl/vouchers/create 互斥；没给 sign 时常见类别都锁）。请求体不合法时返回空。
        public static string[] LockKeys(string path, Dictionary<string, object> body)
        {
            try
            {
                List<string> keys = new List<string>();
                keys.Add("period:ia");
                keys.Add("ia:voucher");
                if (path == DropPath)
                {
                    ParseDrop(body);
                    return keys.ToArray();
                }
                IaVoucherAsk ask = Parse(body);
                foreach (string sign in ask.Sign.Length > 0 ? new string[] { ask.Sign } : Signs)
                {
                    keys.Add("new:gl:" + sign);
                }
                return keys.ToArray();
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        // 审计备注。
        public static string Title(IaVoucherAsk ask)
        {
            string how = ask.All ? "合并" : "逐张";
            if (ask.Month > 0)
            {
                return "存货核算制单 " + Int(ask.Month) + " 月" + (ask.Types.Count > 0 ? " " + string.Join(",", ask.Types.ToArray()) : "") + " " + how;
            }
            return "存货核算制单 " + Int(ask.Ids.Count) + " 张 " + how;
        }

        // ids：1 到 BillMax 个 {type, id}，不重复。
        static List<IaBillRef> Ids(object value)
        {
            IList list = value as IList;
            if (list == null || list.Count < 1 || list.Count > BillMax)
            {
                throw Bad("ids 必须是 1 到 " + Int(BillMax) + " 项的数组", "ids");
            }
            List<IaBillRef> refs = new List<IaBillRef>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < list.Count; i++)
            {
                string at = FieldPath.Item("ids", i);
                IaBillRef one = Ref(list[i], at);
                if (!seen.Add(one.Type + ":" + Int(one.Id)))
                {
                    throw Bad("ids 有重复的单据", at);
                }
                refs.Add(one);
            }
            return refs;
        }

        static IaBillRef Ref(object value, string at)
        {
            Dictionary<string, object> one = value as Dictionary<string, object>;
            if (one == null || one.Count != 2 || !one.ContainsKey("type") || !one.ContainsKey("id"))
            {
                throw Bad("ids 的每一项必须是 {type, id}", at);
            }
            IaBillRef r = new IaBillRef();
            r.Type = TypeCode(Requests.Field(one, "type"), FieldPath.Join(at, "type"));
            object id = Requests.Field(one, "id");
            long number = id is int ? (int)id : (id is long ? (long)id : 0);
            if (number < 1 || number > int.MaxValue)
            {
                throw Bad("id 必须是正整数（来源单据表头主键）", FieldPath.Join(at, "id"));
            }
            r.Id = (int)number;
            return r;
        }

        static List<string> Types(object value)
        {
            List<string> types = new List<string>();
            if (value == null)
            {
                return types;
            }
            IList list = value as IList;
            if (list == null || list.Count < 1 || list.Count > 20)
            {
                throw Bad("types 必须是 1 到 20 个单据类型编码的数组", "types");
            }
            for (int i = 0; i < list.Count; i++)
            {
                string code = TypeCode(list[i], FieldPath.Item("types", i));
                if (!types.Contains(code))
                {
                    types.Add(code);
                }
            }
            return types;
        }

        // 存货核算单据类型编码（IA_Subsidiary.cVouType）：2 到 4 位数字。是否支持在登录后判断（409）。
        static string TypeCode(object value, string at)
        {
            string text = value as string;
            if (text == null || !TypeRe.IsMatch(text))
            {
                throw Bad("单据类型必须是存货核算的单据类型编码（2 到 4 位数字，如 01、09、3201）", at);
            }
            return text;
        }

        static bool Merge(object value)
        {
            if (value == null)
            {
                return false;
            }
            string text = value as string;
            if (text != PerBill && text != AllBills)
            {
                throw Bad("merge 只能是 per_bill（一张单据一张凭证，缺省）或 all（全部单据一张凭证）", "merge");
            }
            return text == AllBills;
        }

        static string Digest(object value)
        {
            if (value == null)
            {
                return "";
            }
            string text = value as string;
            if (text == null)
            {
                throw Bad("digest 必须是字符串", "digest");
            }
            text = text.Trim();
            if (text.Length == 0 || text.Length > DigestMax)
            {
                throw Bad("digest 必须是 1 到 " + Int(DigestMax) + " 字", "digest");
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    throw Bad("digest 不能含控制字符", "digest");
                }
            }
            return text;
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
