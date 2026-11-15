using System.Collections.Generic;

namespace U8Co
{
    // 质量单据写路由（QmGen / QmDelete 用 ForKey 取）：write:<类型>:create|delete（检验单、其他报检单另有 update，QmEdit）；不良品处理单（QmRejOps）另有 verify|unverify。
    // 报检单不开放单独审核；产品报检单开放单独弃审 write:qm_product_inspect:unverify。删除已审核的报检单前要有
    // 弃审按钮权限（QmSpec.UnverifyAuth，QmDelete.Gate 直接查）。
    // 功能 id 见 QmSpec（UFMeta AA_FormButtonAuths 的卡片按钮）。数据权限按请求的每一行判断：供应商（来料必有）、部门、
    // 存货、仓库，列名是桥拼的行（QmInsSrc.PermRows / QmChkSrc.PermRows / QmDoc.PermRows），与读取规则的对象一致。
    // 未覆盖：U8 客户端对报检、检验按钮的记录级控制范围。
    internal static partial class PermRegistry
    {
        static PermRule[] QmWriteRules()
        {
            List<PermRule> rules = new List<PermRule>();
            foreach (QmSpec spec in QmSpec.List())
            {
                VoucherKind kind = Kinds.Find(spec.Kind);
                string title = kind == null ? spec.Kind : kind.Title;
                rules.Add(QmWrite(spec, "create", title + "新增", spec.AddAuth));
                rules.Add(QmWrite(spec, "delete", title + "删除", spec.DeleteAuth));
                // 检验单修改（报检单不开放修改），功能 id 已按 UFMeta 按钮权限核对，见 QmEditReq.Auth；产品报检单
                // 单独弃审（QmInsUnverify，弃审按钮 id）。
                string editAuth = QmEditReq.Auth(spec.Kind);
                if (editAuth.Length > 0)
                {
                    rules.Add(QmWrite(spec, "update", title + "修改", editAuth));
                }
                if (spec.Kind == QmInsUnverify.Kind)
                {
                    rules.Add(QmWrite(spec, "unverify", title + "弃审", spec.UnverifyAuth));
                }
            }
            // 不良品处理单：新增、删除、审核、弃审（QmRejSpec 的卡片按钮 id，见 QmRejSpec）。
            foreach (QmRejSpec spec in QmRejSpec.List())
            {
                rules.Add(RejWrite(spec, "create", "新增", spec.AddAuth));
                rules.Add(RejWrite(spec, "delete", "删除", spec.DeleteAuth));
                rules.Add(RejWrite(spec, "verify", "审核", spec.VerifyAuth));
                rules.Add(RejWrite(spec, "unverify", "弃审", spec.UnverifyAuth));
            }
            // 其他报检单、其他检验单新增、删除、审核、弃审（QmOthSpec 的 id）；其他报检单新增后的补审核另查审核 id（QmOthIns）。
            foreach (QmOthSpec spec in QmOthSpec.List())
            {
                rules.Add(OthWrite(spec, "create", "新增", spec.AddAuth));
                rules.Add(OthWrite(spec, "delete", "删除", spec.DeleteAuth));
                rules.Add(OthWrite(spec, "verify", "审核", spec.VerifyAuth));
                rules.Add(OthWrite(spec, "unverify", "弃审", spec.UnverifyAuth));
                rules.Add(OthWrite(spec, "update", "修改", QmEditReq.Auth(spec.Kind)));  // 功能 id 见 QmEditReq.Auth
            }
            return rules.ToArray();
        }

        // 没有供应商（可空列）；部门、仓库可空，存货必有。数据权限的行由 QmOthIns / QmChkSrc / QmOthDel 拼。
        static PermRule OthWrite(QmOthSpec spec, string op, string what, string auth)
        {
            return R(spec.RuleKey(op), spec.Title + what, A(auth), PermObj.Opt(PermObj.Vendor, "CVENCODE"),
                PermObj.Opt(PermObj.Department, "CDEPCODE"), PermObj.H(PermObj.Inventory, "CINVCODE"),
                PermObj.Opt(PermObj.Warehouse, "CWHCODE"));
        }

        // 不良品处理单表头有供应商（来料必有）、存货、仓库，没有部门列；生单时部门取检验单（可空）。
        static PermRule RejWrite(QmRejSpec spec, string op, string what, string auth)
        {
            PermObj vendor = spec.Incoming
                ? PermObj.H(PermObj.Vendor, "CVENCODE") : PermObj.Opt(PermObj.Vendor, "CVENCODE");
            return R(spec.RuleKey(op), spec.Title + what, A(auth), vendor, PermObj.Opt(PermObj.Department, "CDEPCODE"),
                PermObj.H(PermObj.Inventory, "CINVCODE"), PermObj.Opt(PermObj.Warehouse, "CWHCODE"));
        }

        static PermRule QmWrite(QmSpec spec, string op, string title, string auth)
        {
            PermObj vendor = spec.Incoming
                ? PermObj.H(PermObj.Vendor, "CVENCODE") : PermObj.Opt(PermObj.Vendor, "CVENCODE");
            return R(spec.RuleKey(op), title, A(auth), vendor, PermObj.Opt(PermObj.Department, "CDEPCODE"),
                PermObj.H(PermObj.Inventory, "CINVCODE"), PermObj.Opt(PermObj.Warehouse, "CWHCODE"));
        }
    }
}
