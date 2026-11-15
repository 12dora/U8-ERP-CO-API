using System;
using System.Collections.Generic;

namespace U8Co
{
    // 基础档案：EAI 根标签、RsXml 文件、表和键。SQL 里的表名列名只从这里来。
    // 标签名一律是 RsXml 里的 EAI 标签，大小写不敏感，发送时换成 RsXml 的写法。
    internal sealed class ArcKind
    {
        public string Name;
        public string Root;
        public string RsFile;
        public string Table;
        public string Key;
        public string NameCol;
        public string ClassCol;
        public string Ts;
        // 第二张表（存货 Inventory_Sub、人员 Person）按同一编码合并读取。
        public string Sub;
        public string SubKey;
        public string SubTs;
        // 编码级次：GradeDef_Base.KEYWORD，新增时按编码长度算 rank，末级标志默认 1。
        public string Grade;
        public string RankTag;
        public string EndTag;
        // 新增时缺省为登录日期的标签。
        public string DateTag;
        public bool InvBody;
        public bool NeedTemplate;
        // 修改（diffedit）时 U8 仍要的标签：调用方没改就从当前行补上。
        public string[] Resend = new string[0];
        // 删除时要带上的标签（人员的证件类型）。
        public string[] DeleteTags = new string[0];
        // 调用方不能写的标签。
        public string[] Block = new string[0];
        // 银行账号、证件号、联系方式等：get 照常返回，只是不从模板复制到新档案。
        public string[] Private = new string[0];
        // 从模板复制时跳过的标签（与模板编码绑定、停用日期等）。
        public string[] TplSkip = new string[0];
        // 修改时不从当前行带的标签：U8 自己维护的级次、末级、成本与耗用量，避免把读到的旧值写回。
        public string[] UpdSkip = new string[0];
        // 新增缺省值：标签、值成对。
        public string[] Defaults = new string[0];
        // 其中至少一个为 1，都没给时第一个置 1。
        public string[] OneOf = new string[0];
        // 目标缺失时从来源复制：目标、来源成对。
        public string[] Copies = new string[0];
        // 只读档案（ArcKindRo）：新增、修改、删除 400「该档案只读」；按年度分表存的列（科目 iyear）；编码最大长度。
        public bool ReadOnly;
        public string YearCol;
        public int CodeMax = 30;
        // get、list 仍按表列名返回（ArcReadRo，开户银行、项目）；NoDelete 挡住删除（目前没有档案用它）；
        // 不走 RsXml 的档案（项目，受控 SQL 写入）的固定标签表，ArcMap.Of 直接返回它。
        public bool RoRead;
        public bool NoDelete;
        // U8 的 EAI 不提供修改（实测：自定义项档案、客户存货对照返回「…不提供修改功能！」），只能删了重建。
        public bool NoUpdate;
        // 修改被拒时的说明（固定资产卡片改做变动单、设备台账只能新增），null 用通用说明。
        public string NoUpdateText;
        public ArcMap SqlMap;
        // 新增必须给出的名称标签；null 表示名称就是编码的一部分（自定义项档案的档案值）。
        public string NameTag = "name";
        // 两列主键的可写档案（ArcPair）：EAI 报文里不发 code，按这两个标签发编码的两段（如自定义项的 id、value）。
        public string[] CodeTags;
        // 发送编码的 EAI 标签，调用方不能写（总账档案，见 ArcGlKinds）：币种的编码是名称，按 <name> 发，<code>（币种符号）是普通字段；
        // 凭证类别的编码是类别字，按 <type> 发。其余档案都是 code。
        public string CodeTag = "code";
        // 修改用的 EAI proc：一律 diffedit（发整条记录）；原因码（ArcReason）实测的是 edit，同样发整条记录。
        public string EditProc = "diffedit";

        static readonly string[] Common = new string[] { "CreatePerson", "ModifyPerson", "ModifyDate" };
        static readonly Dictionary<string, ArcKind> All = Build();

        HashSet<string> _block;
        HashSet<string> _skip;
        HashSet<string> _upd;

        // 给 meta 路由用：全部档案类型。
        internal static IEnumerable<ArcKind> List()
        {
            return All.Values;
        }

        public static ArcKind Find(string name)
        {
            ArcKind kind;
            if (name == null || !All.TryGetValue(name, out kind))
            {
                return null;
            }
            return kind;
        }

        public bool Blocked(string tag)
        {
            return _block.Contains(tag);
        }

        public bool UpdSkipped(string tag)
        {
            return _upd.Contains(tag);
        }

        public bool TplSkipped(string tag)
        {
            return _skip.Contains(tag);
        }

        public string DefaultOf(string tag)
        {
            for (int i = 0; i + 1 < Defaults.Length; i += 2)
            {
                if (string.Equals(Defaults[i], tag, StringComparison.OrdinalIgnoreCase))
                {
                    return Defaults[i + 1];
                }
            }
            return null;
        }

        void Seal()
        {
            string[] common = new string[Common.Length + 1];
            Common.CopyTo(common, 0);
            common[Common.Length] = CodeTag;
            _block = Set(common, Block, null);
            _upd = Set(common, Block, UpdSkip);
            _skip = Set(common, Block, Private);
            _skip.UnionWith(TplSkip);
            _skip.Add("name");
            // 级次不从模板带：按新编码算，算不出就不发，交给 U8 判断。
            if (RankTag != null)
            {
                _skip.Add(RankTag);
            }
        }

        static HashSet<string> Set(string[] a, string[] b, string[] c)
        {
            HashSet<string> set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            set.UnionWith(a);
            if (b != null)
            {
                set.UnionWith(b);
            }
            if (c != null)
            {
                set.UnionWith(c);
            }
            return set;
        }

        static Dictionary<string, ArcKind> Build()
        {
            ArcKind[] kinds = new ArcKind[]
            {
                Customer(), Vendor(), Inventory(), Department(), Person(), Warehouse(),
                Class("customer_class", "customerclass", "CustomerClassXmlRs.xml", "CustomerClass", "cCCCode", "cCCName"),
                Class("vendor_class", "vendorclass", "VendorClassXmlRs.xml", "VendorClass", "cVCCode", "cVCName"),
                Class("inventory_class", "inventoryclass", "InventoryClassXmlRs.xml", "InventoryClass", "cInvCCode", "cInvCName"),
                // 原因码档案（ArcReason）。
                ArcReason.Kind()
            };
            kinds = ArcKindRo.Append(kinds);
            // 客户、供应商的银行账户和联系人（ArcPartner）。
            kinds = ArcPartner.Append(kinds);
            Dictionary<string, ArcKind> map = new Dictionary<string, ArcKind>(StringComparer.Ordinal);
            for (int i = 0; i < kinds.Length; i++)
            {
                kinds[i].Seal();
                map.Add(kinds[i].Name, kinds[i]);
            }
            return map;
        }

        static ArcKind Customer()
        {
            ArcKind k = Flat("customer", "customer", "CustomerXmlRs.xml", "Customer", "cCusCode", "cCusName");
            k.ClassCol = "cCCCode";
            k.DateTag = "seed_date";
            k.Defaults = new string[] { "ccusmngtypecode", "999", "ccusexch_name", AccDefaults.HomeToken };
            k.Block = new string[]
            {
                "ar_rest", "last_tr_date", "last_tr_amount", "last_rec_date", "last_rec_amount", "tr_frequency"
            };
            k.Private = new string[]
            {
                "bank_acc_number", "address", "phone", "mobile", "email", "devliver_site",
                "cCusEnAddr1", "cCusEnAddr2", "cCusEnAddr3", "cCusEnAddr4", "cBranchAddr", "cBranchPhone"
            };
            k.TplSkip = new string[]
            {
                "abbrname", "head_corp_code", "InvoiceCompany", "ccuscreditcompany", "auth_class",
                "tax_reg_code", "cRelVendor", "end_date", "legal_man", "contact", "fax", "bp", "postcode",
                "bank_open", "ccusbankcode", "cCusEnName", "cBranchPerson", "Memo", "Proxy",
                "LicenceDate", "LicenceSDate", "LicenceEDate", "LicenceADays", "BusinessDate", "BusinessSDate",
                "BusinessEDate", "BusinessADays", "ProxySDate", "ProxyEDate", "ProxyADays"
            };
            return k;
        }

        static ArcKind Vendor()
        {
            ArcKind k = Flat("vendor", "vendor", "VendorXmlRs.xml", "Vendor", "cVenCode", "cVenName");
            k.ClassCol = "cVCCode";
            k.DateTag = "seed_date";
            k.Resend = new string[] { "name", "abbrname", "bvencargo", "bproxyforeign", "bvenservice" };
            k.OneOf = new string[] { "bvencargo", "bproxyforeign", "bvenservice" };
            k.Defaults = new string[] { "cvenexch_name", AccDefaults.HomeToken };
            k.Block = new string[]
            {
                "ap_rest", "last_tr_date", "last_tr_money", "last_pay_date", "last_pay_amount", "tr_frequency"
            };
            k.Private = new string[]
            {
                "bank_acc_number", "address", "receive_site", "phone", "mobile", "email", "PassGMP",
                "cvenenaddr1", "cvenenaddr2", "cvenenaddr3", "cvenenaddr4", "cvenbranchaddr", "cvenbranchphone"
            };
            k.TplSkip = new string[]
            {
                "abbrname", "head_corp_code", "auth_class", "tax_reg_code", "cRelCustomer", "barcode", "end_date",
                "legal_man", "contact", "fax", "bp", "postcode", "bank_open", "cvenbankcode", "cvenenname",
                "cvenbranchperson", "Memo", "ProxyDate", "cvenlicenceno", "cvenbusinessno", "cvengspauthno",
                "ivengspauth", "LicenceDate", "LicenceSDate", "LicenceEDate", "LicenceADays", "BusinessDate",
                "BusinessSDate", "BusinessEDate", "BusinessADays", "ProxySDate", "ProxyEDate", "ProxyADays"
            };
            return k;
        }

        static ArcKind Inventory()
        {
            ArcKind k = Flat("inventory", "inventory", "inventoryxmlrs.xml", "Inventory", "cInvCode", "cInvName");
            k.ClassCol = "cInvCCode";
            k.Sub = "Inventory_Sub";
            k.SubKey = "cInvSubCode";
            k.DateTag = "start_date";
            k.InvBody = true;
            k.NeedTemplate = true;
            k.Resend = new string[] { "name" };
            // 名称和换算率标签在表里没有对应列；上次盘点日期由库存维护。
            k.Block = new string[]
            {
                "lastcheck_date", "unitgroup_name", "puunit_name", "saunit_name", "stunit_name", "caunit_name",
                "puunit_ichangrate", "saunit_ichangrate", "stunit_ichangrate", "caunit_ichangrate"
            };
            k.Private = new string[] { "address", "ProduceAddress" };
            // 最新成本、参考成本、最高进价由入库/核算回写；平均（日均）耗用量、再订货点由 ROP 计算。
            k.UpdSkip = new string[]
            {
                "new_cost", "ref_cost", "top_source_price", "avgquantity", "VagQuantity", "subscribe_point", "SubscribePoint"
            };
            k.TplSkip = new string[]
            {
                "InvAddCode", "barcode", "end_date", "file", "checkout_no", "licence", "RegisterNo", "EnterNo",
                "cEngineerFigNo", "switch_item", "dreplacedate", "EnglishName", "NotPatentName"
            };
            return k;
        }

        static ArcKind Department()
        {
            ArcKind k = Flat("department", "department", "DepartmentXmlRs.xml", "Department", "cDepCode", "cDepName");
            k.Grade = "department";
            k.RankTag = "rank";
            k.EndTag = "endflag";
            k.UpdSkip = new string[] { "rank", "endflag" };
            k.DateTag = "ddepbegindate";
            k.Resend = new string[] { "name" };
            k.Private = new string[] { "phone", "address", "cdepemail" };
            k.TplSkip = new string[] { "ddependdate", "cdepfax", "cdeppostcode", "vauthorizedoc" };
            return k;
        }

        static ArcKind Person()
        {
            ArcKind k = Flat("person", "v_aa_hr_hi_person", "V_AA_Hr_Hi_PersonXmlRs.xml", "hr_hi_person", "cPsn_Num", "cPsn_Name");
            k.ClassCol = "cDept_num";
            k.Ts = "hrts";
            k.Sub = "Person";
            k.SubKey = "cPersonCode";
            k.SubTs = "pubufts";
            k.DateTag = "dpvaliddate";
            k.Resend = new string[] { "name", "rIDType", "rpersontype", "cdept_num", "rsex", "rEmployState" };
            k.DeleteTags = new string[] { "rIDType" };
            k.Defaults = new string[] { "rIDType", "0" };
            k.Copies = new string[] { "cdepcode", "cdept_num" };
            k.Block = new string[] { "MPicture" };
            k.Private = new string[]
            {
                "vIDNo", "vSSNo", "cpsnaccount", "cpsnbankcode", "dbirthdate", "MPicture", "cpsnmobilephone",
                "cpsnfphone", "cpsnophone", "cpsninphone", "cpsnemail", "cpsnfaddr", "cpsnpostaddr", "cpsnpostcode",
                "cpsnqqcode", "cpsnurl", "rNativePlace", "rNational", "rhealthStatus", "rMarriStatus",
                "rPerResidence", "vAliaName"
            };
            // 业务部门 cdepcode 不从模板带，按调用方的 cdept_num 缺省。
            k.TplSkip = new string[]
            {
                "JobNumber", "dpinvaliddate", "dJoinworkDate", "dEnterDate", "dRegularDate", "dEnterUnitDate",
                "cpsnoseat", "bProbation", "cdepcode", "cPsn_NameEN"
            };
            return k;
        }

        static ArcKind Warehouse()
        {
            ArcKind k = Flat("warehouse", "warehouse", "WarehouseXmlRs.xml", "Warehouse", "cWhCode", "cWhName");
            k.ClassCol = "cDepCode";
            k.Resend = new string[] { "name", "valuestyle" };
            k.Private = new string[] { "address", "phone" };
            k.TplSkip = new string[] { "barcode" };
            return k;
        }

        static ArcKind Class(string name, string root, string file, string table, string key, string nameCol)
        {
            ArcKind k = Flat(name, root, file, table, key, nameCol);
            k.Grade = root;
            k.RankTag = "rank";
            k.EndTag = "end_rank_flag";
            k.UpdSkip = new string[] { "rank", "end_rank_flag" };
            k.Resend = new string[] { "name" };
            k.TplSkip = new string[] { "barcode" };
            return k;
        }

        static ArcKind Flat(string name, string root, string file, string table, string key, string nameCol)
        {
            ArcKind k = new ArcKind();
            k.Name = name;
            k.Root = root;
            k.RsFile = file;
            k.Table = table;
            k.Key = key;
            k.NameCol = nameCol;
            k.Ts = "pubufts";
            return k;
        }
    }
}
