using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 来源校验：生产订单须已审核（Status=3）未关闭；子件须属于该订单且为领料方式；
    // 产品检验单须已审核、来自生产订单，入库数量不超过合格数 + 让步接收 − 累计入库；合并检验的检验单按来源逐行（MfgGenMerge）。
    // 行上可带货位 cposition，生单前按仓库的货位管理核对（CheckPositions）。
    internal static partial class MfgGen
    {
        const string MoSql = "select MoCode from mom_order where MoId=?";
        const string AllocSql = "select convert(varchar(20), a.AllocateId) as AllocateId,"
            + " convert(varchar(20), a.MoDId) as MoDId, a.InvCode, convert(varchar(40), a.Qty) as Qty, a.OpSeq,"
            + " convert(varchar(10), isnull(a.WIPType,0)) as WIPType, convert(varchar(5), isnull(a.ByproductFlag,0)) as Byproduct,"
            + " convert(varchar(20), d.SortSeq) as MoSeq, d.InvCode as ProdCode, convert(varchar(40), d.Qty) as MoQty,"
            + " d.MDeptCode, convert(varchar(10), d.Status) as Status"
            + " from mom_moallocate a join mom_orderdetail d on d.MoDId=a.MoDId where a.AllocateId=? and d.MoId=?";
        const string CheckSql = "select c.CCHECKCODE, c.CVERIFIER, c.CSOURCE, convert(varchar(20), c.SOURCEAUTOID) as MoDId,"
            + " c.CINVCODE, convert(varchar(40), isnull(c.FREGQUANTITY,0)) as RegQty,"
            + " convert(varchar(40), isnull(c.FCONQUANTIY,0)) as ConQty, convert(varchar(40), isnull(c.FsumQuantity,0)) as SumQty,"
            + " convert(varchar(5), isnull(c.BPROINFLAG,0)) as InDone, convert(varchar(5), isnull(c.BMERGECHECKFLAG,0)) as Merged,"
            + " c.CCHECKPERSONCODE, convert(varchar(10), c.DDATE, 23) as CheckDate"
            + " from QMCHECKVOUCHER c where c.ID=? and c.CVOUCHTYPE='QM04'";
        const string DetailSql = "select convert(varchar(20), o.MoId) as MoId, o.MoCode, convert(varchar(20), d.SortSeq) as MoSeq,"
            + " d.InvCode as ProdCode, d.MDeptCode, convert(varchar(10), d.Status) as Status"
            + " from mom_orderdetail d join mom_order o on o.MoId=d.MoId where d.MoDId=?";
        const string WhSql = "select cWhCode from Warehouse where cWhCode=?";
        const string RdSql = "select convert(varchar(5), isnull(bRdFlag,0)) as flag, convert(varchar(5), isnull(bRdEnd,0)) as leaf"
            + " from Rd_Style where cRdCode=?";
        const string DeptSql = "select cDepCode from Department where cDepCode=?";

        // 返回的表头行带 MoId/MoCode 和第一行子件所属订单行的产品、数量、部门；所有子件须同一订单行。
        static Dictionary<string, object> LoadMo(object conn, int moId, object[] lines, List<MfgLine> into)
        {
            Dictionary<string, object> mo = Rows.One(conn, MoSql, new object[] { moId });
            if (mo == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string modId = "";
            for (int i = 0; i < lines.Length; i++)
            {
                MfgLine line = AllocLine(conn, moId, lines[i]);
                string own = CoRows.Col(line.Src, "MoDId");
                if (i > 0 && own != modId)
                {
                    throw new BridgeException(400, "bad_request", "明细须属于同一生产订单行");
                }
                modId = own;
                into.Add(line);
            }
            Dictionary<string, object> head = new Dictionary<string, object>(into[0].Src);
            head["MoId"] = moId.ToString(CultureInfo.InvariantCulture);
            head["MoCode"] = CoRows.Col(mo, "MoCode");
            return head;
        }

        static MfgLine AllocLine(object conn, int moId, object raw)
        {
            Dictionary<string, object> line = MfgReq.LineOf(raw, true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            Dictionary<string, object> src = Rows.One(conn, AllocSql, new object[] { id, moId });
            if (src == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            RequireReleased(CoRows.Col(src, "Status"));
            if (CoRows.Col(src, "WIPType") != "3" || CoRows.Col(src, "Byproduct") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "该子件不是领料方式，不能生成材料出库");
            }
            return NewLine(line, id, qty, src);
        }

        static Dictionary<string, object> LoadCheck(object conn, int checkId, object[] lines, List<MfgLine> into)
        {
            Dictionary<string, object> check = Rows.One(conn, CheckSql, new object[] { checkId });
            if (check == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RequireCheck(check);
            if (CoRows.Col(check, "Merged") == "1")
            {
                return LoadMerged(conn, checkId, check, lines, into);
            }
            if (lines.Length != 1)
            {
                throw BridgeException.BadField("lines", "非合并检验的检验单只能有 1 行");
            }
            Dictionary<string, object> line = MfgReq.LineOf(lines[0], true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            if (id != checkId)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            Dictionary<string, object> detail = Rows.One(conn, DetailSql, new object[] { CoRows.AsId(CoRows.Col(check, "MoDId")) });
            if (detail == null)
            {
                throw new BridgeException(409, "state_mismatch", "检验单对应的生产订单不存在");
            }
            RequireReleased(CoRows.Col(detail, "Status"));
            decimal left = Num(check, "RegQty") + Num(check, "ConQty") - Num(check, "SumQty");
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            into.Add(NewLine(line, id, qty, check));
            Dictionary<string, object> head = new Dictionary<string, object>(detail);
            head["MoDId"] = CoRows.Col(check, "MoDId");
            return head;
        }

        static void RequireCheck(Dictionary<string, object> check)
        {
            if (CoRows.Col(check, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            // 合并检验的表头 SOURCEAUTOID 只是其中一个来源（有的为 0），订单行按来源取（MfgGenMerge）。
            bool merged = CoRows.Col(check, "Merged") == "1";
            if (CoRows.Col(check, "CSOURCE") != "生产订单" || (!merged && CoRows.AsId(CoRows.Col(check, "MoDId")) <= 0))
            {
                throw new BridgeException(409, "state_mismatch", "检验单来源不是生产订单");
            }
            if (CoRows.Col(check, "InDone") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "检验单已入库完毕");
            }
        }

        // mom_orderdetail.Status：1 开立、2 锁定、3 审核、4 关闭。
        static void RequireReleased(string status)
        {
            if (status == "4")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已关闭");
            }
            if (status != "3")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单未审核");
            }
        }

        static MfgLine NewLine(Dictionary<string, object> line, int id, decimal qty, Dictionary<string, object> src)
        {
            MfgLine item = new MfgLine();
            item.Id = id;
            item.Qty = qty;
            item.Batch = MfgReq.Text(line, "cbatch");
            item.Pos = MfgReq.Text(line, "cposition");
            item.Memo = MfgReq.Text(line, "cbmemo");
            item.Src = src;
            return item;
        }

        // 仓库、收发类别（末级且方向对）、部门先查一遍，免得 U8 报不明错误。
        static void CheckArchives(object conn, MfgJob job, bool inbound)
        {
            string wh = MfgReq.Text(job.Head, "cwhcode");
            if (Rows.One(conn, WhSql, new object[] { wh }) == null)
            {
                throw new BridgeException(400, "bad_request", "仓库不存在");
            }
            Dictionary<string, object> rd = Rows.One(conn, RdSql, new object[] { job.RdCode });
            bool flagOk = rd != null && CoRows.Col(rd, "flag") == (inbound ? "1" : "0");
            if (!flagOk || CoRows.Col(rd, "leaf") != "1")
            {
                throw new BridgeException(400, "bad_request", "收发类别无效");
            }
            if (job.DeptCode.Length > 0 && Rows.One(conn, DeptSql, new object[] { job.DeptCode }) == null)
            {
                throw new BridgeException(400, "bad_request", "部门不存在");
            }
        }

        // 货位：仓库启用货位管理时每行必须填该仓库的末级货位，未启用的不能填（规则和用语同无来源采购入库 StockPurInPos）。
        // 桥只写表体 cPosition，货位台账 InvPosition 由 U8 写（同无来源采购入库，货位 DOM 传空的；10 / 11 也在保存时写，已在测试账套核对）。
        static void CheckPositions(object conn, MfgJob job)
        {
            string wh = MfgReq.Text(job.Head, "cwhcode");
            bool pos = StockPurInPos.WhPos(conn, wh);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["cposition"] = job.Lines[i].Pos ?? "";
                try
                {
                    StockPurInPos.CheckLine(conn, wh, pos, row, true);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex.WithField("cposition"), "lines", i);
                }
            }
        }

        static decimal Num(Dictionary<string, object> row, string name)
        {
            decimal value;
            if (!StockUnits.Dec(CoRows.Col(row, name), out value))
            {
                return 0m;
            }
            return value;
        }
    }
}
