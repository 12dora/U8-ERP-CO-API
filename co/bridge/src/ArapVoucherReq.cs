using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // arap/voucher 的请求：一张单据（id）或同类型的 2 到 20 张单据（ids，合并制单）生成一张凭证。
    internal sealed class VoucherAsk
    {
        public string Flag;
        public string Kind;
        // 第一张单据的主键；合并制单时是 ids 的第一个。
        public int Id;
        // 全部单据主键，按请求顺序；单张时只有 Id 一个。
        public List<int> Ids = new List<int>();
        public bool Merge;
        // 凭证类别；空串表示按规则选（有银行 / 现金科目的收款单「收」、付款单「付」，其余「转」）。
        public string Sign;
        // 制单日期；空串表示单据日期。
        public string Date;
        // 摘要；空串表示按单据取（往来明细上的摘要，没有就「销售 / 购 / 收 / 付」加往来单位名称）。
        public string Digest;
        // 调用方指定的现金流量项目（科目编码 → 项目编码，ArapCashItems）；没给是空表。
        public CashItemMap CashItems = new CashItemMap();
    }

    // arap/voucher/delete 的请求：按凭证的外部业务号（Ar_Detail / Ap_Detail.cPZid = GL_accvouch.coutno_id）取消制单。
    internal sealed class VoucherDropAsk
    {
        public string Flag;
        public string PzId;
    }

    // arap/voucher（制单）、arap/voucher/delete（取消制单）在登录前的校验（400）。登录子系统就是 flag（AR / AP）。
    // 制单：flag、type（sale_invoice / purchase_invoice / ar_receipt / ap_payment / ar_bill / ap_bill，另有退款单
    // ar_refund / ap_refund，须与 flag 同侧）、
    // id（单据主键），可选 sign、voucher_date、digest、cash_items（ArapCashItems）。请求里的 date 是登录日期（公共字段），制单日期另用 voucher_date。
    // 合并制单：ids = [{type, id}, …] 代替 id（两者只能给一个），2 到 20 张、type 都与顶层 type 相同、不重复。
    internal static class ArapVoucherReq
    {
        public const string Action = "arap_voucher";
        public const string DropAction = "arap_voucher_delete";
        public const int DigestMax = 120;
        public const int MergeMax = 20;
        static readonly string[] AskKeys = new string[] { "flag", "type", "id", "ids", "sign", "voucher_date", "digest", "cash_items" };
        static readonly string[] DropKeys = new string[] { "flag", "pz_id" };
        static readonly Regex PzId = new Regex("^(AR|AP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        internal static string[] Fields(bool drop)
        {
            return (string[])(drop ? DropKeys : AskKeys).Clone();
        }

        // 单据类型 → AR / AP；不支持制单的返回 null。
        public static string FlagOf(string kind)
        {
            switch (kind)
            {
                case "sale_invoice":
                case "ar_receipt":
                case "ar_bill":
                case "ar_refund":
                    return "AR";
                case "purchase_invoice":
                case "ap_payment":
                case "ap_bill":
                case "ap_refund":
                    return "AP";
                default:
                    return null;
            }
        }

        // meta 的 ops.arap_voucher。
        public static bool Handles(VoucherKind kind)
        {
            return kind != null && FlagOf(kind.Name) != null;
        }

        public static VoucherAsk Parse(Dictionary<string, object> body)
        {
            VoucherAsk ask = new VoucherAsk();
            ask.Flag = Flag(body);
            ask.Kind = Requests.Field(body, "type") as string;
            string side = FlagOf(ask.Kind);
            if (side == null)
            {
                throw Bad("type 只能是 sale_invoice、purchase_invoice、ar_receipt、ap_payment、ar_bill、ap_bill、ar_refund、ap_refund", "type");
            }
            if (side != ask.Flag)
            {
                throw Bad("type 与 flag 不一致（应收 AR：sale_invoice、ar_receipt、ar_bill、ar_refund；应付 AP：purchase_invoice、ap_payment、ap_bill、ap_refund）",
                    "type");
            }
            object ids = Requests.Field(body, "ids");
            if (ids != null)
            {
                if (body.ContainsKey("id"))
                {
                    throw Bad("id 与 ids 只能给一个（一张单据用 id，合并制单用 ids）", "ids");
                }
                ask.Ids = Ids(ids, ask.Kind);
                ask.Merge = true;
            }
            else
            {
                ask.Ids.Add(Id(Requests.Field(body, "id"), "id"));
            }
            ask.Id = ask.Ids[0];
            object sign = Requests.Field(body, "sign");
            ask.Sign = sign == null ? "" : GlReq.SignText(sign, "sign");
            ask.Date = GlReq.OptDate(Requests.Field(body, "voucher_date"), "voucher_date");
            ask.Digest = Digest(Requests.Field(body, "digest"));
            ask.CashItems = ArapCashItems.Parse(Requests.Field(body, "cash_items"));
            return ask;
        }

        public static VoucherDropAsk ParseDrop(Dictionary<string, object> body)
        {
            VoucherDropAsk ask = new VoucherDropAsk();
            ask.Flag = Flag(body);
            string no = Requests.Field(body, "pz_id") as string;
            if (no == null || !PzId.IsMatch(no))
            {
                throw Bad("pz_id 必须是 AR 或 AP 后接数字的凭证外部业务号（如 AR0000000004727）", "pz_id");
            }
            if (no.Substring(0, 2) != ask.Flag)
            {
                throw Bad("pz_id 与 flag 不一致（AR 开头是应收、AP 开头是应付）", "pz_id");
            }
            ask.PzId = no;
            return ask;
        }

        // 制单：单据键 "<type>:<id>"、同类别凭证编号 "new:gl:<类别>"（与总账新增同一个键；没给 sign 时收、付、转都锁）、
        // "arap:voucher:<AR|AP>"。取消制单：凭证和单据入队前查不到，只锁 "arap:voucher:<AR|AP>"。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body, bool drop)
        {
            try
            {
                if (drop)
                {
                    return new string[] { "arap:voucher:" + ParseDrop(body).Flag };
                }
                VoucherAsk ask = Parse(body);
                List<string> keys = new List<string>();
                foreach (int id in ask.Ids)
                {
                    keys.Add(ask.Kind + ":" + id.ToString(CultureInfo.InvariantCulture));
                }
                string[] signs = ask.Sign.Length > 0 ? new string[] { ask.Sign } : ArapVoucherRule.Signs;
                foreach (string sign in signs)
                {
                    keys.Add("new:gl:" + sign);
                }
                keys.Add("arap:voucher:" + ask.Flag);
                return keys.ToArray();
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        static string Flag(Dictionary<string, object> body)
        {
            string flag = Requests.Field(body, "flag") as string;
            if (flag != "AR" && flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            return flag;
        }

        // 合并制单的请求带 ids（不是 JSON null）。公共校验据此不要求顶层 id。
        public static bool Merging(Dictionary<string, object> body)
        {
            return body != null && Requests.Field(body, "ids") != null;
        }

        // ids：JSON 数组，元素 {type, id}，2 到 MergeMax 个，type 与 kind 相同，id 不重复。
        internal static List<int> Ids(object value, string kind)
        {
            IList list = value as IList;
            if (list == null)
            {
                throw Bad("ids 必须是 JSON 数组", "ids");
            }
            if (list.Count < 2 || list.Count > MergeMax)
            {
                throw Bad("ids 必须是 2 到 " + MergeMax.ToString(CultureInfo.InvariantCulture) + " 张单据（一张单据请用 id）", "ids");
            }
            List<int> ids = new List<int>();
            for (int i = 0; i < list.Count; i++)
            {
                string at = FieldPath.Item("ids", i);
                int id = Ref(list[i], kind, at);
                if (ids.Contains(id))
                {
                    throw Bad("ids 有重复的单据", at);
                }
                ids.Add(id);
            }
            return ids;
        }

        static int Ref(object value, string kind, string at)
        {
            Dictionary<string, object> one = value as Dictionary<string, object>;
            if (one == null || one.Count != 2 || !one.ContainsKey("type") || !one.ContainsKey("id"))
            {
                throw Bad("ids 的每一项必须是 {type, id}", at);
            }
            if (Requests.Field(one, "type") as string != kind)
            {
                throw Bad("合并制单的单据类型必须都是 type（" + kind + "）", FieldPath.Join(at, "type"));
            }
            return Id(Requests.Field(one, "id"), FieldPath.Join(at, "id"));
        }

        static int Id(object value, string field)
        {
            long number;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw Bad("id 必须是整数", field);
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw Bad("id 无效", field);
            }
            return (int)number;
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
            if (text.Length > DigestMax)
            {
                throw Bad("digest 最多 " + DigestMax.ToString(CultureInfo.InvariantCulture) + " 字", "digest");
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

        static BridgeException Bad(string message)
        {
            return new BridgeException(400, "bad_request", message);
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
