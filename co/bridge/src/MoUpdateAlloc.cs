using System;
using System.Globalization;

namespace U8Co
{
    // 改了行数量时子件数量的算法，与 U8 界面改数量后保存的结果一致（测试账套实测核对）：
    //   系数 f = (1 + CompScrap/100)；变动用量（FVFlag=1）且不是返工订单（MoClass≠2）时再除以 (1 − ParentScrap/100)；
    //   单位用量 base = BaseQtyN / BaseQtyD × f（有辅计量用量 AuxBaseQtyN 且换算率非 0 时 = AuxBaseQtyN / BaseQtyD × f × 换算率）；
    //   数量 = 变动用量 ? 行数量 × base : base（固定用量不随行数量变），按存货数量小数位四舍五入；
    //   件数 = 未舍入数量 / 换算率，按件数小数位。
    // U8 在 AuxBaseQtyN × 换算率（按数量小数位舍入）与 BaseQtyN 不一致时按存货的「BOM 展开单位」分支，桥读不到这项设置；这种子件、有换算率却没有
    // AuxBaseQtyN 的子件、母件损耗率不小于 100 的子件，改数量时 409，请到 U8 客户端改。回读核对用同一个函数。
    internal static class MoUpdateAlloc
    {
        const decimal Eps = 0.000001m;

        public static void Scale(MoAllocTarget t, MoLineRow line, decimal qty, int qtyDigits, int auxDigits)
        {
            MoAllocRow a = t.Row;
            bool variety = a.FVFlag == "1";
            decimal f = 1m + a.CompScrap / 100m;
            if (variety && line.MoClass != "2")
            {
                if (a.ParentScrap >= 100m)
                {
                    throw Refuse(line, a, "母件损耗率不小于 100");
                }
                f = f / (1m - a.ParentScrap / 100m);
            }
            if (a.BaseD <= 0m)
            {
                throw Refuse(line, a, "基本用量分母不大于 0");
            }
            bool aux = a.ChangeRate != 0m;
            decimal unit = a.BaseN / a.BaseD * f;
            if (aux)
            {
                CheckAux(line, a, qtyDigits);
                decimal byAux = a.AuxBaseN / a.BaseD * f * a.ChangeRate;
                // 主用量只与舍入后的辅用量 × 换算率相等时，U8 按「BOM 展开单位」走主或辅两条算法，桥读不到这项设置：两条算出的数量、件数都相同才改，否则 409。
                if (Round(Raw(variety, qty, unit), qtyDigits) != Round(Raw(variety, qty, byAux), qtyDigits)
                    || Round(Raw(variety, qty, unit) / a.ChangeRate, auxDigits) != Round(Raw(variety, qty, byAux) / a.ChangeRate, auxDigits))
                {
                    throw Refuse(line, a, "按主计量和辅计量算出的新数量不同（取决于存货的 BOM 展开单位）");
                }
                unit = byAux;
            }
            decimal raw = Raw(variety, qty, unit);
            t.Qty = Round(raw, qtyDigits);
            t.AuxQty = aux ? Round(raw / a.ChangeRate, auxDigits) : -1m;
        }

        static decimal Raw(bool variety, decimal qty, decimal unit)
        {
            return variety ? qty * unit : unit;
        }

        // U8 由辅计量用量推主用量时按存货数量小数位舍入（实测：辅 1 × 换算率 2.345678 存成主 2.35），与原值或舍入后相等都算自洽。
        static void CheckAux(MoLineRow line, MoAllocRow a, int qtyDigits)
        {
            if (a.AuxBaseN == 0m)
            {
                throw Refuse(line, a, "有换算率却没有辅计量用量");
            }
            decimal exact = a.AuxBaseN * a.ChangeRate;
            if (Math.Abs(a.BaseN - exact) > Eps && Math.Abs(a.BaseN - Round(exact, qtyDigits)) > Eps)
            {
                throw Refuse(line, a, "辅计量用量与主用量不一致（取决于存货的 BOM 展开单位）");
            }
        }

        static BridgeException Refuse(MoLineRow line, MoAllocRow a, string why)
        {
            return new BridgeException(409, "state_mismatch", "第 " + line.SortSeq.ToString(CultureInfo.InvariantCulture)
                + " 行子件 " + a.InvCode + " " + why + "，桥不能按新数量重算，请在 U8 客户端修改数量");
        }

        internal static decimal Round(decimal value, int digits)
        {
            return decimal.Round(value, digits, MidpointRounding.AwayFromZero);
        }

        internal static string Num(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }
    }
}
