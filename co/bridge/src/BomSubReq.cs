using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单明细行上的 substitutes（替代料）和 locators（定位符）数组的登录前校验（BomReq.Field 调用）。字段名不分大小写。
    // 替代料的规则在 SubstituteReq（与生产订单子件共用；物料清单不收 defines，eff_begin / eff_end 缺省取子件行的起止日期）。
    // 定位符每项：loc 必填，最长 255，同一行里不能重复。各最多 50 项；空数组表示清空。
    internal static class BomSubReq
    {
        public const int ItemsMax = SubstituteReq.ItemsMax;
        const int LocMax = 255;
        static readonly string[] LocKeys = new string[] { "loc" };

        // 返回 List<BomSub>（substitutes）或 List<string>（locators）。
        internal static object Parse(string key, object value)
        {
            if (key == BomSubs.SubsKey)
            {
                return SubstituteReq.Parse(value, key, false);
            }
            IList items = value as IList;
            if (items == null)
            {
                throw BridgeException.BadField(key, key + " 必须是数组");
            }
            if (items.Count > ItemsMax)
            {
                throw BridgeException.BadField(key, key + " 不能超过 " + BomReq.Num(ItemsMax) + " 项");
            }
            return Locs(items);
        }

        static List<string> Locs(IList items)
        {
            List<string> locs = new List<string>();
            for (int i = 0; i < items.Count; i++)
            {
                try
                {
                    Dictionary<string, object> item = items[i] as Dictionary<string, object>;
                    if (item == null)
                    {
                        throw new BridgeException(400, "bad_request", BomSubs.LocsKey + " 的每一项必须是对象");
                    }
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
    }
}
