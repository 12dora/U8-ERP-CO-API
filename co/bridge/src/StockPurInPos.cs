using System;
using System.Collections.Generic;

namespace U8Co
{
    // 无来源采购入库单的账套选项与货位预检，调用 U8 之前抛出。
    // 货位只写表体 cPosition；货位台账 InvPosition 由 U8 自己写，桥不写。
    // 货位 DOM 传空，U8 在保存时写 InvPosition，已在测试账套核对。
    internal static class StockPurInPos
    {
        const string HavePoSql = "select cValue from AccInformation where cSysID='PU' and cName='bPTHavePO'";
        const string WhSql = "select cWhCode, convert(varchar(5), isnull(bWhPos,0)) as whpos from Warehouse where cWhCode=?";
        const string PosSql = "select cPosCode, convert(varchar(5), isnull(bPosEnd,0)) as posend from Position"
            + " where cPosCode=? and cWhCode=?";
        const string DocWhSql = "select cWhCode from RdRecord01 where ID=?";

        // 采购选项「普通业务必有订单」打开时 U8 保存会拒绝手工录入的存货（蓝字）。
        // 红字退库不调用本方法：该选项下 U8 客户端仍可手工录入无来源红字采购入库（见 HavePoRefuses）。
        public static void RefuseHavePo(object conn)
        {
            string value = Rows.Scalar(conn, HavePoSql, new object[0]);
            if (HavePoRefuses(value, false))
            {
                throw new BridgeException(409, "state_mismatch", "账套设置了普通业务必有订单，不能无来源录入采购入库单");
            }
        }

        // 选项值为 1 / True 且是蓝字时拒绝；红字一律放行（选项打开的账套里有用户在 U8 客户端录入的无来源红字退库）。
        internal static bool HavePoRefuses(string value, bool red)
        {
            if (red)
            {
                return false;
            }
            string text = value == null ? "" : value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        // 仓库存在并返回是否启用货位管理。
        public static bool WhPos(object conn, string wh)
        {
            Dictionary<string, object> row = Rows.One(conn, WhSql, new object[] { wh });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "仓库不存在：" + wh);
            }
            return CoRows.FlagOf(row, "whpos");
        }

        // 新增：逐行核对货位。
        public static void CheckCreate(object conn, Dictionary<string, object> head, object[] lines)
        {
            string wh = CoRows.Col(head, "cwhcode");
            if (wh.Length == 0 || lines == null)
            {
                return;
            }
            bool pos = WhPos(conn, wh);
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line != null)
                {
                    CheckLine(conn, wh, pos, line, true);
                }
            }
        }

        // 修改：货位管理的单据不能换仓库；新增行必须有货位；改行送了货位就核对。
        public static void CheckEdit(object conn, int id, Dictionary<string, object> head, object[] lines)
        {
            string wh = Values.Text(Rows.Scalar(conn, DocWhSql, new object[] { id })).Trim();
            bool pos = wh.Length > 0 && WhPos(conn, wh);
            RefuseWhChange(conn, wh, pos, head);
            if (lines == null || wh.Length == 0)
            {
                return;
            }
            for (int i = 0; i < lines.Length; i++)
            {
                CheckEditLine(conn, wh, pos, lines[i] as Dictionary<string, object>);
            }
        }

        static void RefuseWhChange(object conn, string wh, bool pos, Dictionary<string, object> head)
        {
            string next = CoRows.Col(head, "cwhcode");
            if (next.Length == 0 || string.Equals(next, wh, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            if (pos || WhPos(conn, next))
            {
                throw new BridgeException(409, "state_mismatch", "货位管理仓库的单据不能换仓库，请删除后重建");
            }
        }

        static void CheckEditLine(object conn, string wh, bool pos, Dictionary<string, object> line)
        {
            if (line == null)
            {
                return;
            }
            string op = CoRows.Col(line, "op");
            if (op == "add")
            {
                CheckLine(conn, wh, pos, line, true);
            }
            else if (op == "update" && StockPurIn.Sent(line, "cposition"))
            {
                CheckLine(conn, wh, pos, line, false);
            }
        }

        // 材料出库、产成品入库（MfgGen.CheckPositions）复用。
        internal static void CheckLine(object conn, string wh, bool pos, Dictionary<string, object> line, bool required)
        {
            string code = CoRows.Col(line, "cposition");
            if (!pos)
            {
                if (code.Length > 0)
                {
                    throw new BridgeException(400, "bad_request", "仓库未启用货位管理，不能填货位 cposition");
                }
                return;
            }
            if (code.Length == 0)
            {
                if (required || StockPurIn.Sent(line, "cposition"))
                {
                    throw new BridgeException(400, "bad_request", "仓库有货位管理，必须指定货位 cposition");
                }
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, PosSql, new object[] { code, wh });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "货位不存在或不属于仓库 " + wh + "：" + code);
            }
            if (!CoRows.FlagOf(row, "posend"))
            {
                throw new BridgeException(400, "bad_request", "货位不是末级：" + code);
            }
        }
    }
}
