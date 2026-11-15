using System;
using System.Collections.Generic;

namespace U8Co
{
    // 无来源销售出库单（vouchers/create，sale_out）：USERPCO.Insert("32")，表头固定 csource=库存、cbustype=普通销售、
    // brdflag=0、vt_id=87（StockDom.FillHead），表体不挂发货单（iDLsID 为空），U8 不回写销售管理的任何单据。
    // 只给没有启用销售管理的账套用：启用了销售管理的，销售出库单须参照发货单生成（vouchers/generate）。
    // 顺序：名单、必填、数量 → 账套选项 409 → 档案、货位、批次预检 → StockCo.Create（同其他出库）。
    // 删除走 StockCo.Delete（RefuseShip 放行来源库存）；修改见 StockEditSrc。
    internal static class StockSaleOut
    {
        const string OptSql = "select cValue from AccInformation where cSysID=? and cName=?";
        const string CusSql = "select cCusCode from Customer where cCusCode=?";
        const string DeptSql = "select cDepCode from Department where cDepCode=?";
        const string StSql = "select cSTCode from SaleType where cSTCode=?";
        const string RdSql = "select convert(varchar(5), isnull(bRdFlag,0)) as flag, convert(varchar(5), isnull(bRdEnd,0)) as leaf"
            + " from Rd_Style where cRdCode=?";
        const string InvSql = "select cInvCode, convert(varchar(5), isnull(bInvBatch,0)) as batch,"
            + " convert(varchar(5), isnull(bInvQuality,0)) as mass from Inventory where cInvCode=?";

        static readonly string[] HeadRequired = new string[] { "cwhcode", "ccuscode", "cdepcode" };
        static readonly string[] LineRequired = new string[] { "cinvcode", "iquantity" };

        internal static string[] RequiredHead()
        {
            return (string[])HeadRequired.Clone();
        }

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            Check(kind, head, lines);
            RefuseOption(ctx.Conn);
            CheckArchives(ctx.Conn, head);
            StockPurInPos.CheckCreate(ctx.Conn, head, lines);
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    CheckInventory(ctx.Conn, (Dictionary<string, object>)lines[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return StockCo.Create(ctx, kind, head, lines);
        }

        // 不连库的校验（--selftest 直接调）：字段名单同 StockDom（meta 也用它）、必填、行数、数量。
        internal static void Check(VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                throw BridgeException.BadField("head", "表头必须是对象");
            }
            CheckSide(kind, head, true, "head");
            RequireAll(head, HeadRequired, "head");
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                string at = FieldPath.Item("lines", i);
                if (line == null)
                {
                    throw BridgeException.BadField(at, "表体行不是对象");
                }
                CheckSide(kind, line, false, at);
                RequireAll(line, LineRequired, at);
                try
                {
                    SrcLessReq.Qty(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        // 启用了销售管理（SA 启用日期非空）的账套，U8 的销售出库单只能参照发货单生成；
        // 库存选项「销售出库单由销售系统生成」（ST.bSAcreat）打开时也不能在库存里录入。都在调用 U8 之前 409。
        internal static void RefuseOption(object conn)
        {
            string start = Rows.Scalar(conn, OptSql, new object[] { "SA", "dSaleStartDate" });
            if (start != null && start.Trim().Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "账套已启用销售管理，销售出库单须参照发货单生成");
            }
            if (SrcLess.IsOn(conn, "ST", "bSAcreat", false))
            {
                throw new BridgeException(409, "state_mismatch", "库存选项设置了销售出库单由销售系统生成");
            }
        }

        // 仓库在 StockPurInPos.CheckCreate 里查；收发类别须是出库的末级。
        internal static void CheckArchives(object conn, Dictionary<string, object> head)
        {
            Exists(conn, CusSql, head, "ccuscode", "客户不存在：");
            Exists(conn, DeptSql, head, "cdepcode", "部门不存在：");
            Exists(conn, StSql, head, "cstcode", "销售类型不存在：");
            string rd = CoRows.Col(head, "crdcode");
            if (rd.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, RdSql, new object[] { rd });
            if (row == null || CoRows.Col(row, "flag") != "0" || CoRows.Col(row, "leaf") != "1")
            {
                throw BridgeException.BadField("head.crdcode", "收发类别无效（须为出库类的末级）：" + rd);
            }
        }

        static void Exists(object conn, string sql, Dictionary<string, object> head, string name, string message)
        {
            string code = CoRows.Col(head, name);
            if (code.Length > 0 && Rows.One(conn, sql, new object[] { code }) == null)
            {
                throw BridgeException.BadField("head." + name, message + code);
            }
        }

        // 同无来源材料出库（MfgGen.FreeLine）：批次管理的必须带批号，非批次管理的不能带；保质期管理的本期不支持。
        static void CheckInventory(object conn, Dictionary<string, object> line)
        {
            string inv = CoRows.Col(line, "cinvcode");
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv });
            if (row == null)
            {
                throw BridgeException.BadField("lines.cinvcode", "存货不存在：" + inv);
            }
            if (CoRows.Col(row, "mass") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 启用保质期管理，本期不支持无来源销售出库");
            }
            bool batch = CoRows.Col(row, "batch") == "1";
            string code = CoRows.Col(line, "cbatch");
            if (batch && code.Length == 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 启用批次管理，必须填批号 cbatch");
            }
            if (!batch && code.Length > 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 未启用批次管理，不能填批号");
            }
        }

        static void CheckSide(VoucherKind kind, Dictionary<string, object> map, bool head, string at)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object> kv in map)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!StockDom.MetaAllowed(kind, head, low))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, kv.Key), "不能设置字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (!seen.Add(low))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, kv.Key), "字段重复 " + kv.Key);
                }
            }
        }

        static void RequireAll(Dictionary<string, object> map, string[] names, string at)
        {
            for (int i = 0; i < names.Length; i++)
            {
                if (CoRows.Col(map, names[i]).Length == 0)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, names[i]), "必须填写 " + names[i]);
                }
            }
        }
    }
}
