using System;
using System.Collections.Generic;

namespace U8Co
{
    // 采购入库单（01）无来源新增、修改的预检：供应商、采购类型、存货属性、批号、单价键。
    // 只查不写；在调用 U8 之前抛出，不进事务。
    internal static class StockPurIn
    {
        const string VenSql = "select cVenCode, cVenExch_name, iVenTaxRate from Vendor where cVenCode=?";
        const string PtSql = "select cPTCode, cRdCode from PurchaseType where cPTCode=?";
        const string PtDefaultSql = "select top 1 cPTCode, cRdCode from PurchaseType where bDefault=1 order by cPTCode";
        const string InvSql = "select cInvCode, iTaxRate, bInvBatch, bInvQuality, iMassDate, cMassUnit,"
            + " bPropertyCheck, bPurchase from Inventory where cInvCode=?";
        const string StateSql = "select cSource, convert(varchar(5), isnull(bredvouch,0)) as red,"
            + " case when isnull(bpufirst,0)=1 or isnull(bIsSTQc,0)=1 or isnull(biafirst,0)=1 then '1' else '0' end as pufirst,"
            + " cExch_Name, convert(varchar(40), iExchRate) as exchrate from RdRecord01 where ID=?";

        internal sealed class HeadInfo
        {
            public string Exch;
            public string PtCode;
            public string RdCode;
            public string TaxRate;
        }

        internal sealed class InvInfo
        {
            public string TaxRate;
            public bool Quality;
            public string MassDate;
            public string MassUnit;
        }

        // 账套选项和货位先查，再走库存单据的通用新增（StockCo.Create → StockDom.PurHead / PurBody）。
        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            // 表头 red=true 是红字退库（StockPurInRed），其余同蓝字。
            bool red = StockCo.RedHead(head);
            head = StockCo.WithoutRed(head);
            if (red)
            {
                return StockCo.CreateRedPurIn(ctx, kind, head, lines);
            }
            StockPurInPos.RefuseHavePo(ctx.Conn);
            StockPurInPos.CheckCreate(ctx.Conn, head, lines);
            return StockCo.Create(ctx, kind, head, lines);
        }

        public static HeadInfo CheckHead(WorkContext ctx, Dictionary<string, object> head)
        {
            object conn = ctx.Conn;
            if (CoRows.Col(head, "cwhcode").Length == 0)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            if (AdoXml.WorkflowReleased(conn, "RdRecord01"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
            HeadInfo info = new HeadInfo();
            Dictionary<string, object> ven = Vendor(ctx, CoRows.Col(head, "cvencode"));
            info.Exch = ctx.HomeCurrency;
            info.TaxRate = CoRows.Col(ven, "iVenTaxRate");
            Dictionary<string, object> pt = PurchaseType(conn, CoRows.Col(head, "cptcode"), false);
            info.PtCode = CoRows.Col(pt, "cPTCode");
            info.RdCode = CoRows.Col(pt, "cRdCode");
            return info;
        }

        // 生单的采购入库（参照采购订单、来料检验单、采购退货单）不给 crdcode 时的收发类别，同 U8：
        // 来源单据采购类型的 PurchaseType.cRdCode，空则默认采购类型（bDefault=1）的；还没有就 400。
        internal static string GenRd(object conn, Dictionary<string, object> head, string ptCode)
        {
            string rd = CoRows.Col(head, "crdcode");
            if (rd.Length > 0)
            {
                return rd;
            }
            string pt = ptCode == null ? "" : ptCode.Trim();
            if (pt.Length > 0)
            {
                rd = CoRows.Col(Rows.One(conn, PtSql, new object[] { pt }), "cRdCode");
            }
            if (rd.Length == 0)
            {
                rd = CoRows.Col(Rows.One(conn, PtDefaultSql, new object[0]), "cRdCode");
            }
            if (rd.Length == 0)
            {
                throw BridgeException.BadField("head.crdcode", "必须指定收发类别")
                    .WithHint("采购类型没有设置收发类别，请在请求里给 crdcode");
            }
            return rd;
        }

        // 供应商必填且存在；只收本币（本位币，ctx.HomeCurrency）供应商。
        static Dictionary<string, object> Vendor(WorkContext ctx, string code)
        {
            if (code.Length == 0)
            {
                throw BridgeException.BadField("head.cvencode", "必须指定供应商 cvencode");
            }
            Dictionary<string, object> ven = Rows.One(ctx.Conn, VenSql, new object[] { code });
            if (ven == null)
            {
                throw BridgeException.BadField("head.cvencode", "供应商不存在：" + code);
            }
            string exch = CoRows.Col(ven, "cVenExch_name");
            if (exch.Length > 0 && exch != ctx.HomeCurrency)
            {
                throw new BridgeException(409, "state_mismatch", "外币供应商的采购入库单本期不支持");
            }
            return ven;
        }

        // 不给 cptcode 时取默认采购类型（PurchaseType.bDefault=1），收发类别缺省跟采购类型。
        static Dictionary<string, object> PurchaseType(object conn, string code, bool sent)
        {
            if (sent && code.Length == 0)
            {
                throw BridgeException.BadField("head.cptcode", "采购类型 cptcode 不能为空");
            }
            Dictionary<string, object> pt = code.Length > 0
                ? Rows.One(conn, PtSql, new object[] { code })
                : Rows.One(conn, PtDefaultSql, new object[0]);
            if (pt == null && code.Length > 0)
            {
                throw BridgeException.BadField("head.cptcode", "采购类型不存在：" + code);
            }
            if (pt == null)
            {
                throw BridgeException.BadField("head.cptcode", "没有默认采购类型，请填写 cptcode");
            }
            return pt;
        }

        public static InvInfo CheckLine(object conn, Dictionary<string, object> line)
        {
            string inv = CoRows.Col(line, "cinvcode");
            if (inv.Length == 0)
            {
                throw BridgeException.BadField("lines.cinvcode", "表体行缺少存货");
            }
            decimal qty;
            if (!StockUnits.Dec(CoRows.Col(line, "iquantity"), out qty) || qty <= 0m)
            {
                throw BridgeException.BadField("lines.iquantity", "数量必须大于 0");
            }
            CheckPrices(line);
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv });
            if (row == null)
            {
                throw BridgeException.BadField("lines.cinvcode", "存货不存在：" + inv);
            }
            RefuseInv(row, line, inv);
            return InfoOf(row);
        }

        // 修改时新增行、改行用到的存货缺省（税率、保质期）。存货不存在返回 null，交给 U8 报错。
        public static InvInfo InvOf(object conn, string inv)
        {
            if (inv == null || inv.Trim().Length == 0)
            {
                return null;
            }
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv.Trim() });
            return row == null ? null : InfoOf(row);
        }

        static InvInfo InfoOf(Dictionary<string, object> row)
        {
            InvInfo info = new InvInfo();
            info.TaxRate = CoRows.Col(row, "iTaxRate");
            info.Quality = CoRows.FlagOf(row, "bInvQuality");
            info.MassDate = CoRows.Col(row, "iMassDate");
            info.MassUnit = CoRows.Col(row, "cMassUnit");
            return info;
        }

        static void RefuseInv(Dictionary<string, object> row, Dictionary<string, object> line, string inv)
        {
            if (CoRows.FlagOf(row, "bPropertyCheck"))
            {
                throw new BridgeException(409, "state_mismatch", "该存货需来料检验，不能直接入库");
            }
            if (!CoRows.FlagOf(row, "bPurchase"))
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 不是外购属性");
            }
            RefuseLot(row, line, inv);
        }

        // 批次、保质期：批次或保质期管理的必须有批号，保质期管理的还要生产日期和失效日期。
        static void RefuseLot(Dictionary<string, object> row, Dictionary<string, object> line, string inv)
        {
            bool batch = CoRows.FlagOf(row, "bInvBatch");
            bool quality = CoRows.FlagOf(row, "bInvQuality");
            string lot = CoRows.Col(line, "cbatch");
            if ((batch || quality) && lot.Length == 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 启用批次或保质期管理，必须填批号 cbatch");
            }
            if (!batch && !quality && lot.Length > 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 未启用批次管理，不能填批号");
            }
            if (quality && (CoRows.Col(line, "dmadedate").Length == 0 || CoRows.Col(line, "dvdate").Length == 0))
            {
                throw BridgeException.BadField("lines.dmadedate", "存货 " + inv + " 启用保质期管理，必须填生产日期 dmadedate 和失效日期 dvdate");
            }
        }

        // 含税单价 ioritaxcost 与无税单价 iunitcost / 无税金额 iprice 只能走一种。
        public static void CheckPrices(Dictionary<string, object> line)
        {
            bool tax = Given(line, "ioritaxcost");
            if (tax && (Given(line, "iunitcost") || Given(line, "iprice")))
            {
                throw BridgeException.BadField("lines.ioritaxcost", "含税单价 ioritaxcost 不能和 iunitcost、iprice 同时填写");
            }
            string[] keys = new string[] { "ioritaxcost", "iunitcost", "iprice", "itaxrate" };
            for (int i = 0; i < keys.Length; i++)
            {
                if (!Given(line, keys[i]))
                {
                    continue;
                }
                decimal value;
                if (!StockUnits.Dec(CoRows.Col(line, keys[i]), out value) || value < 0m)
                {
                    throw BridgeException.BadField("lines." + keys[i], "字段 " + keys[i] + " 必须是不小于 0 的数");
                }
            }
        }

        // 修改：只改桥自己新增出来的那种单据（来源库存、蓝字、非期初、未记账、无发票和结算）。
        public static void RefuseEdit(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> before)
        {
            object conn = ctx.Conn;
            Dictionary<string, object> state = Rows.One(conn, StateSql, new object[] { id });
            if (state == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(state, "cSource") != "库存")
            {
                throw new BridgeException(409, "state_mismatch", "只能修改来源为库存的单据");
            }
            RefuseEditState(ctx, state);
            if (StockMsg.Col(before, "cbaccounter").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已记账，不能修改");
            }
            if (StockCo.Billed(conn, kind, id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
        }

        // 有来源（采购订单 / 来料检验单）的采购入库单修改（StockEditSrc）：红字、期初、外币同来源库存的口径；
        // 来源、记账、下游由 StockCo.RefuseDelete 查。
        internal static void RefuseSourcedEdit(WorkContext ctx, int id)
        {
            Dictionary<string, object> state = Rows.One(ctx.Conn, StateSql, new object[] { id });
            if (state == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseEditState(ctx, state);
        }

        static void RefuseEditState(WorkContext ctx, Dictionary<string, object> state)
        {
            if (CoRows.FlagOf(state, "red"))
            {
                throw new BridgeException(409, "state_mismatch", "红字采购入库单不支持修改");
            }
            if (CoRows.FlagOf(state, "pufirst"))
            {
                throw new BridgeException(409, "state_mismatch", "期初采购入库单不支持修改");
            }
            RefuseForeign(ctx.HomeCurrency, state);
        }

        // 重算税价按本币、汇率 1（StockDomPurIn.PurCells），U8 里录的外币单不改。
        static void RefuseForeign(string home, Dictionary<string, object> state)
        {
            string exch = CoRows.Col(state, "cExch_Name");
            decimal rate;
            bool one = !StockUnits.Dec(CoRows.Col(state, "exchrate"), out rate) || rate == 1m;
            if ((exch.Length > 0 && exch != home) || !one)
            {
                throw new BridgeException(409, "state_mismatch", "外币采购入库单不支持修改");
            }
        }

        // 修改的请求体：表头换供应商或采购类型要重新核对；新增行按新增规则核对；改行只核单价键和批号。
        public static void CheckEdit(WorkContext ctx, Dictionary<string, object> head, object[] lines)
        {
            object conn = ctx.Conn;
            if (head != null && Sent(head, "cvencode"))
            {
                Vendor(ctx, CoRows.Col(head, "cvencode"));
            }
            if (head != null && Sent(head, "cptcode"))
            {
                PurchaseType(conn, CoRows.Col(head, "cptcode"), true);
            }
            if (head != null && Sent(head, "cwhcode") && CoRows.Col(head, "cwhcode").Length == 0)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            if (lines == null)
            {
                return;
            }
            for (int i = 0; i < lines.Length; i++)
            {
                CheckEditLine(conn, lines[i] as Dictionary<string, object>);
            }
        }

        static void CheckEditLine(object conn, Dictionary<string, object> line)
        {
            if (line == null)
            {
                return;
            }
            string op = CoRows.Col(line, "op");
            if (op == "add")
            {
                StockPurInPos.RefuseHavePo(conn);
                CheckLine(conn, line);
                return;
            }
            if (op != "update")
            {
                return;
            }
            CheckPrices(line);
            if (Sent(line, "cbatch") && CoRows.Col(line, "cbatch").Length == 0)
            {
                throw BridgeException.BadField("lines.cbatch", "不能清空批号 cbatch");
            }
        }

        // 采购期初、库存期初、存货期初任一为真都算期初。
        const string FirstSql = "select case when isnull(bpufirst,0)=1 or isnull(bIsSTQc,0)=1 or isnull(biafirst,0)=1"
            + " then '1' else '0' end from RdRecord01 where ID=?";

        // 删除采购入库单（任何来源）：已记账、期初都拒绝。before 须带 cbaccounter（StockCo.ReadHead booked）。
        public static void RefuseDeleteState(object conn, int id, Dictionary<string, object> before)
        {
            if (StockMsg.Col(before, "cbaccounter").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已记账，不能删除");
            }
            if (Values.Flag(Rows.Scalar(conn, FirstSql, new object[] { id })))
            {
                throw new BridgeException(409, "state_mismatch", "期初采购入库单不能删除");
            }
        }

        // 改已有行：批号、生产日期、失效日期按行上存货的批次 / 保质期规则核对（新增行在 CheckLine 里核）。
        public static void CheckChangedLine(object conn, string inv, Dictionary<string, object> fields)
        {
            bool lot = Sent(fields, "cbatch");
            bool dates = Sent(fields, "dmadedate") || Sent(fields, "dvdate");
            if (!lot && !dates)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { (inv ?? "").Trim() });
            if (row == null)
            {
                return;
            }
            bool quality = CoRows.FlagOf(row, "bInvQuality");
            if (lot)
            {
                ChangedLot(CoRows.FlagOf(row, "bInvBatch") || quality, CoRows.Col(fields, "cbatch"), inv);
            }
            if (quality)
            {
                ChangedDates(fields, inv);
            }
        }

        static void ChangedDates(Dictionary<string, object> fields, string inv)
        {
            if (Blank(fields, "dmadedate") || Blank(fields, "dvdate"))
            {
                throw BridgeException.BadField("lines.dmadedate", "存货 " + inv + " 启用保质期管理，生产日期和失效日期不能为空");
            }
        }

        static void ChangedLot(bool managed, string lot, string inv)
        {
            if (managed && lot.Length == 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 启用批次或保质期管理，批号 cbatch 不能为空");
            }
            if (!managed && lot.Length > 0)
            {
                throw BridgeException.BadField("lines.cbatch", "存货 " + inv + " 未启用批次管理，不能填批号");
            }
        }

        static bool Blank(Dictionary<string, object> map, string name)
        {
            return Sent(map, name) && CoRows.Col(map, name).Length == 0;
        }

        // 有非空值才算给了；null 与空串在 StockDom.Cell 里都不写入。
        internal static bool Given(Dictionary<string, object> map, string name)
        {
            return CoRows.Col(map, name).Length > 0;
        }

        internal static bool Sent(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return false;
            }
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
