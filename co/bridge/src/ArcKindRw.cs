namespace U8Co
{
    // 货位、计量单位、自定义项档案、客户存货对照改为可写，走 EAI（U8SrvTrans.IClsCommon，与九类档案同一条路）。
    // get、list 仍按表列名返回（RoRead），新增、修改、删除的字段是 RsXml 的 EAI 标签。调用 U8 之前的校验在 ArcGuard。
    // EAI 目录（EAI\XML\Operation\Dir.xml）里这四个根标签都是 in="y"，Distribute.xml 指向 U8SrvTrans.IclsCommon。
    internal static class ArcKindRw
    {
        // 货位 PositionXmlRs.xml：编码级次按 GradeDef_Base 的 positionclass（如 2222），新增时桥算级次、末级置 1；
        // 级次、末级由桥和 U8 维护，调用方不能写。所属仓库 warehouse_code 必填，修改时不能换仓库（ArcPos）。
        internal static ArcKind Position(ArcKind k)
        {
            Eai(k, "position", "PositionXmlRs.xml");
            k.Grade = "positionclass";
            k.RankTag = "grade";
            k.EndTag = "end_flag";
            k.Block = new string[] { "grade", "end_flag" };
            k.UpdSkip = new string[] { "grade", "end_flag" };
            k.Resend = new string[] { "name" };
            k.TplSkip = new string[] { "barcode", "remark" };
            return k;
        }

        // 计量单位 UnitXmlRs.xml：所属计量单位组 group_code 必填，主计量单位标志、换算率、序号按组的换算类型补齐（ArcUnit）。
        // 模板不带主计量单位标志、序号、条码、对应存货。
        internal static ArcKind Unit(ArcKind k)
        {
            Eai(k, "unit", "UnitXmlRs.xml");
            k.ClassCol = "cGroupCode";
            k.Resend = new string[] { "name" };
            k.TplSkip = new string[] { "barcode", "main_flag", "SerialNum", "cunitrefinvcode" };
            return k;
        }

        // 自定义项档案 DefineXmlRs.xml（根标签 define，表 UserDefine）：编码 "<自定义项号>:<档案值>" 拆成 id、value 两个标签发送，
        // 两者都不能在 fields 里改；可写的只有别名 alias、条码 barcode。名称就是档案值，新增不要求 name。
        internal static ArcKind Define(ArcKind k)
        {
            Eai(k, "define", "DefineXmlRs.xml");
            k.CodeTags = new string[] { "id", "value" };
            k.Block = new string[] { "id", "value" };
            k.NameTag = null;
            k.NoUpdate = true;
            return k;
        }

        // 客户存货对照 CusInvContraposeXmlRs.xml：编码 "<客户编码>:<存货编码>" 拆成 ccuscode、cinvcode 发送；
        // 新增必须有客户存货名称 ccusinvname（列表的 name）。
        internal static ArcKind Contra(ArcKind k)
        {
            Eai(k, "cusinvcontrapose", "CusInvContraposeXmlRs.xml");
            k.CodeTags = new string[] { "ccuscode", "cinvcode" };
            k.Block = new string[] { "ccuscode", "cinvcode" };
            k.NameTag = "ccusinvname";
            k.NoUpdate = true;
            return k;
        }

        internal static void Eai(ArcKind k, string root, string file)
        {
            k.ReadOnly = false;
            k.Root = root;
            k.RsFile = file;
        }
    }
}
