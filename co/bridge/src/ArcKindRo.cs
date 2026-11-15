using System;

namespace U8Co
{
    // 只读档案：科目、凭证类别等；get 按表列名返回，ReadOnly 挡住新增、修改、删除。
    // 本单位开户银行、项目可写，但 get、list 照旧按表列名返回（RoRead）：
    // 开户银行走 EAI（BankXmlRs.xml，同九类可写档案）；项目走受控 SQL（ArcProjectWrite 新增、修改，ArcProjectDel 删除）。
    // 货位、计量单位、自定义项档案、客户存货对照也走 EAI 可写（ArcKindRw），get、list 同样按表列名返回；
    // 之后计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案也改为 EAI 可写（ArcKindRwMore）。
    // 币种、凭证类别也可写（ArcGlKinds：新增走 U8PzInsert 的 EAI 组件，修改、删除走受控 SQL），get、list 照旧按表列名返回。
    // 仍然只读：科目（ICode 实测不可用、写入年度无法确定）、行业、客户收货地址（EAI 没有根标签）。汇率 可写（ArcExchWrite）。
    // Ts 为 null 的表没有 rowversion，列表不支持 changed_since。
    // 货位、收发类别、采购类型、销售类型、地区分类、行业、银行档案（所属银行）单列主键；
    // 客户收货地址、自定义项、客户存货对照是两列主键，编码写成 "<第一列>:<第二列>"（ArcPair）。编码长度取自表列长度。
    // 汇率 exchange_rate 见 ArcExch，固定资产卡片 fa_card 见 ArcFa，操作员 operator、角色 role 见 ArcUa。
    internal static class ArcKindRo
    {
        // 项目：编码写成 "<项目大类>:<项目编码>"，表按大类取 fitemss<大类>（见 ArcProject）。
        internal const string Project = "project";

        internal static ArcKind[] Append(ArcKind[] kinds)
        {
            ArcKind[] extra = new ArcKind[]
            {
                Account(),
                ArcKindRw.Unit(Ro("unit", "ComputationUnit", "cComunitCode", "cComUnitName", "pubufts", 35)),
                ArcKindRwMore.UnitGroupKind(Ro("unit_group", "ComputationGroup", "cGroupCode", "cGroupName", "pubufts", 35)),
                // 结算方式编码最长 3（SettleStyle.cSSCode）。
                ArcKindRwMore.SettleKind(Ro("settle_style", "SettleStyle", "cSSCode", "cSSName", "pubufts", 3)),
                // 凭证类别、币种可写（ArcGlKinds / ArcGl）。
                ArcGlKinds.SignKind(Ro("voucher_sign", "dsign", "csign", "ctext", null, 30)),
                // 单据和凭证里引用币种都用币种名称（cexch_name，最长 8），编码列 cexch_code 在 get 的 fields 里。
                ArcGlKinds.CurrencyKind(Ro("currency", "foreigncurrency", "cexch_name", "cexch_name", "pubufts", 8)),
                BankKind(),
                ProjectKind(),
                ArcKindRw.Position(Cls(Ro("position", "Position", "cPosCode", "cPosName", "pubufts", 20), "cWhCode")),
                ArcKindRwMore.RdKind(Ro("rd_style", "Rd_Style", "cRdCode", "cRdName", "pubufts", 5)),
                ArcKindRwMore.PurchaseTypeKind(Ro("purchase_type", "PurchaseType", "cPTCode", "cPTName", "pubufts", 2)),
                ArcKindRwMore.SaleTypeKind(Ro("sale_type", "SaleType", "cSTCode", "cSTName", "pubufts", 2)),
                ArcKindRwMore.DistrictKind(Ro("district_class", "DistrictClass", "cDCCode", "cDCName", "pubufts", 12)),
                // 行业的 rowversion 列叫 ufts。
                Ro("trade_class", "TradeClass", "cTradeCCode", "cTradeCName", "ufts", 12),
                ArcKindRwMore.AaBankKind(Ro("aa_bank", "AA_Bank", "cBankCode", "cBankName", "pubufts", 5)),
                // 客户收货地址没有 rowversion；自定义项的名称就是档案值 cValue（别名 cAlias 在 fields 里）。
                ArcPair.Apply(Ro("customer_address", "CusDeliverAdd", "cCusCode", "cDeliverAdd", null, 0)),
                ArcKindRw.Define(ArcPair.Apply(Ro("user_define", "UserDefine", "cID", "cValue", "pubufts", 0))),
                ArcKindRw.Contra(ArcPair.Apply(Ro("customer_inventory", "CusInvContrapose", "cCusCode", "cCusInvName", "pubufts", 0))),
                // 汇率（exch）：按 (币种, 年度, 期间[, 日]) 并起记账汇率和调整汇率，编码 "<币种>:<年度>:<期间>[:<日>]"，读取在 ArcExch；
                // 可写：新增走 EAI 分发器，修改、删除走受控 SQL（ArcExchWrite、ArcExchSql）。
                ArcExchWrite.Kind(Ro(ArcExch.Name, "exch", "cexch_name", "cexch_name", "pubufts", ArcExch.CodeMax)),
                // 固定资产卡片（fa_card）：编码是卡片编号，没有 rowversion；按登录月末取卡片版本和累计折旧，读取都在 ArcFa。
                // 可新增（code 是资产编号）、撤销本期新增，走 EAI capitalasserts（ArcFaWrite、FaCardReq）；不能修改。
                FaCardReq.Kind(Ro(ArcFa.Name, "fa_Cards", "sCardNum", "sAssetName", null, ArcFa.CodeMax)),
                // 设备台账（equipment，EQ_EQData）：按表列名读取；只能新增，走 EAI eqdata（ArcEq）。
                ArcEq.Kind(Ro(ArcEq.Name, "EQ_EQData", "cEQCode", "cEQName", "ufts", ArcEq.CodeMax)),
                // U8 操作员、角色（系统库 UFSYSTEM，三段名）：只列本账套有授权的，没有 rowversion，读取都在 ArcUa。
                Ro(ArcUa.Operator, "UFSYSTEM..UA_User", "cUser_Id", "cUser_Name", null, ArcUa.CodeMax),
                Ro(ArcUa.Role, "UFSYSTEM..UA_Group", "cGroup_Id", "cGroup_Name", null, ArcUa.CodeMax)
            };
            ArcKind[] all = new ArcKind[kinds.Length + extra.Length];
            Array.Copy(kinds, all, kinds.Length);
            Array.Copy(extra, 0, all, kinds.Length, extra.Length);
            return all;
        }

        internal static bool IsProject(ArcKind kind)
        {
            return kind != null && kind.Name == Project;
        }

        // 科目表按年度存多年的科目，读登录年度（GlState.LoginYear）那一年。
        static ArcKind Account()
        {
            ArcKind k = Ro("account", "code", "ccode", "ccode_name", "pubufts", 40);
            k.YearCol = "iyear";
            return k;
        }

        // 表名、主键列由 ArcProject 按大类决定；这里的 Table/Key 只作说明，不进 SQL。
        // 可写字段：name 项目名称、bclose 是否结算、citemccode 所属分类（末级）；编码不能改。删除只删没有被引用的项目（ArcProjectDel）。
        static ArcKind ProjectKind()
        {
            ArcKind k = Ro(Project, "fitem", "citemcode", "citemname", null, ArcProject.MaxCode);
            k.ClassCol = "citemccode";
            k.ReadOnly = false;
            k.SqlMap = ArcMap.Fixed(new string[] { "name", "citemname", "bclose", "bclose", "citemccode", "citemccode" });
            return k;
        }

        // 本单位开户银行：编码最长 3（Bank.cBCode）。银行账号是业务数据，get 照常返回，只是不从模板复制；
        // 账户名称、客户编号、开户日期、签约标志也跟账号走，不复制。
        static ArcKind BankKind()
        {
            ArcKind k = Ro("bank", "Bank", "cBCode", "cBName", "pubufts", 3);
            k.ReadOnly = false;
            k.Root = "bank";
            k.RsFile = "BankXmlRs.xml";
            k.Resend = new string[] { "name", "account", "flag", "cbankcode", "ccurrencyname" };
            k.Defaults = new string[] { "flag", "0" };
            k.TplSkip = new string[] { "account", "caccname", "ccustomerno", "dopenaccdate", "isignflag" };
            return k;
        }

        static ArcKind Cls(ArcKind k, string classCol)
        {
            k.ClassCol = classCol;
            return k;
        }

        static ArcKind Ro(string name, string table, string key, string nameCol, string ts, int codeMax)
        {
            ArcKind k = new ArcKind();
            k.Name = name;
            k.Table = table;
            k.Key = key;
            k.NameCol = nameCol;
            k.Ts = ts;
            k.ReadOnly = true;
            k.RoRead = true;
            k.CodeMax = codeMax;
            return k;
        }
    }
}
