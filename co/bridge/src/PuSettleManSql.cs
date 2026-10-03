using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购手工结算的写入 SQL，与 U8 采购「手工结算」界面执行的 SQL 一致（含 PU_AutoSettleBillRefRD，实测核对）：
    // PU_GetID 取 PurSTID / PurSTsID（号已被占用时 409）→ INSERT PurSettleVouch（cSVCode = 15 位补零的 PSVID，cSettleType 取缺省 01）
    // → 每行 INSERT PurSettleVouchs（入库行侧的存货、项目、自由项抄入库行，发票侧的发票号、备注抄发票行）→ 每条入库行
    // EXEC PU_SettleWriteBKRDS（回写已结算数量 iSQuantity、结算日期、单价、ST_RdRecords_bak）→ 发票行结清时写 dSDate、
    // 配对行写入库日期 dInDate（U8 的触发器同样写），整张发票结清时写表头 dSDate → Inventory.iInvNCost = 配对行的结算单价
    // → RdRecord01 时间戳。全部在请求连接的事务里，调用方的值只进参数，数以不变区域文本传、在 SQL 里转 decimal / float。
    internal static class PuSettleManSql
    {
        const string NextIdSql = "SET NOCOUNT ON; DECLARE @id int; EXEC PU_GetID ?, 1, @MaxID=@id OUTPUT;"
            + " SELECT convert(varchar(20), @id) AS id";
        // PU_GetID 只读 MAX、不加锁：两个会话可能拿到同一个号。这里加更新锁、范围锁再查，锁持有到提交，
        // 并发的另一单在这里等，等到时号已被占用，回 409。
        const string HeadTakenSql = "select convert(varchar(20), count(*)) from PurSettleVouch with (updlock, holdlock) where PSVID=?";
        const string BodyTakenSql = "select convert(varchar(20), count(*)) from PurSettleVouchs with (updlock, holdlock)"
            + " where ID between ? and ?";
        internal const string IdTakenText = "结算单号冲突，请重试";

        internal const string HeadSql = "INSERT INTO PurSettleVouch(PSVID,cSVCode,cBusType,cPTCode,dSVDate,cVenCode,cDepCode,"
            + "cPersonCode,iTaxRate,cSVMemo,cMaker,bMerger) VALUES (?,?,N'普通采购',?,CONVERT(datetime, CONVERT(date, ?, 23)),"
            + "?,?,?,CONVERT(float, ?),?,?,0)";

        // 入库侧有行时抄入库行，否则抄发票行（项目、自由项）。cUpSoType：有入库行为 01（入库单），红蓝发票对冲为空（同 U8）。
        internal const string LineSql = "INSERT INTO PurSettleVouchs(ID,PSVID,cPIVCode,iRdsID,cInvCode,cBillCode,iBsID,iSVQuantity,"
            + "iSVNum,iSVCost,iSVPrice,iSVExpense,iSVACost,iSVAPrice,iTax,iSum,bAccount,bIsPurAcc,cItemCode,cItem_class,cItemName,"
            + "cFree1,cFree2,cfree3,cfree4,cfree5,cfree6,cfree7,cfree8,cfree9,cfree10,cUpSoType,cbMemo,MaterialFee,ProcessFee,"
            + "cRDcVencode,cBVcVencode)"
            + " SELECT ?,?,rh.cCode,?,COALESCE(r.cInvCode,b.cInvCode),bh.cPBVCode,?,"
            + "CONVERT(decimal(30,10), ?),CONVERT(decimal(30,10), ?),CONVERT(decimal(30,10), ?),CONVERT(decimal(19,4), ?),0,"
            + "CONVERT(decimal(30,10), ?),CONVERT(decimal(19,4), ?),CONVERT(decimal(19,4), ?),CONVERT(decimal(19,4), ?),0,0,"
            + "{C:cItemCode},{C:cItem_class},CASE WHEN r.AutoID IS NULL THEN b.cItemName ELSE r.cName END,"
            + "{C:cFree1},{C:cFree2},{C:cFree3},{C:cFree4},{C:cFree5},{C:cFree6},{C:cFree7},{C:cFree8},{C:cFree9},{C:cFree10},"
            + "CASE WHEN r.AutoID IS NULL THEN NULL ELSE N'01' END,b.cbMemo,"
            + "CASE WHEN r.AutoID IS NULL OR b.ID IS NULL THEN NULL ELSE r.iMaterialFee END,"
            + "CASE WHEN r.AutoID IS NULL OR b.ID IS NULL THEN NULL ELSE 0 END,rh.cVenCode,bh.cVenCode"
            + " FROM (SELECT 1 AS one) z LEFT JOIN rdrecords01 r ON r.AutoID=? LEFT JOIN RdRecord01 rh ON rh.ID=r.ID"
            + " LEFT JOIN PurBillVouchs b ON b.ID=? LEFT JOIN PurBillVouch bh ON bh.PBVID=b.PBVID";

        // 参数同 U8 手工结算界面：@iRDCost = 入库行暂估单价，@iAverPrice = 结算单价，@bWBCost = 1，材料费、加工费 0，
        // @cvType = 01（采购入库单），@cSysID = PU。@iRDQuanCheck 传入库行加锁读到的未结算数量（iQuantity − iSQuantity）：
        // 过程只在 @iRDQuan 等于它（本单把入库行结清）时改写入库行的 iPrice、iUnitCost，部分结算保留原单价、金额，
        // 同 PU_AutoSettleBillRefRD（整行结清才按结算金额合计改写）。
        internal const string WriteBackSql = "SET NOCOUNT ON; DECLARE @q float, @set float, @real float, @cost float, @avg float,"
            + " @left float;"
            + " SET @q = CONVERT(float, CONVERT(decimal(30,10), ?)); SET @set = CONVERT(float, CONVERT(decimal(30,10), ?));"
            + " SET @real = CONVERT(float, CONVERT(decimal(30,10), ?)); SET @cost = CONVERT(float, CONVERT(decimal(30,10), ?));"
            + " SET @avg = CONVERT(float, CONVERT(decimal(30,10), ?)); SET @left = CONVERT(float, CONVERT(decimal(30,10), ?));"
            + " EXEC PU_SettleWriteBKRDS @RdsID=?, @SysDate=?, @iRDQuan=@q, @iSetMoney=@set, @iRealSetMoney=@real,"
            + " @iRDCost=@cost, @iRDQuanCheck=@left, @iAverPrice=@avg, @iMaterialFee=0, @iProcessFee=0, @bWBCost=1,"
            + " @g_QuanDecimal=?, @g_CostDecimal=?, @cvType=N'01', @cSysID=N'PU'";

        // 发票行：结算合计等于发票数量时写结算日期。
        internal const string BillLineSql = "UPDATE b SET b.dSDate=CONVERT(datetime, CONVERT(date, ?, 23)) FROM PurBillVouchs b"
            + " WHERE b.ID=? AND ABS(ISNULL(b.iPBVQuantity,0)-ISNULL((SELECT SUM(s.iSVQuantity) FROM PurSettleVouchs s"
            + " WHERE s.iBsID=?),0))<0.000001";
        // 配对行的发票行写入库日期（本单配到的入库单的最晚日期）。
        internal const string InDateSql = "UPDATE b SET b.dInDate=(SELECT MAX(rh.dDate) FROM PurSettleVouchs s"
            + " JOIN rdrecords01 r ON r.AutoID=s.iRdsID JOIN RdRecord01 rh ON rh.ID=r.ID WHERE s.PSVID=? AND s.iBsID=?)"
            + " FROM PurBillVouchs b WHERE b.ID=? AND EXISTS (SELECT 1 FROM PurSettleVouchs s2 WHERE s2.PSVID=? AND s2.iBsID=?"
            + " AND ISNULL(s2.iRdsID,0)<>0)";
        // 发票表头：每行都已写结算日期时写表头。
        internal const string BillHeadSql = "UPDATE PurBillVouch SET dSDate=CONVERT(datetime, CONVERT(date, ?, 23))"
            + " WHERE PBVID=? AND NOT EXISTS (SELECT 1 FROM PurBillVouchs b WHERE b.PBVID=? AND b.dSDate IS NULL)";
        // 存货最新成本：配对行（有发票）的结算单价，赠品不写（同 PU_AutoSettleBillRefRD）。
        internal const string InvCostSql = "UPDATE i SET i.iInvNCost=s.iSVCost FROM Inventory i JOIN PurSettleVouchs s"
            + " ON s.cInvCode=i.cInvCode JOIN rdrecords01 r ON r.AutoID=s.iRdsID"
            + " WHERE s.PSVID=? AND ISNULL(s.iBsID,0)<>0 AND ISNULL(r.bgift,0)=0";
        internal const string TouchSql = "UPDATE RdRecord01 SET ID=ID WHERE ID IN (SELECT r.ID FROM PurSettleVouchs s"
            + " JOIN rdrecords01 r ON r.AutoID=s.iRdsID WHERE s.PSVID=? AND s.cUpSoType=N'01')";

        static readonly string Insert = Expand(LineSql);

        // {C:col} → 有入库行抄入库行，否则抄发票行。
        internal static string Expand(string sql)
        {
            string text = sql;
            int at = text.IndexOf("{C:", StringComparison.Ordinal);
            while (at >= 0)
            {
                int end = text.IndexOf('}', at);
                string col = text.Substring(at + 3, end - at - 3);
                text = text.Substring(0, at) + "CASE WHEN r.AutoID IS NULL THEN b." + col + " ELSE r." + col + " END"
                    + text.Substring(end + 1);
                at = text.IndexOf("{C:", StringComparison.Ordinal);
            }
            return text;
        }

        // PU_GetID（PurSTID 表头、PurSTsID 表体）。表体一次取起点，本单各行依次加 1。
        internal static int NextId(object conn, string type)
        {
            int id = CoRows.AsId(Rows.Scalar(conn, NextIdSql, new object[] { type }));
            if (id <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有分配结算单" + (type == "PurSTID" ? "表头" : "表体") + "主键");
            }
            return id;
        }

        internal static void RequireFree(object conn, int psvid, int first, int count)
        {
            if (Rows.Scalar(conn, HeadTakenSql, new object[] { psvid }) != "0"
                || Rows.Scalar(conn, BodyTakenSql, new object[] { first, first + count - 1 }) != "0")
            {
                throw new BridgeException(409, "u8_rejected", IdTakenText);
            }
        }

        internal static void InsertHead(object conn, ManPlan plan, int psvid, string maker)
        {
            Insert1(conn, HeadSql, HeadArgs(plan, psvid, maker));
        }

        // 主键撞车（锁之外仍有别的写入抢先占号）按 409 处理，不当 500。
        static void Insert1(object conn, string sql, object[] args)
        {
            try
            {
                GlSql.Exec(conn, sql, args);
            }
            catch (Exception ex)
            {
                if (ArcGl.DuplicateKey(ex))
                {
                    throw new BridgeException(409, "u8_rejected", IdTakenText);
                }
                throw;
            }
        }

        internal static object[] HeadArgs(ManPlan plan, int psvid, string maker)
        {
            return new object[] { psvid, Code(psvid), Opt(plan.PtCode), plan.Date, Opt(plan.Ven), Opt(plan.Dep), Opt(plan.Person),
                plan.Rate, plan.Memo, maker };
        }

        // cSVCode：15 位补零的 PSVID（同 U8）。
        internal static string Code(int psvid)
        {
            return psvid.ToString("D15", CultureInfo.InvariantCulture);
        }

        internal static void InsertLine(object conn, ManRow row, int id, int psvid)
        {
            Insert1(conn, Insert, LineArgs(row, id, psvid));
        }

        internal static object[] LineArgs(ManRow row, int id, int psvid)
        {
            int rds = row.Rd == null ? 0 : row.Rd.Id;
            int bs = row.Bs == null ? 0 : row.Bs.Id;
            return new object[] { id, psvid, rds, bs, Text(row.Qty), Text(row.Num), Text(row.Cost), Text(row.Money),
                Text(row.ACost), Text(row.APrice), Text(row.Tax), Text(row.Sum), rds, bs };
        }

        // PU_SettleWriteBKRDS 里有 PRINT 和动态 SQL：逐个 NextRecordset 读完全部结果，过程才在服务器上执行完，
        // 错误也在读到那一处时才抛出（同 SqlScript）；只取第一个结果就放掉会让后面的回写不执行。
        internal static void WriteBack(object conn, ManPlan plan, ManRdSum sum)
        {
            object[] args = WriteBackArgs(plan, sum);
            object cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", WriteBackSql);
                ComUtil.Set(cmd, "CommandTimeout", 45);
                for (int i = 0; i < args.Length; i++)
                {
                    AddText(cmd, i, (string)args[i]);
                }
                rs = ComUtil.Call(cmd, "Execute", new object[0]);
                for (int n = 0; rs != null && n < MaxSets; n++)
                {
                    object next = ComUtil.Call(rs, "NextRecordset", new object[0]);
                    ComUtil.ReleaseOne(rs);
                    rs = next;
                }
            }
            finally
            {
                ComUtil.Final(rs);
                ComUtil.Final(cmd);
            }
        }

        const int MaxSets = 1000;
        const int AdVarWChar = 202;
        const int AdParamInput = 1;

        static void AddText(object cmd, int index, string value)
        {
            string name = "p" + index.ToString(CultureInfo.InvariantCulture);
            object param = ComUtil.Call(cmd, "CreateParameter", new object[] { name, AdVarWChar, AdParamInput, 4000, value });
            object parameters = null;
            try
            {
                parameters = ComUtil.Get(cmd, "Parameters");
                ComUtil.Call(parameters, "Append", new object[] { param });
            }
            finally
            {
                ComUtil.ReleaseOne(parameters);
                ComUtil.ReleaseOne(param);
            }
        }

        // @iAverPrice = 本单结算金额 / 数量（单价位数）；@iRDCost 为入库行暂估单价（没有时 0，PU_SettleWriteBKRDS 据此先写暂估）；
        // @iRDQuanCheck = 入库行未结算数量（事务里加锁读到的 iQuantity − iSQuantity）。
        internal static object[] WriteBackArgs(ManPlan plan, ManRdSum sum)
        {
            decimal avg = sum.Qty == 0m ? 0m : PuSettleManPlan.Round(sum.Money / sum.Qty, plan.CostDec);
            decimal cost = sum.Rd.HasACost ? sum.Rd.ACost : 0m;
            return new object[] { Text(sum.Qty), Text(sum.APrice), Text(sum.Money), Text(cost), Text(avg),
                Text(sum.Rd.Qty - sum.Rd.SQty), sum.Rd.Id.ToString(CultureInfo.InvariantCulture), plan.Date,
                plan.QuanDec.ToString(CultureInfo.InvariantCulture), plan.CostDec.ToString(CultureInfo.InvariantCulture) };
        }

        // 发票行结清、入库日期、发票表头，存货最新成本，入库单时间戳。
        internal static void Stamp(object conn, ManPlan plan, int psvid)
        {
            List<int> heads = new List<int>();
            foreach (KeyValuePair<ManBs, decimal> pair in PuSettleManPlan.BsSums(plan.Rows))
            {
                ManBs bs = pair.Key;
                GlSql.Exec(conn, BillLineSql, new object[] { plan.Date, bs.Id, bs.Id });
                GlSql.Exec(conn, InDateSql, new object[] { psvid, bs.Id, bs.Id, psvid, bs.Id });
                if (!heads.Contains(bs.Pbvid))
                {
                    heads.Add(bs.Pbvid);
                }
            }
            for (int i = 0; i < heads.Count; i++)
            {
                GlSql.Exec(conn, BillHeadSql, new object[] { plan.Date, heads[i], heads[i] });
            }
            GlSql.Exec(conn, InvCostSql, new object[] { psvid });
            GlSql.Exec(conn, TouchSql, new object[] { psvid });
        }

        internal static string Text(decimal value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static string Opt(string value)
        {
            string text = (value ?? "").Trim();
            return text.Length == 0 ? null : text;
        }
    }
}
