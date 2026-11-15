using System;
using System.Collections.Generic;

namespace U8Co
{
    // 采购退货单（红字到货单）：同 PU_ArrivalVouch / PU_ArrivalVouchs，iBillType=1、bNegative=1，数量金额为负。
    // 与蓝字到货单同一个 CO 单据类型（vt 2、卡片 26、iVTid 8169），只是 Init 的 bPositive / sBillType 不同（见 RedInit）。
    // 参照原蓝字到货单行（缺省，iCorId = 原行 Autoid）或采购订单行（iPOsID）生成；读取、审核、删除走 PurchaseCo / PuRetDel。
    internal static partial class PuRet
    {
        // ===== 可调整：红字 Init 参数，只改这一块 =====
        // 生单 Init(2, login, conn, info, bPositive, sBillType, "普通采购", 0, "", pt)。方案 A：false / "1"。
        // 不要用 "2"（那是拒收，U8 跳过订单回写）。
        internal const bool GenPositive = false;
        internal const string GenBill = "1";
        // 读取、审核/弃审、删除的 Init。蓝字到货单这里是 true / ""；红字先与生单相同。
        internal const bool OpPositive = false;
        internal const string OpBill = "1";
        // 参照采购订单时的可退数量表达式（d = PO_Podetails）。实测（参照到货单的退货）：U8 把 iArrQTY 减去退货数、
        // fPoRetQuantity 加上退货数，所以 iArrQTY 已是净到货，不能再减 fPoRetQuantity。参照订单的退货同此口径，已实测。
        internal const string PoLeftExpr = "isnull(d.iArrQTY,0)";
        // ===== 可调整块结束 =====

        const string ArrLeftExpr = "isnull(s.iQuantity,0) - isnull(s.fRetQuantity,0)";
        const string ArrHeadSql = "select h.cCode as SrcCode, h.cverifier as Verifier, h.ccloser as Closer,"
            + " convert(varchar(5), isnull(h.iBillType,0)) as BillType, h.cVenCode, h.cDepCode, h.cPersonCode, h.cPTCode,"
            + " h.cBusType, h.cexch_name, convert(varchar(40), convert(decimal(28,10), isnull(h.iExchRate,1))) as nflat,"
            + " convert(varchar(40), isnull(h.iTaxRate,0)) as iTaxRate, h.cpocode as PoCode"
            + " from PU_ArrivalVouch h where h.ID=?";
        const string PoHeadSql = "select m.cPOID as SrcCode, m.cVerifier as Verifier, m.cCloser as Closer,"
            + " '0' as BillType, m.cVenCode, m.cDepCode, m.cPersonCode, m.cPTCode,"
            + " m.cBusType, m.cexch_name, convert(varchar(40), convert(decimal(28,10), isnull(m.nflat,1))) as nflat,"
            + " convert(varchar(40), isnull(m.iTaxRate,0)) as iTaxRate, m.cPOID as PoCode"
            + " from PO_Pomain m where m.POID=?";
        const string FreeOf = "case when isnull({0}.cFree1,N'')+isnull({0}.cFree2,N'')+isnull({0}.cFree3,N'')+isnull({0}.cFree4,N'')"
            + "+isnull({0}.cFree5,N'')+isnull({0}.cFree6,N'')+isnull({0}.cFree7,N'')+isnull({0}.cFree8,N'')+isnull({0}.cFree9,N'')"
            + "+isnull({0}.cFree10,N'')<>N'' then '1' else '0' end as HasFree";
        static readonly string ArrLineSql = "select s.cInvCode, convert(varchar(40), " + ArrLeftExpr + ") as SrcLeft,"
            + " s.cbcloser as LineCloser, s.cWhCode as SrcWh, convert(varchar(5), isnull(s.bTaxCost,0)) as bTaxCost,"
            + " convert(varchar(40), s.iOriTaxCost) as iTaxPrice, convert(varchar(40), s.iOriCost) as iUnitPrice,"
            + " convert(varchar(40), s.iTaxRate) as iPerTaxRate, s.cUnitID, convert(varchar(40), s.iinvexchrate) as ChangRate,"
            + " convert(varchar(40), isnull(s.iQuantity,0)) as SrcQty, convert(varchar(40), s.iNum) as SrcNum,"
            + " convert(varchar(5), isnull(i.iGroupType,0)) as GroupType, " + string.Format(FreeOf, "s") + ","
            + " convert(varchar(20), isnull(s.iPOsID,0)) as PoLine, s.cordercode as OrderCode, s.cBatch as SrcBatch,"
            + " convert(varchar(30), convert(money, m.ufts), 2) as PoUfts,"
            + " convert(varchar(30), convert(money, a.ufts), 2) as ArrUfts"
            + " from PU_ArrivalVouchs s inner join PU_ArrivalVouch a on a.ID=s.ID left join Inventory i on i.cInvCode=s.cInvCode"
            + " left join PO_Podetails d on d.ID=s.iPOsID left join PO_Pomain m on m.POID=d.POID"
            + " where s.Autoid=? and s.ID=?";
        static readonly string PoLineSql = "select d.cInvCode, convert(varchar(40), " + PoLeftExpr + ") as SrcLeft,"
            + " d.cbCloser as LineCloser, i.cDefWareHouse as SrcWh, convert(varchar(5), isnull(d.bTaxCost,0)) as bTaxCost,"
            + " convert(varchar(40), d.iTaxPrice) as iTaxPrice, convert(varchar(40), d.iUnitPrice) as iUnitPrice,"
            + " convert(varchar(40), d.iPerTaxRate) as iPerTaxRate, d.cUnitID, convert(varchar(40), c.iChangRate) as ChangRate,"
            + " convert(varchar(40), isnull(d.iQuantity,0)) as SrcQty, convert(varchar(40), d.iNum) as SrcNum,"
            + " convert(varchar(5), isnull(i.iGroupType,0)) as GroupType, " + string.Format(FreeOf, "d") + ","
            + " convert(varchar(20), d.ID) as PoLine, m.cPOID as OrderCode, N'' as SrcBatch,"
            + " convert(varchar(30), convert(money, m.ufts), 2) as PoUfts"
            + " from PO_Podetails d inner join PO_Pomain m on m.POID=d.POID left join Inventory i on i.cInvCode=d.cInvCode"
            + " left join ComputationUnit c on c.cComunitCode=d.cUnitID where d.ID=? and d.POID=?";
        // 事务里的重读加 UPDLOCK / HOLDLOCK：到提交为止别的会话改不了这两行（到货、退货、删除都要改它们）。
        static readonly string ArrLeftSql = "select convert(varchar(40), " + ArrLeftExpr
            + ") from PU_ArrivalVouchs s with (updlock, holdlock) where s.Autoid=?";
        static readonly string PoLeftSql = "select convert(varchar(40), " + PoLeftExpr
            + ") from PO_Podetails d with (updlock, holdlock) where d.ID=?";

        // Info_PU.Init(login, "普通采购", pt) 引用 {0}；VoucherCO_PU.Init(2, login, conn, info, bPositive, sBillType, "普通采购", 0, "", pt)
        // 引用 {1..7}，pt 是采购类型编码（PuSession.InitType），参数见 RedInit；bOutTrans=true，事务由桥管。审核/弃审另开 SetVerifyMode(true)。
        internal static void Open(WorkContext ctx, bool generate, bool verify, out object info, out object co)
        {
            info = ComUtil.Create("Info_PU.ClsS_Infor");
            co = ComUtil.Create("VoucherCO_PU.clsVoucherCO_PU");
            if (info == null || co == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            object login = ctx.Session.Login;
            string pt = PuSession.InitType(ctx);
            object[] infoArgs = new object[] { login, "普通采购", pt };
            object infoRet = ComUtil.CallRef(info, "Init", infoArgs, new int[] { 0 });
            CoRows.LoginBack(ctx, login, infoArgs[0]);
            string infoMsg = Values.Text(infoRet).Trim();
            if (infoMsg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", infoMsg);
            }
            bool positive = generate ? GenPositive : OpPositive;
            string bill = generate ? GenBill : OpBill;
            object[] args = new object[] { 2, login, ctx.Conn, info, positive, bill, "普通采购", 0, "", pt };
            ComUtil.CallRef(co, "Init", args, new int[] { 1, 2, 3, 4, 5, 6, 7 });
            if (args[2] != null && !object.ReferenceEquals(ctx.Conn, args[2]))
            {
                CoRows.Note(ctx.Item, "Init 更换了连接");
            }
            CoRows.LoginBack(ctx, login, args[1]);
            CoRows.ReleaseIfNew(ctx.Conn, args[2]);
            CoRows.ReleaseIfNew(info, args[3]);
            CoRows.Note(ctx.Item, "Init 红字 positive=" + Values.Text(args[4]) + " bill=" + Values.Text(args[5]));
            ComUtil.Set(co, "bOutTrans", true);
            if (verify)
            {
                ComUtil.Call(co, "SetVerifyMode", new object[] { true });
                CoRows.Note(ctx.Item, "SetVerifyMode 1 vt=2 红字");
            }
        }

        // 登录前校验与蓝字到货单相同：表头只收 cWhCode、dDate、cMemo、cDepCode；表体 1 到 200 行，只收 source_line_id、quantity。
        public static void CheckGenerate(Dictionary<string, object> head, object[] lines)
        {
            PuArr.CheckGenerate(head, lines);
        }

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            CheckGenerate(head, lines);
            RetJob job = new RetJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.SourceId = sourceId;
            job.FromPo = ctx.Item.Source != null && ctx.Item.Source.Name == "purchase_order";
            job.Source = Kinds.Find(job.FromPo ? "purchase_order" : "arrival");
            job.Src = LoadHead(ctx.Conn, job);
            // 到货单（含退货单）审批流已发布：生成的单据要走审批，暂不支持。
            PurchaseCo.RefuseFlow(ctx.Conn, kind, new Dictionary<string, object>());
            job.Wh = MfgReq.Text(head, "cwhcode");
            job.Memo = MfgReq.Text(head, "cmemo");
            job.Dept = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Src, "cDepCode"));
            job.Date = Or(MfgReq.Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            job.Lines = LoadLines(ctx.Conn, job, lines);
            return Save(job);
        }

        // 来源须已审核、未关闭、普通采购；到货单来源还须是蓝字（iBillType=0）。
        static Dictionary<string, object> LoadHead(object conn, RetJob job)
        {
            Dictionary<string, object> src = Rows.One(conn, job.FromPo ? PoHeadSql : ArrHeadSql, new object[] { job.SourceId });
            if (src == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(src, "BillType") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "来源须为蓝字到货单");
            }
            if (CoRows.Col(src, "Verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(src, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(src, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的来源单据");
            }
            return src;
        }

        static List<RetLine> LoadLines(object conn, RetJob job, object[] lines)
        {
            decimal exch = Rate(CoRows.Col(job.Src, "nflat"));
            string headRate = CoRows.Col(job.Src, "iTaxRate");
            List<RetLine> list = new List<RetLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> raw = PuInvReq.LineOf(lines[i]);
                RetLine line = new RetLine();
                line.SrcLine = MfgReq.LineId(raw);
                line.Qty = MfgReq.LineQty(raw);
                line.Src = Rows.One(conn, job.FromPo ? PoLineSql : ArrLineSql, new object[] { line.SrcLine, job.SourceId });
                if (line.Src == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                if (CoRows.Col(line.Src, "LineCloser").Length > 0)
                {
                    throw new BridgeException(409, "state_mismatch", "单据已关闭");
                }
                line.PoLine = CoRows.AsId(CoRows.Col(line.Src, "PoLine"));
                line.Left = PuInv.Num(CoRows.Col(line.Src, "SrcLeft"));
                RequireLeft(line);
                Units(line);
                if (CoRows.Col(line.Src, CoRows.FlagOf(line.Src, "bTaxCost") ? "iTaxPrice" : "iUnitPrice").Length == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "来源行没有单价");
                }
                // 负数量：金额随之为负，单价仍为正。
                line.Amt = StockGen.PoAmounts(line.Src, -line.Qty, exch, headRate);
                list.Add(line);
            }
            return list;
        }

        // 不允许超量退货：退货数量不超过来源行的可退数量（到货单：iQuantity − fRetQuantity；订单：PoLeftExpr）。
        static void RequireLeft(RetLine line)
        {
            if (line.Qty > line.Left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可退货数量");
            }
        }

        // 事务里加锁重读可退数量再核一次：来源行按来源口径；有订单行时，同一订单行上本单退货合计
        // 还不能超过订单行净到货（iArrQTY），挡住参照到货单与参照订单两条路对同一订单行超退。
        static void ReadLeft(object conn, RetJob job)
        {
            Dictionary<int, decimal> perPo = new Dictionary<int, decimal>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                RetLine line = job.Lines[i];
                string sql = job.FromPo ? PoLeftSql : ArrLeftSql;
                line.Left = PuInv.Num(Rows.Scalar(conn, sql, new object[] { line.SrcLine }));
                RequireLeft(line);
                if (line.PoLine > 0)
                {
                    decimal sum;
                    perPo.TryGetValue(line.PoLine, out sum);
                    perPo[line.PoLine] = sum + line.Qty;
                }
            }
            foreach (KeyValuePair<int, decimal> kv in perPo)
            {
                decimal left = PuInv.Num(Rows.Scalar(conn, PoLeftSql, new object[] { kv.Key }));
                if (kv.Value > left)
                {
                    throw new BridgeException(409, "state_mismatch", "超过采购订单行可退货数量");
                }
            }
        }

        // 辅计量同蓝字到货单：固定换算（iGroupType=1）写来源行 cUnitID 和换算率（没有换算率时用 数量 / 件数）；
        // 无换算不写；浮动换算和带自由项的来源行不支持。
        static void Units(RetLine line)
        {
            Dictionary<string, object> src = line.Src;
            string group = CoRows.Col(src, "GroupType");
            if (group == "2" || CoRows.Col(src, "HasFree") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "暂不支持浮动换算率/自由项的退货生单");
            }
            line.Unit = "";
            line.Rate = 0m;
            string unit = CoRows.Col(src, "cUnitID");
            if (group != "1" || unit.Length == 0)
            {
                return;
            }
            decimal rate = PuInv.Num(CoRows.Col(src, "ChangRate"));
            decimal num = PuInv.Num(CoRows.Col(src, "SrcNum"));
            if (rate <= 0m && num != 0m)
            {
                rate = PuInv.Num(CoRows.Col(src, "SrcQty")) / num;
            }
            if (rate > 0m)
            {
                line.Unit = unit;
                line.Rate = rate;
            }
        }

        static decimal Rate(string text)
        {
            decimal rate = PuInv.Num(text);
            return rate <= 0m ? 1m : rate;
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback ?? "";
        }

        sealed class RetJob
        {
            public WorkContext Ctx;
            public VoucherKind Kind;
            public VoucherKind Source;
            public bool FromPo;
            public int SourceId;
            public string Code;
            public string Wh;
            public string Memo;
            public string Dept;
            public string Date;
            public Dictionary<string, object> Src;
            public List<RetLine> Lines;
        }

        sealed class RetLine
        {
            public int SrcLine;
            public int PoLine;
            public decimal Qty;
            public decimal Left;
            public string Unit;
            public decimal Rate;
            public Dictionary<string, object> Src;
            public Dictionary<string, string> Amt;
        }
    }
}
