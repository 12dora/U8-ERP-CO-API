using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 来源为库存的销售出库单（32，未启用销售管理的账套手工录入）修改。闸门同来源发货单的（未审核、未记账、
    // 非红字非期初、没有开票、审批流），另拒绝换仓库（库存选项 bCanModifySaleoutWh「普通销售类型销售出库单允许修改仓库」
    // 各账套均为否）和清空客户、部门；新增行按仓库货位、存货批次预检，再按其他出库的口径交 StockEdit.Update（可增删行）。
    // 表头改了客户、部门、销售类型、收发类别的，同新增查档案（StockSaleOut.CheckArchives）；新增行的 400 带 lines[i] 路径。
    // 表体没有发货单关联（iDLsID 为空），不核对、不回写发货单累计出库。其他来源 409。
    internal static partial class StockEditSrc
    {
        const string SaleWhSql = "select cWhCode from {0} where {1}=?";
        const string SaleArcSql = "select cCusCode as ccuscode, cDepCode as cdepcode, cSTCode as cstcode, cRdCode as crdcode "
            + "from {0} where {1}=?";
        static readonly string[] SaleArcCols = new string[] { "ccuscode", "cdepcode", "cstcode", "crdcode" };
        const string SaleInvSql = "select cInvCode, convert(varchar(5), isnull(bInvBatch,0)) as batch,"
            + " convert(varchar(5), isnull(bInvQuality,0)) as mass from Inventory where cInvCode=?";

        static ApiResult SaleOutStock(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> before,
            Dictionary<string, object> head, object[] lines)
        {
            string source = StockMsg.Col(before, "cSource");
            if (source != "库存")
            {
                throw new BridgeException(409, "state_mismatch", "只能修改来源为发货单或库存的销售出库单");
            }
            Gate(ctx, kind, id, before, source);
            string sql = string.Format(CultureInfo.InvariantCulture, SaleWhSql, kind.HeadTable, kind.IdColumn);
            string raw = Rows.Scalar(ctx.Conn, sql, new object[] { id });
            string wh = raw == null ? "" : raw.Trim();
            RefuseSaleHead(wh, head);
            StockSaleOut.CheckArchives(ctx.Conn, ChangedArchives(ctx.Conn, kind, id, head));
            CheckSaleLines(ctx.Conn, wh, lines);
            return StockEdit.Update(ctx, kind, id, head, lines);
        }

        static bool SaleOutMeta(VoucherKind kind, bool head, string lowerField)
        {
            string low = lowerField == null ? "" : lowerField.Trim().ToLowerInvariant();
            if (head && low == "cwhcode")
            {
                return false;
            }
            return SrcField(head, low) || StockDom.MetaAllowed(kind, head, low);
        }

        static void RefuseSaleHead(string wh, Dictionary<string, object> head)
        {
            if (StockPurIn.Sent(head, "cwhcode")
                && !string.Equals(CoRows.Col(head, "cwhcode"), wh, StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(409, "state_mismatch", "不能修改销售出库单的仓库");
            }
            if (StockPurIn.Sent(head, "ccuscode") && CoRows.Col(head, "ccuscode").Length == 0)
            {
                throw BridgeException.BadField("head.ccuscode", "销售出库单必须有客户");
            }
            if (StockPurIn.Sent(head, "cdepcode") && CoRows.Col(head, "cdepcode").Length == 0)
            {
                throw BridgeException.BadField("head.cdepcode", "销售出库单必须有部门");
            }
        }

        // 表头送来且与现值不同（去空格、忽略大小写）的档案字段；空值留给 RefuseSaleHead 和 U8。
        static Dictionary<string, object> ChangedArchives(object conn, VoucherKind kind, int id, Dictionary<string, object> head)
        {
            Dictionary<string, object> changed = new Dictionary<string, object>();
            string sql = string.Format(CultureInfo.InvariantCulture, SaleArcSql, kind.HeadTable, kind.IdColumn);
            Dictionary<string, object> now = Rows.One(conn, sql, new object[] { id });
            for (int i = 0; i < SaleArcCols.Length; i++)
            {
                string name = SaleArcCols[i];
                string want = StockMsg.Col(head, name);
                if (StockPurIn.Sent(head, name) && want.Length > 0
                    && !string.Equals(want, StockMsg.Col(now, name), StringComparison.OrdinalIgnoreCase))
                {
                    changed[name] = want;
                }
            }
            return changed;
        }

        // 只查新增行：修改已有行的货位由 StockPosGuard.RefuseLineChange 拒绝，存货不能换（StockEdit.RefuseCode）。
        static void CheckSaleLines(object conn, string wh, object[] lines)
        {
            if (lines == null || wh.Length == 0)
            {
                return;
            }
            bool pos = StockPurInPos.WhPos(conn, wh);
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line == null || CoRows.Col(line, "op") != "add")
                {
                    continue;
                }
                try
                {
                    CheckSaleAdd(conn, wh, pos, line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        // 存货存在；批次管理的必须带批号，非批次管理的不能带；保质期管理的本期不收（同无来源新增）。货位照仓库设置。
        static void CheckSaleAdd(object conn, string wh, bool pos, Dictionary<string, object> line)
        {
            string inv = CoRows.Col(line, "cinvcode");
            if (inv.Length == 0)
            {
                // StockEdit.CheckAdd 报「表体行缺少存货」。
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, SaleInvSql, new object[] { inv });
            if (row == null)
            {
                throw BridgeException.BadField("lines.cinvcode", "存货不存在：" + inv);
            }
            if (CoRows.Col(row, "mass") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 启用保质期管理，本期不支持无来源销售出库");
            }
            bool batch = CoRows.Col(row, "batch") == "1";
            string lot = CoRows.Col(line, "cbatch");
            if (batch && lot.Length == 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 启用批次管理，必须填批号 cbatch");
            }
            if (!batch && lot.Length > 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 未启用批次管理，不能填批号");
            }
            StockPurInPos.CheckLine(conn, wh, pos, line, true);
        }
    }
}
