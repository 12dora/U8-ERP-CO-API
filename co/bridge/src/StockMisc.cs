using System;
using System.Collections.Generic;

namespace U8Co
{
    // 形态转换单（15）、调拨申请单（62）、盘点单（18）的写路由分派和共用闸门。Dispatch.Handle 先问这里，
    // 返回 null 再走原来的路由表。读取仍走 VoucherRead → StockCo.Load，这里只在读取前挡住 AssemVouch 里的组装、拆卸单。
    // 登录前的闸门（Creatable / Deletable / Updatable、字段拦截）照常在 Requests 里做。
    internal static class StockMisc
    {
        const string V = "/u8co/v1/vouchers/";
        internal const string CheckKind = "stock_check";
        // 实测（测试账套）：盘点单 9 参、12 参 Verify 都回「类型不匹配生单时出错」（生成盘盈 / 盘亏其他出入库单时失败，
        // 填了 iAdInQuantity / iAdOutQuantity 也一样），审核、弃审在任何 COM 调用之前拒绝。
        internal const string NoCheckVerify = "盘点单审核暂不支持（U8 生成盘盈盘亏单时报类型不匹配），请到 U8 客户端审核";

        internal static bool Handles(VoucherKind kind)
        {
            if (kind == null || kind.Family != "st")
            {
                return false;
            }
            string st = kind.StType;
            return st == "15" || st == "62" || st == "18";
        }

        public static ApiResult Try(WorkContext ctx, string path)
        {
            if (ctx == null || ctx.Item == null)
            {
                return null;
            }
            if (Kinds.Find("transfer") == ctx.Item.Type)
            {
                return TryTransfer(ctx, path);
            }
            if (!Handles(ctx.Item.Type))
            {
                return null;
            }
            WorkItem item = ctx.Item;
            VoucherKind kind = item.Type;
            switch (path)
            {
                case V + "load":
                    RefuseOtherAssem(ctx.Conn, kind, item.Id);
                    return null;
                case V + "verify":
                    return StockCo.MiscVerify(ctx, kind, item.Id, item.Action);
                case V + "create":
                    return PuAppRoutes.StampNewId(ctx, StockCo.MiscCreate(ctx, kind, item.Head, item.Lines));
                case V + "update":
                    return StockEdit.MiscUpdate(ctx, kind, item.Id, item.Head, item.Lines);
                case V + "delete":
                    return StockCo.MiscDelete(ctx, kind, item.Id);
            }
            return null;
        }

        // 调拨单：参照调拨申请单生单（StockGenTr）；参照生成的调拨单不能修改（改数量会与申请单的累计调拨数量对不上）。
        // 其余调拨单路由返回 null，照旧走 Dispatch；删除的回退核对在 StockCo.Delete（StockGen.GuardTrUndo）。
        static ApiResult TryTransfer(WorkContext ctx, string path)
        {
            WorkItem item = ctx.Item;
            if (path == V + "generate")
            {
                return PuAppRoutes.StampNewId(ctx,
                    StockGen.TransferFromRequest(ctx, item.Type, item.Id, item.Head, item.Lines));
            }
            if (path == V + "update" && FromRequest(ctx.Conn, item.Id))
            {
                throw new BridgeException(409, "state_mismatch", "参照调拨申请单生成的调拨单不能修改，请删除后重新生成");
            }
            return null;
        }

        internal static bool FromRequest(object conn, int tvId)
        {
            string n = Rows.Scalar(conn, "select count(*) from TransVouchs where ID=? and isnull(iTRIds,0)<>0", new object[] { tvId });
            return CoRows.AsId(n) > 0;
        }

        // 预检表头。形态转换单只认 cVouchType='15'，其余（组装 13、拆卸 14）当作不存在。
        internal static Dictionary<string, object> Head(object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, HeadSql(kind), new object[] { id });
            if (row != null && kind.StType == "15" && StockMsg.Col(row, "cVouchType") != "15")
            {
                return null;
            }
            return row;
        }

        static string HeadSql(VoucherKind kind)
        {
            string sql = "select " + kind.CodeColumn + ", " + kind.VerifierColumn + ", " + kind.VerifyDateColumn
                + ", iswfcontrolled, csource";
            if (kind.StType == "15")
            {
                sql = sql + ", cVouchType";
            }
            else if (kind.StType == "18")
            {
                sql = sql + ", cWhCode";
            }
            else
            {
                sql = sql + ", cCloser" + RequestCols();
            }
            return sql + " from " + kind.HeadTable + " h where h." + kind.IdColumn + "=?";
        }

        // 调拨申请单：关闭行、已生成调拨的累计数量、调拨单行 iTRIds 指向本单（TransVouchs.iTRIds = 申请单行 autoID）。
        static string RequestCols()
        {
            return ", (select count(*) from ST_AppTransVouchs b where b.ID=h.ID"
                + " and nullif(ltrim(rtrim(b.cBCloser)), N'') is not null) as closed_lines"
                + ", (select count(*) from ST_AppTransVouchs b where b.ID=h.ID and isnull(b.iTvSumQuantity, 0)<>0) as summed"
                + ", (select count(*) from TransVouchs t inner join ST_AppTransVouchs b on b.autoID=t.iTRIds"
                + " where b.ID=h.ID) as down";
        }

        // 修改、删除、弃审共用：调拨申请单已关闭或已生成调拨单时拒绝。其他两类没有下游闸门（生成的 08/09 另查）。
        internal static void RefuseEdit(VoucherKind kind, Dictionary<string, object> head)
        {
            if (kind.StType != "62")
            {
                return;
            }
            if (StockMsg.Col(head, "cCloser").Length > 0 || Count(head, "closed_lines") > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (Count(head, "summed") > 0 || Count(head, "down") > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
        }

        // 弃审前：形态转换单、盘点单审核生成的其他出入库单若已审核，U8 弃审会失败，先说清楚（08/09 本身也不能单独弃审）。
        // 生成的单据按表头 cBusCode = 来源单号、cSource / cBusType 认；盘点路径以客户端审核为准。
        internal static void RefuseUndo(object conn, VoucherKind kind, Dictionary<string, object> head)
        {
            RefuseEdit(kind, head);
            if (kind.StType == "62")
            {
                return;
            }
            string code = StockMsg.Col(head, kind.CodeColumn);
            string filter = kind.StType == "15" ? "r.cSource=N'形态转换'" : "r.cSource like N'%盘点%'";
            string one = " r.ID from {0} r where " + filter
                + " and r.cBusCode=? and nullif(ltrim(rtrim(r.cHandler)), N'') is not null";
            string sql = "select top 1" + string.Format(one, "RdRecord08")
                + " union all select top 1" + string.Format(one, "RdRecord09");
            if (Rows.One(conn, sql, new object[] { code, code }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "生成的其他出入库单已审核，请先在 U8 里弃审");
            }
        }

        // 读取前：形态转换单的 id 若是组装单、拆卸单，按不存在处理（不存在的 id 交给 StockCo.Load 报 404）。
        internal static void RefuseOtherAssem(object conn, VoucherKind kind, int id)
        {
            if (kind.StType != "15")
            {
                return;
            }
            string type = Rows.Scalar(conn, "select cVouchType from AssemVouch where ID=?", new object[] { id });
            if (type != null && type.Trim() != "15")
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
        }

        // 08/09 由哪种单据审核生成：调拨单（StockCo.FromTransfer）、形态转换单（cSource=形态转换，cBusType 转换入库 /
        // 转换出库）、盘点单（cSource 含「盘点」或 cBusType 盘盈 / 盘亏，未经实测）。其他单据返回空串。
        internal static string MadeBy(VoucherKind kind, Dictionary<string, object> head)
        {
            if (kind == null || (kind.StType != "08" && kind.StType != "09"))
            {
                return "";
            }
            if (StockCo.FromTransfer(head))
            {
                return "调拨单";
            }
            return MadeByStock(StockMsg.Col(head, "cSource"), StockMsg.Col(head, "cBusType"));
        }

        static string MadeByStock(string source, string bus)
        {
            if (Has(source, "形态转换") || Has(bus, "转换"))
            {
                return "形态转换单";
            }
            if (Has(source, "盘点") || Has(bus, "盘盈") || Has(bus, "盘亏"))
            {
                return "盘点单";
            }
            return "";
        }

        static bool Has(string text, string part)
        {
            return text != null && text.IndexOf(part, StringComparison.Ordinal) >= 0;
        }

        static int Count(Dictionary<string, object> row, string name)
        {
            return CoRows.AsId(StockMsg.Col(row, name));
        }

        // meta 的必填字段，与 StockDom.MiscHead / MiscBody 的校验一致。
        internal static string[] RequiredHead(VoucherKind kind)
        {
            if (kind.StType == "62")
            {
                return new string[] { "cowhcode", "ciwhcode" };
            }
            return kind.StType == "18" ? new string[] { "cwhcode" } : new string[0];
        }

        internal static string[] RequiredLine(VoucherKind kind)
        {
            if (kind.StType == "15")
            {
                return new string[] { "cinvcode", "cwhcode", "bavtype", "iavquantity" };
            }
            if (kind.StType == "62")
            {
                return new string[] { "cinvcode", "itvquantity" };
            }
            return new string[] { "cinvcode", "icvcquantity" };
        }
    }
}
