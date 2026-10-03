namespace U8Co
{
    // 计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案改为可写，走 EAI。
    // get、list 仍按表列名返回（RoRead），新增、修改、删除的字段是 RsXml 的 EAI 标签；调用 U8 之前的校验在 ArcMoreGuard、
    // 删除前的下级与引用检查在 ArcRefs。EAI 目录（Dir.xml）里这七个根标签都是 in="y"，Distribute.xml 指向 U8SrvTrans.IclsCommon。
    // 币种不在这里：Distribute.xml 指向 U8PzInsert.icurrency，实测按 clsPZInsert 的写法（先设 ToEAICon）报 DISP_E_UNKNOWNNAME；
    // 改为不设 ToEAICon、只调 Transact，与凭证类别、科目一起见 ArcGlKinds / ArcGl（docs/u8-notes.md §11）。
    // 行业（TradeClass）、客户收货地址（CusDeliverAdd）在 Dir.xml / Distribute.xml 里没有根标签，仍只读；
    // 汇率的 currencyrate 走 U8PzInsert.icurrencyrate，RsXml 目录里没有它的字段表；经 EAI 分发器新增、受控 SQL 改删（ArcExchWrite）。
    // 未覆盖：这七类 EAI 是否都支持 diffedit（与自定义项档案不同，RsXml 的定位标签只有 code）。
    internal static class ArcKindRwMore
    {
        internal const string UnitGroup = "unit_group";
        internal const string Settle = "settle_style";
        internal const string Rd = "rd_style";
        internal const string PurchaseType = "purchase_type";
        internal const string SaleType = "sale_type";
        internal const string District = "district_class";
        internal const string AaBank = "aa_bank";

        // 计量单位组 UnitGroupXmlRs.xml：换算类型 type（0 无换算、1 固定、2 浮动）新增必填，组里有单位后不能改（ArcMoreGuard）。
        internal static ArcKind UnitGroupKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "unitgroup", "UnitGroupXmlRs.xml");
            k.Resend = new string[] { "name", "type" };
            k.TplSkip = new string[] { "cgrprelinvcode" };
            return k;
        }

        // 结算方式 BalanceTypeXmlRs.xml：编码级次按 GradeDef_Base 的 settlestyle（如 12），级次、末级由桥和 U8 维护。
        // 是否票据管理 flag、对应票据类型 issbilltype 在表里非空，新增缺省 0。
        internal static ArcKind SettleKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "balancetype", "BalanceTypeXmlRs.xml");
            Graded(k, "settlestyle", "code_rank", "end_rank_flag");
            k.Defaults = new string[] { "flag", "0", "issbilltype", "0" };
            return k;
        }

        // 收发类别 ReceiveSendTypeXmlRs.xml：编码级次按 rd_style（如 122）；收发标志 rsflag（1 收、0 发）下级与上级相同，
        // 没给时取上级的，一级必须给（ArcMoreGuard）。
        internal static ArcKind RdKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "receivesendtype", "ReceiveSendTypeXmlRs.xml");
            Graded(k, "rd_style", "sort", "end_flag");
            k.Resend = new string[] { "name", "rsflag" };
            return k;
        }

        // 采购类型 PurchaseTypeXmlRs.xml / 销售类型 SaleTypeXmlRs.xml：入（出）库类别 rstype_code 可空，给了必须是末级收发类别。
        // 是否默认值 bdefau（采购另有委外默认 bpfdefault）在表里非空，新增缺省 0。
        internal static ArcKind PurchaseTypeKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "purchasetype", "PurchaseTypeXmlRs.xml");
            k.Defaults = new string[] { "bdefau", "0", "bpfdefault", "0" };
            k.TplSkip = new string[] { "bdefau", "bpfdefault" };
            return k;
        }

        internal static ArcKind SaleTypeKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "saletype", "SaleTypeXmlRs.xml");
            k.Defaults = new string[] { "bdefau", "0" };
            k.TplSkip = new string[] { "bdefau" };
            return k;
        }

        // 地区分类 DistrictClassXmlRs.xml：编码级次按 districtclass（如 234）。
        internal static ArcKind DistrictKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "districtclass", "DistrictClassXmlRs.xml");
            Graded(k, "districtclass", "sort", "endflag");
            return k;
        }

        // 银行档案（所属银行）AA_BankXmlRs.xml：银行标识 i_id 由 U8 编号。
        internal static ArcKind AaBankKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "aa_bank", "AA_BankXmlRs.xml");
            k.Block = new string[] { "i_id" };
            k.Resend = new string[] { "name" };
            return k;
        }

        static void Graded(ArcKind k, string keyword, string rankTag, string endTag)
        {
            k.Grade = keyword;
            k.RankTag = rankTag;
            k.EndTag = endTag;
            k.Block = new string[] { rankTag, endTag };
            k.UpdSkip = new string[] { rankTag, endTag };
            k.Resend = new string[] { "name" };
        }
    }
}
