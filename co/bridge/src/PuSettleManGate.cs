using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 采购手工结算的闸门（全是参数化 SQL）。登录后、事务前跑一遍（功能权限、数据权限、全部状态），
    // 事务里加更新锁再跑一遍（PuSettleMan，用锁住之后读到的数重算，防止闸门之后别人改了入库行 / 发票行；入库单、发票的表头同样加锁，
    // 已有结算行（PurSettleVouchs / PurSettleVouch，算已结算合计用）也加 updlock、holdlock，挡住并发新增的结算行）。
    // 入库行：存在、普通采购、已审核、数量与入库行同号且不超过未结算数量（iQuantity − iSQuantity）、入库日期不晚于结算日期。
    // 发票行：存在、普通采购、人民币、非期初、非现付、已采购复核、未应付审核、01 / 02 发票、参照入库单开具（UpSoType 为 rd 或空）、
    //   数量与发票行同号且不超过未结算数量（iPBVQuantity − 已结算合计）、发票日期不晚于结算日期。
    // 同一发票行不混用红蓝发票对冲与配对（税额无法分摊，本单内 400、与已有结算 409）；配对行入库与发票是同一存货；全部入库单、发票同一供应商；红蓝入库对冲行、红蓝发票对冲行各按存货合计数量为 0；
    // 数量的小数位不超过账套的数量位数（PU_SettleWriteBKRDS 按它舍入已结算数量）。
    internal static class PuSettleManGate
    {
        const string RdSql = "select convert(varchar(20), r.AutoID) as AutoID, convert(varchar(20), r.ID) as RdId, r.cInvCode,"
            + " convert(varchar(40), r.iQuantity) as Qty, convert(varchar(40), isnull(r.iSQuantity,0)) as SQty,"
            + " convert(varchar(40), r.fACost) as ACost, convert(varchar(40), isnull(r.iAPrice,0)) as APrice,"
            + " convert(varchar(40), isnull(r.iUnitCost,0)) as UCost, rh.cCode, rh.cVenCode, rh.cDepCode, rh.cPersonCode,"
            + " rh.cPTCode, rh.cBusType, rh.cHandler, rh.cWhCode, convert(varchar(10), rh.dDate, 23) as RdDate,"
            + " convert(varchar(40), isnull(p.aprice,0)) as PriorAPrice"
            + " from rdrecords01 r{L} join RdRecord01 rh{L} on rh.ID=r.ID"
            + " outer apply (select sum(s.iSVAPrice) as aprice from PurSettleVouchs s{L} join PurSettleVouch sh{L} on sh.PSVID=s.PSVID"
            + " where s.iRdsID=r.AutoID and sh.cSettleType=N'01' and s.cUpSoType=N'01') p where r.AutoID in ({IN})";
        const string BsSql = "select convert(varchar(20), b.ID) as ID, convert(varchar(20), b.PBVID) as PBVID, b.cInvCode,"
            + " convert(varchar(40), b.iPBVQuantity) as Qty, convert(varchar(40), isnull(b.iMoney,0)) as Money,"
            + " convert(varchar(40), case when isnull(h.iDiscountTaxType,0)=0 then isnull(b.iCost,0)"
            + " else isnull(b.iCost,0)*(1-isnull(b.iTaxRate,0)/100) end) as Cost,"
            + " convert(varchar(40), isnull(b.iTaxPrice,0)) as Tax, convert(varchar(40), isnull(b.iNum,0)) as Num,"
            + " upper(isnull(b.UpSoType, N'rd')) as UpSo, h.cPBVCode, h.cPBVBillType, h.cBusType, h.cVerifier, h.cPBVVerifier,"
            + " h.cVenCode, h.cDepCode, h.cPersonCode, h.cPTCode, h.cPBVMemo, convert(varchar(40), h.iPBVTaxRate) as HRate,"
            + " isnull(h.cexch_name, N'') as Exch, convert(varchar(10), h.dPBVDate, 23) as BillDate,"
            + " convert(varchar(5), isnull(h.bPayment,0)) as Paid, convert(varchar(5), isnull(h.bFirst,0)) as First,"
            + " convert(varchar(5), isnull(h.bOriginal,0)) as Orig, convert(varchar(40), isnull(p.qty,0)) as PriorQty,"
            + " convert(varchar(40), isnull(p.money,0)) as PriorMoney, convert(varchar(40), isnull(p.tax,0)) as PriorTax,"
            + " convert(varchar(20), isnull(p.offs,0)) as PriorOff, convert(varchar(20), isnull(p.pairs,0)) as PriorPair"
            + " from PurBillVouchs b{L} join PurBillVouch h{L} on h.PBVID=b.PBVID"
            + " outer apply (select sum(s.iSVQuantity) as qty, sum(s.iSVPrice) as money, sum(s.iTax) as tax,"
            + " sum(case when isnull(s.iRdsID,0)=0 then 1 else 0 end) as offs,"
            + " sum(case when isnull(s.iRdsID,0)<>0 then 1 else 0 end) as pairs"
            + " from PurSettleVouchs s{L} where s.iBsID=b.ID) p where b.ID in ({IN})";
        const string DecSql = "select max(case when cName=N'iStrsQuanDecDgt' then isnull(cValue,cDefault) end) as q,"
            + " max(case when cName=N'iStrsPriDecDgt' then isnull(cValue,cDefault) end) as c"
            + " from AccInformation where cName in (N'iStrsQuanDecDgt', N'iStrsPriDecDgt')";
        const string Lock = " with (updlock, holdlock)";
        const string Rmb = "人民币";

        // 事务前：功能权限（不泄露单据状态）、全部闸门、数据权限。
        internal static ManPlan ForCreate(WorkContext ctx, List<ManLine> lines)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(PuSettleManReq.CreateRule);
            PermCheck.RequireRule(perm, rule);
            ManPlan plan = Build(ctx.Conn, lines, Day(ctx), false);
            PuSettleGate.RequireOpen(ctx, plan.Date);
            MoDelete.CheckRows(perm, rule, PermRows(plan));
            return plan;
        }

        internal static string Day(WorkContext ctx)
        {
            string date = (ctx.Item.Date ?? "").Trim();
            if (!PuSettleReq.IsDay(date))
            {
                throw BridgeException.BadField("date", "结算日期必须是 yyyy-MM-dd");
            }
            return date;
        }

        // locked：事务里调用，入库行、发票行加更新锁读。
        internal static ManPlan Build(object conn, List<ManLine> lines, string date, bool locked)
        {
            int[] digits = Digits(conn);
            Dictionary<int, ManRd> rds = LoadRds(conn, lines, locked);
            Dictionary<int, ManBs> bss = LoadBss(conn, lines, locked);
            ManPlan plan = new ManPlan();
            plan.Date = date;
            plan.QuanDec = digits[0];
            plan.CostDec = digits[1];
            plan.Lines = lines;
            PuSettleManRules.Check(plan, rds, bss);
            plan.Rows = PuSettleManPlan.Compute(lines, rds, bss, plan.CostDec);
            PickHead(plan);
            return plan;
        }

        // [数量位数, 单价位数]，读不到时用 U8 出厂值 2 / 2。
        static int[] Digits(object conn)
        {
            Dictionary<string, object> row = Rows.One(conn, DecSql, new object[0]);
            return new int[] { Digit(CoRows.Col(row, "q"), 2), Digit(CoRows.Col(row, "c"), 2) };
        }

        static int Digit(string text, int fallback)
        {
            int n;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) && n >= 0 && n <= 10)
            {
                return n;
            }
            return fallback;
        }

        static Dictionary<int, ManRd> LoadRds(object conn, List<ManLine> lines, bool locked)
        {
            Dictionary<int, ManRd> map = new Dictionary<int, ManRd>();
            List<int> ids = Ids(lines, true);
            if (ids.Count == 0)
            {
                return map;
            }
            foreach (Dictionary<string, object> row in Query(conn, RdSql, ids, locked))
            {
                ManRd rd = new ManRd();
                rd.Id = CoRows.AsId(CoRows.Col(row, "AutoID"));
                rd.RdId = CoRows.AsId(CoRows.Col(row, "RdId"));
                rd.Code = CoRows.Col(row, "cCode");
                rd.InvCode = CoRows.Col(row, "cInvCode");
                rd.Qty = PuInv.Num(CoRows.Col(row, "Qty"));
                rd.SQty = PuInv.Num(CoRows.Col(row, "SQty"));
                rd.HasACost = CoRows.Col(row, "ACost").Length > 0;
                rd.ACost = PuInv.Num(CoRows.Col(row, "ACost"));
                rd.APrice = PuInv.Num(CoRows.Col(row, "APrice"));
                rd.UCost = PuInv.Num(CoRows.Col(row, "UCost"));
                rd.PriorAPrice = PuInv.Num(CoRows.Col(row, "PriorAPrice"));
                rd.Row = row;
                map[rd.Id] = rd;
            }
            return map;
        }

        static Dictionary<int, ManBs> LoadBss(object conn, List<ManLine> lines, bool locked)
        {
            Dictionary<int, ManBs> map = new Dictionary<int, ManBs>();
            List<int> ids = Ids(lines, false);
            if (ids.Count == 0)
            {
                return map;
            }
            foreach (Dictionary<string, object> row in Query(conn, BsSql, ids, locked))
            {
                ManBs bs = new ManBs();
                bs.Id = CoRows.AsId(CoRows.Col(row, "ID"));
                bs.Pbvid = CoRows.AsId(CoRows.Col(row, "PBVID"));
                bs.Code = CoRows.Col(row, "cPBVCode");
                bs.InvCode = CoRows.Col(row, "cInvCode");
                bs.Qty = PuInv.Num(CoRows.Col(row, "Qty"));
                bs.Money = PuInv.Num(CoRows.Col(row, "Money"));
                bs.Cost = PuInv.Num(CoRows.Col(row, "Cost"));
                bs.Tax = PuInv.Num(CoRows.Col(row, "Tax"));
                bs.Num = PuInv.Num(CoRows.Col(row, "Num"));
                bs.PriorQty = PuInv.Num(CoRows.Col(row, "PriorQty"));
                bs.PriorMoney = PuInv.Num(CoRows.Col(row, "PriorMoney"));
                bs.PriorTax = PuInv.Num(CoRows.Col(row, "PriorTax"));
                bs.PriorOffRows = (int)PuInv.Num(CoRows.Col(row, "PriorOff"));
                bs.PriorPairRows = (int)PuInv.Num(CoRows.Col(row, "PriorPair"));
                bs.Row = row;
                map[bs.Id] = bs;
            }
            return map;
        }

        // 去重后的入库行 / 发票行主键（按请求次序）。
        internal static List<int> Ids(List<ManLine> lines, bool rd)
        {
            List<int> ids = new List<int>();
            for (int i = 0; i < lines.Count; i++)
            {
                int id = rd ? lines[i].InId : lines[i].BsId;
                if (id > 0 && !ids.Contains(id))
                {
                    ids.Add(id);
                }
            }
            return ids;
        }

        // {IN} 换成与主键个数相同的 ?（最多 400 个，只由个数决定，不拼调用方文本）；{L} 是锁提示。
        internal static string InList(string sql, int count, bool locked)
        {
            StringBuilder marks = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                marks.Append(i == 0 ? "?" : ",?");
            }
            return sql.Replace("{L}", locked ? Lock : "").Replace("{IN}", marks.ToString());
        }

        static List<Dictionary<string, object>> Query(object conn, string sql, List<int> ids, bool locked)
        {
            object[] args = new object[ids.Count];
            for (int i = 0; i < ids.Count; i++)
            {
                args[i] = ids[i];
            }
            return Rows.Query(conn, InList(sql, ids.Count, locked), args, ids.Count + 1)
                ?? new List<Dictionary<string, object>>();
        }

        // 表头：有发票时取第一张发票的采购类型、部门、业务员、税率、备注（同 U8 按发票结算），只有入库单时取第一张入库单的。
        static void PickHead(ManPlan plan)
        {
            ManRow first = null;
            for (int i = 0; i < plan.Rows.Count && first == null; i++)
            {
                if (plan.Rows[i].Bs != null)
                {
                    first = plan.Rows[i];
                }
            }
            Dictionary<string, object> src = first != null ? first.Bs.Row : plan.Rows[0].Rd.Row;
            plan.Ven = CoRows.Col(src, "cVenCode");
            plan.Dep = CoRows.Col(src, "cDepCode");
            plan.Person = CoRows.Col(src, "cPersonCode");
            plan.PtCode = CoRows.Col(src, "cPTCode");
            plan.Rate = first != null && CoRows.Col(src, "HRate").Length > 0 ? CoRows.Col(src, "HRate") : null;
            plan.Memo = first != null && CoRows.Col(src, "cPBVMemo").Length > 0 ? CoRows.Col(src, "cPBVMemo") : null;
        }

        // 数据权限的行：表头的供应商、部门、业务员、采购类型 + 每行的存货、仓库（入库单的仓库；只有发票行时为空）。
        internal static List<Dictionary<string, object>> PermRows(ManPlan plan)
        {
            Dictionary<string, object> head = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            head["cVenCode"] = plan.Ven;
            head["cDepCode"] = plan.Dep;
            head["cPersonCode"] = plan.Person;
            head["cPTCode"] = plan.PtCode;
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>>();
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                ManRow row = plan.Rows[i];
                Dictionary<string, object> line = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                line["cInvCode"] = row.Rd != null ? row.Rd.InvCode : row.Bs.InvCode;
                line["cWhCode"] = row.Rd != null ? CoRows.Col(row.Rd.Row, "cWhCode") : "";
                lines.Add(line);
            }
            return PuSettleGate.PermRows(head, lines);
        }

        internal static bool IsRmb(string exch)
        {
            string text = (exch ?? "").Trim();
            return text.Length == 0 || text == Rmb;
        }
    }

    // 一次手工结算：结算日期、账套位数、请求行、算好的结算行和表头取值。
    internal sealed class ManPlan
    {
        public string Date;
        public int QuanDec;
        public int CostDec;
        public List<ManLine> Lines;
        public List<ManRow> Rows;
        public string Ven;
        public string Dep;
        public string Person;
        public string PtCode;
        public string Rate;
        public string Memo;
    }
}
