using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 生产订单修改要守住的子件列（mom_moallocate）：MOrderUpdate 把每一行的子件删了重插（AllocateId 全换新，实测），扩展实体
    // 带不回的列会写成缺省（实测：OpComponentId → 0，VirOpComponentIds、cSubSysBarCode、SoDId → NULL，UpperMoQty → 0）。
    // 调用前后逐列比较，这里是唯一的名单；数量类（Qty、AuxQty、UpperMoQty）另按目标值比。任何一列变了一律 504（MoUpdateCheck）。
    // Restore 是 Update 之后桥按快照写回的列（MoUpdateRestore），UpperMoQty 另按新旧行数量比例写回。
    // 空值读成 NullMark，这样 NULL 与空串也能比出来。
    internal static class MoUpdateCols
    {
        // 列名、是否日期（日期按 style 121 转成文本比）。
        static readonly string[][] Cols = new string[][]
        {
            new string[] { "OpComponentId", "" }, new string[] { "VirOpComponentIds", "" }, new string[] { "cSubSysBarCode", "" },
            new string[] { "OpSeq", "" }, new string[] { "WIPType", "" },
            new string[] { "FVFlag", "" }, new string[] { "CompScrap", "" }, new string[] { "ParentScrap", "" },
            new string[] { "BaseQtyN", "" }, new string[] { "BaseQtyD", "" }, new string[] { "AuxBaseQtyN", "" },
            new string[] { "AuxUnitCode", "" }, new string[] { "ChangeRate", "" }, new string[] { "WhCode", "" },
            new string[] { "LotNo", "" }, new string[] { "ByproductFlag", "" }, new string[] { "QcFlag", "" },
            new string[] { "Offset", "" }, new string[] { "CostWIPRel", "" }, new string[] { "Remark", "" },
            new string[] { "ProductType", "" }, new string[] { "SoType", "" }, new string[] { "SoDId", "" },
            new string[] { "SoCode", "" }, new string[] { "SoSeq", "" }, new string[] { "DemandCode", "" },
            new string[] { "FactoryCode", "" }, new string[] { "StartDemDate", "d" }, new string[] { "EndDemDate", "d" },
            new string[] { "Free1", "" }, new string[] { "Free2", "" }, new string[] { "Free3", "" }, new string[] { "Free4", "" },
            new string[] { "Free5", "" }, new string[] { "Free6", "" }, new string[] { "Free7", "" }, new string[] { "Free8", "" },
            new string[] { "Free9", "" }, new string[] { "Free10", "" },
            // 累计量、标志、成本项：MOrderUpdate 重建子件时不带，只比较，闸门要求为缺省（Guard）。
            new string[] { "TransQty", "" }, new string[] { "TransAppQty", "" }, new string[] { "RequisitionQty", "" },
            new string[] { "RequisitionIssQty", "" }, new string[] { "RequisitionFlag", "" }, new string[] { "PickingQty", "" },
            new string[] { "PickingAuxQty", "" }, new string[] { "ReplenishQty", "" }, new string[] { "ReplenishApplyQty", "" },
            new string[] { "DeclaredQty", "" }, new string[] { "OrgQty", "" }, new string[] { "OrgAuxQty", "" },
            new string[] { "CostItemCode", "" }, new string[] { "CostItemName", "" }, new string[] { "QmFlag", "" },
            new string[] { "InvAlloeFlag", "" }, new string[] { "MoallocateSubId", "" }
        };
        // 调用前必须是缺省值（NULL、空串或数值 0）的列：U8 的修改接口重建子件时不带、桥也不写回，非缺省就保不住。
        // 另要求 EndDemDate = StartDemDate（U8 重建时把两者设成同一个值）。账套数据里这些列全是缺省（MoallocateSubId 有 1 行）。
        static readonly string[] Guard = new string[]
        {
            "ParentScrap", "Offset", "QcFlag", "CostWIPRel", "SoType", "SoCode", "SoSeq", "DemandCode", "FactoryCode",
            "TransQty", "TransAppQty", "RequisitionQty", "RequisitionIssQty", "RequisitionFlag", "PickingQty", "PickingAuxQty",
            "ReplenishQty", "ReplenishApplyQty", "DeclaredQty", "OrgQty", "OrgAuxQty", "CostItemCode", "CostItemName",
            "QmFlag", "InvAlloeFlag", "MoallocateSubId"
        };
        public const string GuardText = "子件有 U8 修改接口不能保留的设置（{0}），不能修改";
        // 每个子件的替代料行数（mom_moallocatesub）；有替代料的订单调用前就拒绝（扩展实体不带替代料）。
        public const string Subs = "subs";
        public const string NullMark = "<null>";
        // Update 之后按快照写回的列（原样写回，NullMark 写回 NULL），顺序即 RestoreSql 里 ? 的顺序；最后两个 ? 是 UpperMoQty 和 AllocateId。
        public static readonly string[] Restore = new string[] { "OpComponentId", "VirOpComponentIds", "cSubSysBarCode", "SoDId" };
        const string Prefix = "k_";

        // 拼进 MoUpdateSql 的子件 select 列表：每列 convert 成文本，别名 k_<列名>。
        public static string SelectList()
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < Cols.Length; i++)
            {
                string col = Cols[i][0];
                sb.Append(Cols[i][1] == "d"
                    ? ", isnull(convert(varchar(23), a." + col + ", 121), '" + NullMark + "') as " + Prefix + col
                    : ", isnull(convert(nvarchar(4000), a." + col + "), N'" + NullMark + "') as " + Prefix + col);
            }
            sb.Append(", convert(varchar(10), (select count(*) from mom_moallocatesub s where s.AllocateId=a.AllocateId)) as ")
                .Append(Subs);
            return sb.ToString();
        }

        public static string RestoreSql()
        {
            StringBuilder sb = new StringBuilder("update mom_moallocate set ");
            for (int i = 0; i < Restore.Length; i++)
            {
                sb.Append(Restore[i]).Append("=?, ");
            }
            sb.Append("UpperMoQty=convert(decimal(28,6), ?) where AllocateId=?");
            return sb.ToString();
        }

        // 非缺省的 Guard 列名（含 EndDemDate）；空表示可以改。
        public static List<string> NotDefault(Dictionary<string, string> keep)
        {
            List<string> bad = new List<string>();
            for (int i = 0; i < Guard.Length; i++)
            {
                string value;
                keep.TryGetValue(Guard[i], out value);
                if (!IsDefault(value))
                {
                    bad.Add(Guard[i]);
                }
            }
            string start;
            string end;
            keep.TryGetValue("StartDemDate", out start);
            keep.TryGetValue("EndDemDate", out end);
            if (start != end)
            {
                bad.Add("EndDemDate");
            }
            return bad;
        }

        static bool IsDefault(string value)
        {
            if (value == null || value.Length == 0 || value == NullMark)
            {
                return true;
            }
            decimal n;
            return decimal.TryParse(value, System.Globalization.NumberStyles.Number, System.Globalization.CultureInfo.InvariantCulture, out n)
                && n == 0m;
        }

        public static Dictionary<string, string> Read(Dictionary<string, object> row)
        {
            Dictionary<string, string> keep = new Dictionary<string, string>();
            for (int i = 0; i < Cols.Length; i++)
            {
                keep[Cols[i][0]] = CoRows.Col(row, Prefix + Cols[i][0]);
            }
            return keep;
        }

        // 不相同的列名（数值按十进制比，去掉尾零差异）。
        public static List<string> Changed(Dictionary<string, string> before, Dictionary<string, string> after)
        {
            List<string> bad = new List<string>();
            for (int i = 0; i < Cols.Length; i++)
            {
                string col = Cols[i][0];
                string a;
                string b;
                before.TryGetValue(col, out a);
                after.TryGetValue(col, out b);
                if (!Same(a ?? "", b ?? ""))
                {
                    bad.Add(col);
                }
            }
            return bad;
        }

        static bool Same(string a, string b)
        {
            if (a == b)
            {
                return true;
            }
            decimal x;
            decimal y;
            return decimal.TryParse(a, System.Globalization.NumberStyles.Number, System.Globalization.CultureInfo.InvariantCulture, out x)
                && decimal.TryParse(b, System.Globalization.NumberStyles.Number, System.Globalization.CultureInfo.InvariantCulture, out y)
                && x == y;
        }
    }
}
