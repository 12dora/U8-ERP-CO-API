using System;
using System.Collections.Generic;

namespace U8Co
{
    // 货位（Position）新增、修改、删除前的校验。U8 的规则：仓库要启用货位管理（Warehouse.bWhPos=1）；编码按编码方案
    // （GradeDef_Base 的 positionclass，如 2222）分级，下级与上级同一仓库；有存货记录的末级货位不能再加下级；
    // 有下级或存货记录的货位不能删除。未覆盖：EAI 新增下级后是否自己把上级的末级标志改成 0（桥不改库）。
    internal static class ArcPos
    {
        internal const string Name = "position";
        const string WhSql = "SELECT bWhPos AS pos FROM Warehouse WHERE cWhCode=?";
        const string RuleSql = "SELECT CODINGRULE FROM GradeDef_Base WHERE KEYWORD='positionclass' AND iYear=0";
        const string ParentSql = "SELECT cWhCode AS wh, bPosEnd AS leaf FROM Position WHERE cPosCode=?";
        // 存货记录：货位存量、货位出入库记录、存货档案的默认货位、存货货位对照。
        const string StockSql = "SELECT TOP 1 u.x FROM (SELECT TOP 1 N'货位存量' AS x FROM InvPositionSum WHERE cPosCode=?"
            + " UNION ALL SELECT TOP 1 N'货位出入库记录' FROM InvPosition WHERE cPosCode=?"
            + " UNION ALL SELECT TOP 1 N'存货档案的默认货位' FROM Inventory WHERE cPosition=?"
            + " UNION ALL SELECT TOP 1 N'存货货位对照' FROM InvPosContrapose WHERE cPosCode=?) u";
        const string ChildSql = "SELECT TOP 1 cPosCode FROM Position WHERE cPosCode LIKE ? ESCAPE '\\' AND cPosCode<>?";

        public static void Check(object conn, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            if (req.Op != "create")
            {
                if (ArcGuard.Changed(req, row, "warehouse_code"))
                {
                    throw ArcReq.Bad("货位不能改所属仓库 warehouse_code，请删除后在新仓库下新增");
                }
                return;
            }
            string wh = ArcGuard.Need(bag, req, "warehouse_code", "warehouse_code（所属仓库）");
            string pos = Rows.Scalar(conn, WhSql, new object[] { wh });
            if (pos == null)
            {
                throw ArcReq.Bad("仓库 " + wh + " 不存在");
            }
            if (pos != "1")
            {
                throw ArcReq.Bad("仓库 " + wh + " 没有启用货位管理");
            }
            CheckParent(conn, req.Code, wh);
        }

        // 编码方案每位是一级的长度；编码必须正好落在某一级末尾。第 n 级（n>1）的上级是前 n-1 级的编码。
        static void CheckParent(object conn, string code, string wh)
        {
            string rule = Rows.Scalar(conn, RuleSql, null);
            if (rule == null)
            {
                return;
            }
            int parentLen = ParentLength(rule.Trim(), code.Length);
            if (parentLen < 0)
            {
                throw ArcReq.Bad("货位编码 " + code + " 不符合编码方案 " + rule.Trim() + "（每位是一级的长度）");
            }
            if (parentLen == 0)
            {
                return;
            }
            string parent = code.Substring(0, parentLen);
            Dictionary<string, object> up = Rows.One(conn, ParentSql, new object[] { parent });
            if (up == null)
            {
                throw ArcReq.Bad("上级货位 " + parent + " 不存在");
            }
            string upWh = (ArcRead.Cell(up, "wh") ?? "").Trim();
            if (!string.Equals(upWh, wh, StringComparison.OrdinalIgnoreCase))
            {
                throw ArcReq.Bad("上级货位 " + parent + " 属于仓库 " + upWh + "，下级货位必须在同一仓库");
            }
            string used = ArcRead.Cell(up, "leaf") == "1" ? Stock(conn, parent) : null;
            if (used != null)
            {
                throw ArcGuard.State("上级货位 " + parent + " 是末级且已有" + used + "，不能增加下级");
            }
        }

        // 返回上级编码的长度：一级为 0，不在级次末尾为 -1。
        internal static int ParentLength(string rule, int length)
        {
            int total = 0;
            for (int i = 0; i < rule.Length; i++)
            {
                char c = rule[i];
                if (c < '1' || c > '9')
                {
                    continue;
                }
                int before = total;
                total += c - '0';
                if (total == length)
                {
                    return before;
                }
            }
            return -1;
        }

        public static void CheckDelete(object conn, string code)
        {
            string child = Rows.Scalar(conn, ChildSql, new object[] { ArcRead.Like(code, false), code });
            if (child != null)
            {
                throw ArcGuard.State("货位 " + code + " 有下级货位 " + child.Trim() + "，不能删除");
            }
            string used = Stock(conn, code);
            if (used != null)
            {
                throw ArcGuard.State("货位 " + code + " 已有" + used + "，不能删除");
            }
        }

        static string Stock(object conn, string code)
        {
            return Rows.Scalar(conn, StockSql, new object[] { code, code, code, code });
        }
    }
}
