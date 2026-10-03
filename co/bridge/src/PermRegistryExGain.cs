namespace U8Co
{
    // 应收 / 应付汇兑损益（arap/exchange_gain、arap/exchange_gain/cancel，ArapExGain / ArapExGainCancel 用 ForKey(ExGainKey(…)) 取）。
    // 汇兑损益 AR0507 / AP0507（已按 U8 授权目录核对）；取消在 U8「取消操作」窗体（与取消核销同一个），
    // 按「取消操作」AR0807 / AP0807（更正，原先的 AR050203 / AR0502 是收款单删除 / 录入）。
    // 数据权限：客户 / 供应商受控时按往来单位逐个判断（在 ArapExGain / ArapExGainCancel 里），这里不另登记对象。
    internal static partial class PermRegistry
    {
        public static string ExGainKey(string flag, bool cancel)
        {
            return "write:arap:" + (cancel ? "exchange_gain_cancel:" : "exchange_gain:") + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] ExGainRules()
        {
            return new PermRule[]
            {
                R(ExGainKey("AR", false), "应收汇兑损益", A("AR0507")),
                R(ExGainKey("AP", false), "应付汇兑损益", A("AP0507")),
                R(ExGainKey("AR", true), "应收取消汇兑损益", A("AR0807")),
                R(ExGainKey("AP", true), "应付取消汇兑损益", A("AP0807"))
            };
        }
    }
}
