using System.Text;

namespace U8Co
{
    // 固定资产的 U8 官方 EAI 报文（经 EaiDistribute，即 U8Distribute.iDistribute.ProcessEx）。纯函数，--selftest 覆盖。
    // 卡片 roottag capitalasserts（EAIFaCards.clsFaCards，ImOperation 列出 add / edit / delete / empty）：新增发表头和一个部门 entry；
    // 删除只发表头的 assetno（模板说明），U8 只删本期录入、没有后续版本的卡片，这里用作「撤销新增」，不是资产减少。
    // 变动单（roottag capitalvouchers）不用：本机 U8 的 EAI 没有它的导入样式表，导入回「未设置对象变量」（AS、FA 登录都一样）；
    // 资产减少没有 roottag。两者都在 U8 客户端处理。标签次序照 Template\CapitalAsserts.xml。assetno 对应 fa_Cards.sAssetNum，不是卡片编号 sCardNum。
    internal static class FaCardEai
    {
        internal const string CardRoot = "capitalasserts";

        static readonly string[] HeadOrder = new string[]
        {
            "assetno", "assetname", "typeno", "originalvalue", "startusedate", "currency", "exchangerate", "accdepr", "usedmonths",
            "accountaddmannerno", "status", "depreciationmanner", "life", "netleftvalue", "netleftvaluerate", "style", "reservesite",
            "decvalue", "skeeper"
        };

        // 新增：currency 是本位币名称（桥补），汇率 1；部门一个，比例 1。
        internal static string Add(FaCardPlan plan, string currency)
        {
            StringBuilder sb = EaiDistribute.Begin(CardRoot, "add");
            sb.Append("<header>");
            for (int i = 0; i < HeadOrder.Length; i++)
            {
                string tag = HeadOrder[i];
                string value;
                if (tag == "currency")
                {
                    value = currency;
                }
                else if (tag == "exchangerate")
                {
                    value = "1";
                }
                else if (!plan.Tags.TryGetValue(tag, out value))
                {
                    continue;
                }
                ArcPartnerXml.Tag(sb, tag, value);
            }
            sb.Append("</header><body><entry>");
            ArcPartnerXml.Tag(sb, "assetno", plan.AssetNum);
            ArcPartnerXml.Tag(sb, "deptno", plan.Dept);
            ArcPartnerXml.Tag(sb, "deptscale", "1");
            sb.Append("</entry></body>");
            return EaiDistribute.End(sb, CardRoot);
        }

        // 撤销新增：模板说明删除只填表头的资产编号。
        internal static string Delete(string assetNum)
        {
            StringBuilder sb = EaiDistribute.Begin(CardRoot, "delete");
            sb.Append("<header>");
            ArcPartnerXml.Tag(sb, "assetno", assetNum);
            sb.Append("</header>");
            return EaiDistribute.End(sb, CardRoot);
        }
    }
}
