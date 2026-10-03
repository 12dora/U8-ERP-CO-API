using System;

namespace U8Co
{
    // 客户、供应商的子档案：银行账户（CustomerBank / VendorBank）、联系人（Crm_Contact / Ven_Contact）。
    // 编码是两段 "<客户或供应商编码>:<账号或联系人编码>"（ArcPair，同 customer_address）。get、list 按表列名返回（RoRead）；
    // 新增、修改、删除一次只动一行，不整批替换同一客户的其他行。fields 用本文件的固定标签表（ArcKind.SqlMap），不走 RsXml。
    // 银行账户见 ArcPartnerBank（默认账户规则，受控 SQL），客户联系人见 ArcPartnerContact（EAI customerlinker），
    // 供应商联系人见 ArcVenContact（新增走 EAI 分发器 vendorcontact，修改、删除走受控 SQL）。
    internal static class ArcPartner
    {
        internal const string CustomerBank = "customer_bank";
        internal const string VendorBank = "vendor_bank";
        internal const string CustomerContact = "customer_contact";
        internal const string VendorContact = "vendor_contact";

        internal static bool Is(ArcKind k)
        {
            return k != null && SideOf(k.Name) != null;
        }

        internal static PartnerSide SideOf(string archive)
        {
            if (archive == CustomerBank || archive == CustomerContact)
            {
                return PartnerSide.Customer;
            }
            if (archive == VendorBank || archive == VendorContact)
            {
                return PartnerSide.Vendor;
            }
            return null;
        }

        internal static bool IsBank(ArcKind k)
        {
            return k.Name == CustomerBank || k.Name == VendorBank;
        }

        // ArcKind.Build 调用：追加四类子档案。
        internal static ArcKind[] Append(ArcKind[] kinds)
        {
            ArcKind[] extra = new ArcKind[]
            {
                Bank(CustomerBank, PartnerSide.Customer),
                Bank(VendorBank, PartnerSide.Vendor),
                Contact(CustomerContact, PartnerSide.Customer),
                Contact(VendorContact, PartnerSide.Vendor)
            };
            ArcKind[] all = new ArcKind[kinds.Length + extra.Length];
            Array.Copy(kinds, all, kinds.Length);
            Array.Copy(extra, 0, all, kinds.Length, extra.Length);
            return all;
        }

        // ArcRoutes.Write 调用：新增、修改、删除。
        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            if (IsBank(req.Kind))
            {
                return ArcPartnerBank.Write(ctx, req);
            }
            if (req.Kind.Name == VendorContact)
            {
                return ArcVenContact.Write(ctx, req);
            }
            return ArcPartnerContact.Write(ctx, req);
        }

        // DocLocks.ArcKeys 调用：子档案的写入另锁上级客户或供应商（"arc:customer:<编码>"），与上级档案的修改、
        // 同一客户其他账户的默认标志改动串行。不是子档案返回 null。
        internal static string[] LockKeys(string archive, string code)
        {
            PartnerSide side = SideOf(archive);
            if (side == null)
            {
                return null;
            }
            int at = code.IndexOf(':');
            string partner = at > 0 ? code.Substring(0, at) : code;
            return new string[] { "arc:" + side.Archive + ":" + partner, "arc:" + archive + ":" + code };
        }

        // 银行账户：名称是开户银行 cBranch（U8 客户档案「银行」页的开户银行）；表没有 rowversion。
        // 标签：branch 开户银行、bank_code 所属银行编码（AA_Bank）、account_name 账户名称、default 默认账户，
        // 以及省、市、联行号等（列名随客户 cCus… / 供应商 cVen…）。
        static ArcKind Bank(string name, PartnerSide side)
        {
            string p = side.Prefix;
            ArcKind k = Kind(name, side.BankTable, side.CodeCol, "cBranch", null);
            k.SqlMap = ArcMap.Fixed(new string[]
            {
                "branch", "cBranch", "bank_code", "cBank", "account_name", "cAccountName", "default", "bDefault",
                "province", p + "Prinvince", "city", p + "City", "cbb_dep_id", p + "CBBDepId",
                "branch_id", p + "BranchId", "branch_id_sec", p + "BranchIdSec"
            });
            k.NameTag = null;
            return k;
        }

        // 联系人：标签是 EAI 模板（CustomerLinker.xsl / VendorContact.xml）的标签，列名是 U8CRMEAINew 里的对照。
        // 建档人、变更人、日期由 U8 维护；自定义项 11–16 是数值、日期列，暂不开放。
        // 直接调供应商联系人的 EAI 组件（VencontactSrvEAI.clsCRMEAI）新增报「数据库中没有该字段(bcsexid)」；
        // 经官方分发器 ProcessEx 新增成功，修改、删除回「供应商联系人不支持修改和删除导入」（见 ArcVenContact）。
        // 供应商联系人不开放 position、favorite（ArcPartnerContactMap.VendorPairs）。
        static ArcKind Contact(string name, PartnerSide side)
        {
            ArcKind k = Kind(name, side.ContactTable, side.CodeCol, "cContactName", "ufts");
            k.SqlMap = ArcMap.Fixed(side.IsCustomer ? ArcPartnerContactMap.Pairs() : ArcPartnerContactMap.VendorPairs());
            return k;
        }

        static ArcKind Kind(string name, string table, string key, string nameCol, string ts)
        {
            ArcKind k = new ArcKind();
            k.Name = name;
            k.Table = table;
            k.Key = key;
            k.NameCol = nameCol;
            k.Ts = ts;
            k.RoRead = true;
            // 两列主键：不收 template（ArcReq.ParseCreate 按 CodeTags 判断）；这两个标签只作说明，不发给 U8。
            k.CodeTags = new string[] { "partner", "code" };
            k = ArcPair.Apply(k);
            return k;
        }
    }

    // 客户或供应商一侧的表名、列名。SQL 里的表名、列名只来自这里。
    internal sealed class PartnerSide
    {
        public string Archive;
        public string Label;
        public string Table;
        public string CodeCol;
        public string Prefix;
        public string BankTable;
        public string ContactTable;
        // 上级档案上跟默认账户走的三列：开户银行、银行账号、所属银行编码。
        public string BankCol;
        public string AccountCol;
        public string BankCodeCol;

        internal static readonly PartnerSide Customer = Make("customer", "客户", "Customer", "cCusCode", "cCus", "Crm_Contact");
        internal static readonly PartnerSide Vendor = Make("vendor", "供应商", "Vendor", "cVenCode", "cVen", "Ven_Contact");

        public bool IsCustomer
        {
            get { return Archive == "customer"; }
        }

        // 银行账户表是上级表名加 Bank（CustomerBank、VendorBank）。
        static PartnerSide Make(string archive, string label, string table, string codeCol, string prefix, string contact)
        {
            PartnerSide s = new PartnerSide();
            s.Archive = archive;
            s.Label = label;
            s.Table = table;
            s.CodeCol = codeCol;
            s.Prefix = prefix;
            s.BankTable = table + "Bank";
            s.ContactTable = contact;
            s.BankCol = prefix + "Bank";
            s.AccountCol = prefix + "Account";
            s.BankCodeCol = prefix + "BankCode";
            return s;
        }
    }
}
