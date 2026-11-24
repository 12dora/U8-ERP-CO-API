using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单明细行上的 substitutes（替代料）和 locators（定位符）数组的登录前校验（BomReq.Field 调用）。字段名不分大小写。
    // 替代料每项：inv_code 必填；sequence（优先级 1 到 10000，缺省 1）、factor（替代系数，大于 0、最多 6 位小数，缺省 1）、
    // replace_flag（布尔，缺省 false）、eff_begin / eff_end（yyyy-MM-dd，缺省取子件行的起止日期）可省。同一行里存货不能重复（U8 1011809）。
    // 定位符每项：loc 必填，最长 255，同一行里不能重复。各最多 50 项；空数组表示清空。
    internal static class BomSubReq
    {
        public const int ItemsMax = 50;
        const int LocMax = 255;
        static readonly string[] SubKeys = new string[] { "inv_code", "sequence", "factor", "replace_flag", "eff_begin", "eff_end" };
        static readonly string[] LocKeys = new string[] { "loc" };

        // 返回 List<BomSub>（substitutes）或 List<string>（locators）。
        internal static object Parse(string key, object value)
        {
            IList items = value as IList;
            if (items == null)
            {
                throw BridgeException.BadField(key, key + " 必须是数组");
            }
            if (items.Count > ItemsMax)
            {
                throw BridgeException.BadField(key, key + " 不能超过 " + BomReq.Num(ItemsMax) + " 项");
            }
            return key == BomSubs.SubsKey ? (object)Subs(items) : Locs(items);
        }

        static List<BomSub> Subs(IList items)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            List<BomSub> subs = new List<BomSub>();
            for (int i = 0; i < items.Count; i++)
            {
                try
                {
                    BomSub sub = Sub(Item(items[i], BomSubs.SubsKey));
                    if (!seen.Add(sub.InvCode))
                    {
                        throw BridgeException.BadField("inv_code", "替代料存货重复：" + sub.InvCode);
                    }
                    subs.Add(sub);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, BomSubs.SubsKey, i);
                }
            }
            return subs;
        }

        static List<string> Locs(IList items)
        {
            List<string> locs = new List<string>();
            for (int i = 0; i < items.Count; i++)
            {
                try
                {
                    Dictionary<string, object> item = Item(items[i], BomSubs.LocsKey);
                    BomReq.Only(item, LocKeys);
                    string loc = BomReq.Str(item, "loc", LocMax, true);
                    if (locs.Contains(loc))
                    {
                        throw BridgeException.BadField("loc", "定位符重复：" + loc);
                    }
                    locs.Add(loc);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, BomSubs.LocsKey, i);
                }
            }
            return locs;
        }

        static Dictionary<string, object> Item(object raw, string key)
        {
            Dictionary<string, object> item = raw as Dictionary<string, object>;
            if (item == null)
            {
                throw new BridgeException(400, "bad_request", key + " 的每一项必须是对象");
            }
            return item;
        }

        static BomSub Sub(Dictionary<string, object> item)
        {
            BomReq.Only(item, SubKeys);
            BomSub sub = new BomSub();
            sub.InvCode = BomReq.Str(item, "inv_code", 60, true);
            object seq = MfgReq.Raw(item, "sequence");
            if (seq != null)
            {
                sub.Seq = BomReq.Int(seq, "sequence", 1, 10000);
            }
            object factor = MfgReq.Raw(item, "factor");
            if (factor != null)
            {
                sub.Factor = BomReq.Qty(factor, "factor");
            }
            object flag = MfgReq.Raw(item, "replace_flag");
            if (flag != null)
            {
                sub.Replace = BomReq.Flag(flag, "replace_flag");
            }
            if (MfgReq.Raw(item, "eff_begin") != null)
            {
                sub.EffBeg = BomReq.Date(item, "eff_begin");
            }
            if (MfgReq.Raw(item, "eff_end") != null)
            {
                sub.EffEnd = BomReq.Date(item, "eff_end");
            }
            return sub;
        }
    }
}
