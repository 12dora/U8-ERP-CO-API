using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace U8Co
{
    // arap/writeoff/cancel 的请求：一个核销号（HXAR… / HXAP…）整批取消。
    internal sealed class UnwriteoffAsk
    {
        public string Flag;
        public string CancelNo;
    }

    // arap/writeoff/cancel（取消核销）在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、cancel_no。
    // 核销号的前缀必须与 flag 一致：HXAR 是应收、HXAP 是应付。处理在 ArapUnwriteoff。
    internal static class ArapUnwriteoffReq
    {
        public const string Action = "writeoff_cancel";
        static readonly Regex CancelNo = new Regex("^HX(AR|AP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        public static UnwriteoffAsk Parse(Dictionary<string, object> body)
        {
            string flag = Requests.Field(body, "flag") as string;
            if (flag != "AR" && flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            string no = Requests.Field(body, "cancel_no") as string;
            if (no == null || !CancelNo.IsMatch(no))
            {
                throw Bad("cancel_no 必须是 HXAR 或 HXAP 后接数字的核销号", "cancel_no");
            }
            if (no.Substring(2, 2) != flag)
            {
                throw Bad("cancel_no 与 flag 不一致（HXAR 是应收、HXAP 是应付）", "cancel_no");
            }
            UnwriteoffAsk ask = new UnwriteoffAsk();
            ask.Flag = flag;
            ask.CancelNo = no;
            return ask;
        }

        // 锁键只有 "arap:writeoff:<AR|AP>"：收付款单和被核销单据要查库才知道，入队前拿不到，
        // 于是与同一侧的全部核销、取消核销串行（核销的锁键里也有这一个）。请求体不合法时（登录前已 400）返回空。
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
