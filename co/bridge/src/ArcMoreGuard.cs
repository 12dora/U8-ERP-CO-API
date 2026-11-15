using System;
using System.Collections.Generic;

namespace U8Co
{
    // 计量单位组、结算方式等七类档案新增、修改前的校验（删除前的检查在 ArcRefs）。
    // 分级档案（结算方式、收发类别、地区分类）：新编码必须正好落在编码方案某一级末尾，上级要存在；
    // 上级是末级且已被引用时不能加下级（409）。收发类别的收发标志随上级。
    // 未覆盖：EAI 新增下级后是否自己把上级的末级标志改成 0（桥不改库）；采购类型 / 销售类型的默认值 bdefau
    // 设成 1 时 U8 是否自己清掉其他类型的默认标志。
    internal static class ArcMoreGuard
    {
        const string RuleSql = "SELECT CODINGRULE FROM GradeDef_Base WHERE KEYWORD=? AND iYear=0";
        const string RdSql = "SELECT bRdEnd AS leaf FROM Rd_Style WHERE cRdCode=?";
        const string UnitSql = "SELECT TOP 1 cComunitCode FROM ComputationUnit WHERE cGroupCode=?";
        // U8：计量单位分组最多只能有一个无换算单位组（iGroupType=0）。
        const string NoConvSql = "SELECT TOP 1 cGroupCode FROM ComputationGroup WHERE iGroupType=0 AND cGroupCode<>?";

        public static void Check(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            bool create = req.Op == "create";
            switch (req.Kind.Name)
            {
                case ArcKindRwMore.UnitGroup:
                    UnitGroup(conn, req, bag, row);
                    break;
                case ArcKindRwMore.PurchaseType:
                case ArcKindRwMore.SaleType:
                    RdRef(conn, req, bag, row);
                    break;
                case ArcKindRwMore.Settle:
                case ArcKindRwMore.Rd:
                case ArcKindRwMore.District:
                    if (create)
                    {
                        Graded(conn, req, bag);
                    }
                    break;
            }
        }

        // 换算类型 type：0 无换算、1 固定换算、2 浮动换算。新增必填；组里已有计量单位时不能改。
        // 无换算组只能有一个（实测 U8 原文「计量单位分组最多只能有一个无换算单位组！」），新增或改成 0 时先查，409。
        static void UnitGroup(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            string type = GroupType(req, bag);
            if (req.Op != "create" && !ArcGuard.Changed(req, row, "type"))
            {
                return;
            }
            if (type == "0" && Rows.Scalar(conn, NoConvSql, new object[] { req.Code }) != null)
            {
                throw ArcGuard.State("计量单位分组最多只能有一个无换算单位组！");
            }
            if (req.Op == "create")
            {
                return;
            }
            string unit = Rows.Scalar(conn, UnitSql, new object[] { req.Code });
            if (unit != null)
            {
                throw ArcGuard.State("计量单位组 " + req.Code + " 已有计量单位 " + unit.Trim() + "，不能修改换算类型");
            }
        }

        static string GroupType(ArcReq req, ArcBag bag)
        {
            string type = (bag.Get(req.Map.Canon("type")) ?? "").Trim();
            if (req.Op == "create" && type.Length == 0)
            {
                throw ArcReq.Bad("缺少字段 type（换算类型：0 无换算、1 固定换算、2 浮动换算）", "fields.type");
            }
            if (type != "0" && type != "1" && type != "2")
            {
                throw ArcReq.Bad("换算类型 type 只能是 0 无换算、1 固定换算、2 浮动换算", "fields.type");
            }
            return type;
        }

        // 入（出）库类别 rstype_code：新增时给了、或修改时改了，必须是存在的末级收发类别。
        static void RdRef(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            string code = (bag.Get(req.Map.Canon("rstype_code")) ?? "").Trim();
            bool check = req.Op == "create" ? code.Length > 0 : ArcGuard.Changed(req, row, "rstype_code") && code.Length > 0;
            if (!check)
            {
                return;
            }
            string leaf = Rows.Scalar(conn, RdSql, new object[] { code });
            if (leaf == null)
            {
                throw ArcReq.Bad("收发类别 " + code + " 不存在", "fields.rstype_code");
            }
            if (leaf.Trim() != "1")
            {
                throw ArcReq.Bad("收发类别 " + code + " 不是末级，不能作为入（出）库类别", "fields.rstype_code");
            }
        }

        static void Graded(object conn, ArcReq req, ArcBag bag)
        {
            ArcKind k = req.Kind;
            string rule = Rows.Scalar(conn, RuleSql, new object[] { k.Grade });
            Dictionary<string, string> parent = null;
            if (rule != null)
            {
                parent = Parent(conn, req, rule.Trim());
            }
            if (k.Name == ArcKindRwMore.Rd)
            {
                RdFlag(req, bag, parent);
            }
        }

        // 返回上级的当前行（一级返回 null）。上级是末级且已被引用时 409。
        static Dictionary<string, string> Parent(object conn, ArcReq req, string rule)
        {
            int parentLen = ArcPos.ParentLength(rule, req.Code.Length);
            if (parentLen < 0)
            {
                throw ArcReq.Bad("编码 " + req.Code + " 不符合编码方案 " + rule + "（每位是一级的长度）", "code");
            }
            if (parentLen == 0)
            {
                return null;
            }
            string code = req.Code.Substring(0, parentLen);
            Dictionary<string, string> up = ArcRead.Row(conn, req.Kind, code);
            if (up == null)
            {
                throw ArcReq.Bad("上级 " + code + " 不存在", "code");
            }
            string leaf = Cell(up, req.Map.Column(req.Map.Canon(req.Kind.EndTag)));
            string used = leaf == "1" ? ArcRefs.Used(conn, req.Kind.Name, code) : null;
            if (used != null)
            {
                throw ArcGuard.State("上级 " + code + " 是末级且已被" + used + "使用，不能增加下级");
            }
            return up;
        }

        // 收发标志 rsflag（1 收、0 发）：下级没给取上级的，给了必须与上级相同；一级必须给。
        static void RdFlag(ArcReq req, ArcBag bag, Dictionary<string, string> parent)
        {
            string tag = req.Map.Canon("rsflag");
            string flag = ArcGuard.Flag(bag, req, "rsflag");
            string up = parent == null ? null : Cell(parent, "bRdFlag");
            if (flag == null && up == null)
            {
                throw ArcReq.Bad("缺少字段 rsflag（收发标志：1 收、0 发）", "fields.rsflag");
            }
            if (flag != null && up != null && !string.Equals(flag, up, StringComparison.Ordinal))
            {
                throw ArcReq.Bad("收发标志 rsflag 必须与上级相同（" + up + "）", "fields.rsflag");
            }
            if (tag != null)
            {
                bag.Put(tag, flag ?? up);
            }
        }

        static string Cell(Dictionary<string, string> row, string column)
        {
            string value;
            if (row == null || column == null || !row.TryGetValue(column, out value) || value == null)
            {
                return null;
            }
            return value.Trim();
        }
    }
}
