using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // meta/fields 的档案部分：字段名 = meta archives[].writable（RsXml 标签去掉 Blocked；项目、客户银行 / 联系人用固定标签表；
    // 只读档案为空）；标题 = 标签 → 列名（ArcMap.Column）→ AA_ColumnDic_base.cCaption（zh-CN）。cKey 按档案的表挑
    // （与该表列名重合最多的列表定义），按顺序取第一个有这一列的；没有就 label null。没有类型、枚举。
    internal static class MetaFieldsArc
    {
        const int MaxRows = 6000;

        // { 档案, cKey… }：只有标识符，标题一律现查。
        static readonly string[][] CaptionKeys = new string[][]
        {
            new string[] { "customer", "CustomerRef", "CustomerDistribute" },
            new string[] { "vendor", "VendorRef" },
            new string[] { "inventory", "InventoryRef" },
            new string[] { "department", "DepartmentRef", "Department" },
            new string[] { "person", "HR_HI_PERSON", "HR31", "PersonRef" },
            new string[] { "warehouse", "Warehouse", "WarehouseRef" },
            new string[] { "customer_class", "CustomerClassRef", "CustomerClass" },
            new string[] { "vendor_class", "VendorClass", "VendorClassRef" },
            new string[] { "inventory_class", "InventoryClass", "InventoryClassRef" },
            new string[] { "unit", "ComputationUnit", "ComputationUnitRef" },
            new string[] { "unit_group", "ComputationGroup", "ComputationGroupRef" },
            new string[] { "settle_style", "SettleStyle", "SettleStyleRef" },
            new string[] { "voucher_sign", "dsignRef" },
            new string[] { "currency", "foreigncurrency", "foreigncurrencyRef" },
            new string[] { "position", "Position", "PositionRef" },
            new string[] { "rd_style", "Rd_Style", "Rd_StyleRef" },
            new string[] { "purchase_type", "PurchaseType", "PurchaseTypeRef" },
            new string[] { "sale_type", "SaleType", "SaleTypeRef" },
            new string[] { "district_class", "DistrictClass", "DistrictClassRef" },
            new string[] { "trade_class", "TradeClassRef" },
            new string[] { "aa_bank", "AA_Bank", "AA_BankRef" },
            new string[] { "customer_address", "CusDeliverAddRef" },
            new string[] { "user_define", "Userdefine", "UserdefineRef" },
            new string[] { "customer_inventory", "CusInvContrapose", "CusInvContraposeRef" },
            new string[] { "exchange_rate", "exch", "exchRef" },
            new string[] { "reason", "Reason", "ReasonRef" },
            new string[] { "account", "code", "codeRef" },
            new string[] { "project", "FItemRef" },
            new string[] { "bank", "Bank", "BankRef" },
            new string[] { "customer_bank", "CustomerBankRef" },
            // U8 可能没有供应商银行的列表定义：借用客户银行的，只取两边同名的通用银行列（BorrowCols），其余 label null。
            new string[] { "vendor_bank", "CustomerBankRef" },
            new string[] { "customer_contact", "Crm_Contact", "Crm_ContactRef" },
            new string[] { "vendor_contact", "Crm_Contact", "Crm_ContactRef" }
        };

        // { 档案, 可借用标题的列… }：该档案的 cKey 是借别的表的，只有这些列取标题。
        static readonly string[][] BorrowCols = new string[][]
        {
            new string[] { "vendor_bank", "cBank", "cBranch", "cAccountNum", "cAccountName", "bDefault" }
        };

        // 新增时桥自己要求的标签（名称标签 NameTag 之外）：ArcBank、ArcMoreGuard、ArcPos、ArcUnit、ArcPartnerBank、ArcProjectWrite、ArcReason。
        // 只在某些情况下才要求的（一级收发类别的 rsflag、辅计量单位的 changerate）不算。
        static readonly string[][] NeedTags = new string[][]
        {
            new string[] { ArcBank.Name, "account", "cbankcode", "ccurrencyname" },
            new string[] { "unit_group", "type" },
            new string[] { ArcPos.Name, "warehouse_code" },
            new string[] { ArcUnit.Name, "group_code" },
            new string[] { ArcPartner.CustomerBank, "branch" },
            new string[] { ArcPartner.VendorBank, "branch" },
            new string[] { ArcKindRo.Project, "citemccode" },
            new string[] { ArcReason.Name, ArcReason.TypeTag }
        };

        internal static Dictionary<string, object> Build(WorkContext ctx, ArcKind kind)
        {
            string[] names = Writable(kind);
            ArcMap map = names.Length == 0 ? null : ArcMap.Of(kind);
            string[] keys = KeysOf(kind.Name);
            Dictionary<string, List<string[]>> captions = Captions(ctx.Conn, keys);
            List<object> fields = new List<object>();
            for (int i = 0; i < names.Length; i++)
            {
                string column = map.Column(names[i]);
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["name"] = names[i];
                d["label"] = Borrowable(kind.Name, column) ? Pick(captions, keys, column) : null;
                d["type"] = null;
                d["required"] = Required(kind, names[i]);
                fields.Add(d);
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["archive"] = kind.Name;
            result["fields"] = fields;
            result["fields_revision"] = MetaFieldsRoute.Revision(fields, null, null);
            return result;
        }

        // 同 MetaArc 的 writable。
        internal static string[] Writable(ArcKind kind)
        {
            if (kind.ReadOnly)
            {
                return new string[0];
            }
            if (kind.SqlMap != null)
            {
                return new List<string>(kind.SqlMap.Tags).ToArray();
            }
            if (string.IsNullOrEmpty(kind.RsFile))
            {
                return new string[0];
            }
            List<string> list = new List<string>();
            IList<string> tags = ArcMap.Of(kind).Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                if (!kind.Blocked(tags[i]))
                {
                    list.Add(tags[i]);
                }
            }
            return list.ToArray();
        }

        internal static string[] KeysOf(string archive)
        {
            for (int i = 0; i < CaptionKeys.Length; i++)
            {
                if (CaptionKeys[i][0] == archive)
                {
                    string[] keys = new string[CaptionKeys[i].Length - 1];
                    Array.Copy(CaptionKeys[i], 1, keys, 0, keys.Length);
                    return keys;
                }
            }
            return new string[0];
        }

        // 没登记借用的档案一律可取；登记了的只放行名单里的列（不分大小写）。
        internal static bool Borrowable(string archive, string column)
        {
            for (int i = 0; i < BorrowCols.Length; i++)
            {
                if (BorrowCols[i][0] != archive)
                {
                    continue;
                }
                for (int j = 1; j < BorrowCols[i].Length; j++)
                {
                    if (string.Equals(BorrowCols[i][j], column, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
                return false;
            }
            return true;
        }

        internal static bool Required(ArcKind kind, string tag)
        {
            if (kind.NameTag != null && string.Equals(kind.NameTag, tag, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            for (int i = 0; i < NeedTags.Length; i++)
            {
                if (NeedTags[i][0] != kind.Name)
                {
                    continue;
                }
                for (int j = 1; j < NeedTags[i].Length; j++)
                {
                    if (string.Equals(NeedTags[i][j], tag, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        // cKey（不分大小写）→ [列名, 标题] 列表。
        static Dictionary<string, List<string[]>> Captions(object conn, string[] keys)
        {
            Dictionary<string, List<string[]>> map = new Dictionary<string, List<string[]>>(StringComparer.OrdinalIgnoreCase);
            if (keys.Length == 0)
            {
                return map;
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, CaptionSql(keys.Length), keys, MaxRows);
            for (int i = 0; i < rows.Count; i++)
            {
                string key = CoRows.Col(rows[i], "k");
                string caption = CoRows.Col(rows[i], "c");
                if (caption.Length == 0)
                {
                    continue;
                }
                List<string[]> list;
                if (!map.TryGetValue(key, out list))
                {
                    list = new List<string[]>();
                    map[key] = list;
                }
                list.Add(new string[] { CoRows.Col(rows[i], "f"), caption });
            }
            return map;
        }

        internal static string CaptionSql(int count)
        {
            StringBuilder sql = new StringBuilder("SELECT cKey AS k, cFld AS f, cCaption AS c FROM AA_ColumnDic_base "
                + "WHERE LocaleID='zh-CN' AND cKey IN (");
            for (int i = 0; i < count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
            }
            return sql.Append(")").ToString();
        }

        static string Pick(Dictionary<string, List<string[]>> captions, string[] keys, string column)
        {
            if (string.IsNullOrEmpty(column))
            {
                return null;
            }
            for (int k = 0; k < keys.Length; k++)
            {
                List<string[]> list;
                if (!captions.TryGetValue(keys[k], out list))
                {
                    continue;
                }
                for (int i = 0; i < list.Count; i++)
                {
                    if (string.Equals(list[i][0], column, StringComparison.OrdinalIgnoreCase))
                    {
                        return list[i][1];
                    }
                }
            }
            return null;
        }
    }
}
