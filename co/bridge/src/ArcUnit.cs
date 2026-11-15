using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 计量单位（ComputationUnit）新增、修改、删除前的校验。计量单位组的换算类型 ComputationGroup.iGroupType：
    // 0 无换算（组里每个单位都是主计量单位，没有换算率）；1 固定换算、2 浮动换算（组里一个主计量单位，换算率 1，
    // 其余是辅计量单位，换算率大于 0）。U8 的写法：无换算组的单位 bMainUnit=1、iChangRate 为空、iNumber 从 0 递增；
    // 固定换算组的主计量单位 iNumber 为空、换算率 1，辅计量单位 iNumber 从 1 递增。
    // 未覆盖：EAI 是否自己补主计量单位标志、序号；已被存货使用的计量单位 U8 是否允许改换算率（桥先拒绝）。
    internal static class ArcUnit
    {
        internal const string Name = "unit";
        const string GroupSql = "SELECT iGroupType AS kind FROM ComputationGroup WHERE cGroupCode=?";
        const string MainSql = "SELECT TOP 1 cComunitCode AS code FROM ComputationUnit WHERE cGroupCode=? AND bMainUnit=1";
        const string SerialSql = "SELECT MAX(iNumber) AS n FROM ComputationUnit WHERE cGroupCode=?";
        const string OtherSql = "SELECT TOP 1 cComunitCode AS code FROM ComputationUnit WHERE cGroupCode=? AND cComunitCode<>?";
        // 存货档案里引用计量单位的列：主计量、辅计量（库存默认）、采购、销售、库存、成本、生产、零售。
        const string UsedSql = "SELECT TOP 1 cInvCode FROM Inventory WHERE cComUnitCode=? OR cAssComUnitCode=? OR cPUComUnitCode=?"
            + " OR cSAComUnitCode=? OR cSTComUnitCode=? OR cCAComUnitCode=? OR cProductUnit=? OR cShopUnit=?";

        public static void Check(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            if (req.Op == "create")
            {
                Create(conn, req, bag);
                return;
            }
            Update(conn, req, bag, row);
        }

        static void Create(object conn, ArcReq req, ArcBag bag)
        {
            string group = ArcGuard.Need(bag, req, "group_code", "group_code（计量单位组）");
            string kind = GroupKind(conn, group);
            string main = ArcGuard.Flag(bag, req, "main_flag");
            string rate = Text(bag, req, "changerate");
            if (kind == "0")
            {
                if (rate != null || main == "0")
                {
                    throw ArcReq.Bad("计量单位组 " + group + " 是无换算组：单位都是主计量单位，不能设置 changerate、main_flag=0", "fields.changerate");
                }
                ArcGuard.Put(bag, req, "main_flag", "1");
                Serial(conn, req, bag, group, 0);
                return;
            }
            string owner = Rows.Scalar(conn, MainSql, new object[] { group });
            if (owner == null)
            {
                // 组里第一个单位就是主计量单位，换算率 1。
                if (main == "0" || (rate != null && Rate(rate) != 1m))
                {
                    throw ArcReq.Bad("计量单位组 " + group + " 还没有主计量单位：第一个单位必须是主计量单位（main_flag=1、changerate=1）", "fields.main_flag");
                }
                ArcGuard.Put(bag, req, "main_flag", "1");
                ArcGuard.Put(bag, req, "changerate", "1");
                return;
            }
            if (main == "1")
            {
                throw ArcReq.Bad("计量单位组 " + group + " 已有主计量单位 " + owner.Trim() + "，新单位只能是辅计量单位（main_flag=0）", "fields.main_flag");
            }
            if (rate == null)
            {
                throw ArcReq.Bad("缺少字段 changerate（辅计量单位的换算率，大于 0）", "fields.changerate");
            }
            Rate(rate);
            ArcGuard.Put(bag, req, "main_flag", "0");
            Serial(conn, req, bag, group, 1);
        }

        // 不能换组、不能改主计量单位标志；换算率只给换算组的辅计量单位改，且该单位没有被存货使用。
        static void Update(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            if (ArcGuard.Changed(req, row, "group_code"))
            {
                throw ArcReq.Bad("不能修改计量单位所属的计量单位组 group_code", "fields.group_code");
            }
            string current = Cell(row, "bMainUnit") == "1" ? "1" : "0";
            string main = ArcGuard.Flag(bag, req, "main_flag");
            if (req.Fields.Has(req.Map.Canon("main_flag")) && main != current)
            {
                throw ArcReq.Bad("不能修改主计量单位标志 main_flag", "fields.main_flag");
            }
            if (!RateChanged(req, row))
            {
                return;
            }
            string group = Cell(row, "cGroupCode");
            if (GroupKind(conn, group) == "0" || current == "1")
            {
                throw ArcReq.Bad("只有换算组的辅计量单位能改换算率 changerate（主计量单位是 1，无换算组没有换算率）", "fields.changerate");
            }
            Rate(Text(bag, req, "changerate") ?? "");
            string inv = Used(conn, req.Code);
            if (inv != null)
            {
                throw ArcGuard.State("计量单位 " + req.Code + " 已被存货 " + inv + " 使用，不能修改换算率");
            }
        }

        public static void CheckDelete(object conn, string code, Dictionary<string, string> row)
        {
            string inv = Used(conn, code);
            if (inv != null)
            {
                throw ArcGuard.State("计量单位 " + code + " 已被存货 " + inv + " 使用，不能删除");
            }
            string group = Cell(row, "cGroupCode");
            if (Cell(row, "bMainUnit") != "1" || group.Length == 0 || KindOrNone(conn, group) == "0")
            {
                return;
            }
            string other = Rows.Scalar(conn, OtherSql, new object[] { group, code });
            if (other != null)
            {
                throw ArcGuard.State("计量单位 " + code + " 是计量单位组 " + group + " 的主计量单位，组里还有 " + other.Trim() + "，不能删除");
            }
        }

        static string GroupKind(object conn, string group)
        {
            string kind = Rows.Scalar(conn, GroupSql, new object[] { group });
            if (kind == null)
            {
                throw ArcReq.Bad("计量单位组 " + group + " 不存在", "fields.group_code");
            }
            return kind.Trim();
        }

        // 删除时组已不存在就按无换算处理（不再查同组单位），不报 400。
        static string KindOrNone(object conn, string group)
        {
            string kind = Rows.Scalar(conn, GroupSql, new object[] { group });
            return kind == null ? "0" : kind.Trim();
        }

        // 序号 SerialNum 没给时取组里最大序号 + 1；组里还没有序号时从 first 开始。
        static void Serial(object conn, ArcReq req, ArcBag bag, string group, int first)
        {
            string tag = req.Map.Canon("SerialNum");
            if (tag == null || bag.Has(tag))
            {
                return;
            }
            string max = Rows.Scalar(conn, SerialSql, new object[] { group });
            int n;
            int next = max != null && int.TryParse(max.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? n + 1 : first;
            bag.Put(tag, next.ToString(CultureInfo.InvariantCulture));
        }

        // 换算率按数值比较（库里是 25.000000，调用方可能写 25）。
        static bool RateChanged(ArcReq req, Dictionary<string, string> row)
        {
            string tag = req.Map.Canon("changerate");
            if (tag == null || !req.Fields.Has(tag))
            {
                return false;
            }
            decimal now;
            string want = req.Fields.Get(tag) ?? "";
            bool known = decimal.TryParse(Cell(row, "iChangRate"), NumberStyles.Float, CultureInfo.InvariantCulture, out now);
            return !known || Rate(want) != now;
        }

        static decimal Rate(string text)
        {
            decimal rate;
            if (!decimal.TryParse(text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out rate) || rate <= 0m)
            {
                throw ArcReq.Bad("换算率 changerate 必须是大于 0 的数", "fields.changerate");
            }
            return rate;
        }

        static string Used(object conn, string code)
        {
            object[] args = new object[] { code, code, code, code, code, code, code, code };
            string inv = Rows.Scalar(conn, UsedSql, args);
            return inv == null ? null : inv.Trim();
        }

        static string Text(ArcBag bag, ArcReq req, string tag)
        {
            string value = bag.Get(req.Map.Canon(tag));
            return value == null || value.Trim().Length == 0 ? null : value.Trim();
        }

        static string Cell(Dictionary<string, string> row, string column)
        {
            string value;
            if (row == null || !row.TryGetValue(column, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }
    }
}
