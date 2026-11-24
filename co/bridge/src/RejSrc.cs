using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 不良品处理单入库的去向：来料不良品处理单（QM05）→ 采购入库（rdrecords01）、产品不良品处理单（QM06）→ 产成品入库（rdrecords10）。
    // 两类共用同一套来源规则（RejSrc）：审核、审批流、处理流程、整行入库、入库后 BFLAG 的核对与删除时的回退核对。
    internal sealed class RejKind
    {
        public readonly string Source;
        public readonly string VouchType;
        public readonly string HeadSql;
        public readonly string UsedSql;
        public readonly string RdSql;

        // 入库单表头 cSource：来源类型的中文名，与 U8 自己生成的单据一致。
        public string Title
        {
            get { return Kinds.Find(Source).Title; }
        }

        // headCols：该类来源另需的表头列（逗号开头），如产品不良品处理单的生产订单行、来料不良品处理单的到货单行。
        public RejKind(string source, string vouchType, string rdTable, string headCols)
        {
            Source = source;
            VouchType = vouchType;
            HeadSql = "select r.CREJECTCODE, r.CVERIFIER, r.CSOURCE,"
                + " convert(varchar(5), isnull(r.IsWfControlled,0)) as Wf, convert(varchar(5), isnull(r.iVerifyStateNew,0)) as WfState,"
                + " convert(varchar(20), r.CHECKID) as CheckId, r.CCHECKCODE, r.CCHECKPERSON,"
                + " convert(varchar(10), r.DCHECKDATE, 23) as CheckDate, r.CINVCODE, r.CBATCH" + headCols
                + " from QMREJECTVOUCHER r where r.ID=? and r.CVOUCHTYPE='" + vouchType + "'";
            UsedSql = "select convert(varchar(40), isnull(sum(iQuantity),0)) from " + rdTable + " where iRejectIds=?";
            RdSql = "select convert(varchar(20), b.iRejectIds) as RejId from " + rdTable
                + " b where b.ID=? and isnull(b.iRejectIds,0)<>0";
        }
    }

    // 不良品处理单行（QMREJECTVOUCHERS）的来源规则。不良品处理单没有累计入库数量列，已入库数量按入库单 iRejectIds 汇总；
    // U8 入库后把行上 BFLAG 置 1，入库单删除后须回到 0。处理流程 2 的含义来自处理方式档案（QmRejSrc.DimFlow），这里只认流程号。
    internal static class RejSrc
    {
        // 未覆盖：全部入库后 U8 是否把 QMREJECTVOUCHERS.BFLAG 置 1、入库单删除后是否回退。
        internal static readonly bool RejCheckFlag = true;

        internal static readonly RejKind Product = new RejKind("qm_product_reject", "QM06", "rdrecords10",
            // 生产订单行按 U8 参照视图 QM_RefProReject 的取法：非标订单用 ISOURCEPROORDERAUTOID，否则用 SOURCEAUTOID。
            ", convert(varchar(20), case when isnull(r.ISOURCEPROORDERAUTOID,0)<>0 then r.ISOURCEPROORDERAUTOID"
            + " else r.SOURCEAUTOID end) as MoDId");

        internal static readonly RejKind Incoming = new RejKind("qm_incoming_reject", "QM05", "rdrecords01",
            ", r.CVENCODE, convert(varchar(20), r.SOURCEAUTOID) as ArrLine");

        const string LineSql = "select convert(varchar(20), s.AUTOID) as AutoId, isnull(s.CDIMINVCODE,'') as DimInv,"
            + " convert(varchar(40), s.FDIMQUANTITY) as DimQty, convert(varchar(40), isnull(s.FQUANTITY,0)) as RejQty,"
            + " convert(varchar(10), isnull(s.IDISPOSEFLOW,-1)) as Flow, convert(varchar(5), isnull(s.BFLAG,0)) as Done,"
            + " isnull(s.CDIMBATCH,'') as DimBatch from QMREJECTVOUCHERS s where s.AUTOID=? and s.ID=?";
        const string FlagSql = "select convert(varchar(5), isnull(BFLAG,0)) from QMREJECTVOUCHERS where AUTOID=?";

        // 表头：已审核（审批流单据须审批完成）。来源单据（生产订单 / 到货单）由各去向自己核对。
        internal static Dictionary<string, object> Head(object conn, RejKind kind, int rejectId)
        {
            Dictionary<string, object> rej = Rows.One(conn, kind.HeadSql, new object[] { rejectId });
            if (rej == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(rej, "CVERIFIER").Length == 0 || !WfDone(rej))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            return rej;
        }

        // 未覆盖：审批流单据 iVerifyStateNew=2 视为审批完成。
        static bool WfDone(Dictionary<string, object> rej)
        {
            return CoRows.Col(rej, "Wf") != "1" || CoRows.Col(rej, "WfState") == "2";
        }

        // 只接处理流程 2（降级 / 让步接收 / 升级，入库）的行；处理后存货、数量为空时退回原存货、不良品数量（未经实测）。
        // 返回表头加表体各列，另有 InvCode（入库存货）、BaseQty（处理后数量）。
        internal static Dictionary<string, object> Line(object conn, Dictionary<string, object> rej, int rejectId, int lineId)
        {
            Dictionary<string, object> body = Rows.One(conn, LineSql, new object[] { lineId, rejectId });
            if (body == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (CoRows.Col(body, "Flow") != QmRejSrc.DimFlow.ToString(CultureInfo.InvariantCulture))
            {
                throw new BridgeException(409, "state_mismatch", "该不良品处理方式不能入库");
            }
            if (CoRows.Col(body, "Done") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单已入库完毕");
            }
            Dictionary<string, object> src = new Dictionary<string, object>(rej);
            foreach (KeyValuePair<string, object> kv in body)
            {
                src[kv.Key] = kv.Value;
            }
            string inv = CoRows.Col(body, "DimInv");
            src["InvCode"] = inv.Length > 0 ? inv : CoRows.Col(rej, "CINVCODE");
            string dim = CoRows.Col(body, "DimQty");
            src["BaseQty"] = dim.Length > 0 ? dim : CoRows.Col(body, "RejQty");
            return src;
        }

        // 数量：不超过剩余的处理后数量，且必须一次入库全部（实测 U8 不接受部分入库：「存货…入库数量合计必须等于降级后数量」）。
        internal static void CheckQty(object conn, RejKind kind, Dictionary<string, object> src, int lineId, decimal qty)
        {
            decimal left = MfgGen.Num(CoRows.Col(src, "BaseQty")) - Used(conn, kind, lineId);
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            if (!MfgGen.Same(qty, left))
            {
                throw new BridgeException(409, "state_mismatch",
                    "参照不良品处理单须一次入库全部处理后数量，本行应为 " + MfgGen.Dec(left));
            }
        }

        internal static decimal Used(object conn, RejKind kind, int lineId)
        {
            return MfgGen.Num(Rows.Scalar(conn, kind.UsedSql, new object[] { lineId }));
        }

        internal static decimal Flag(object conn, int lineId)
        {
            return MfgGen.Num(Rows.Scalar(conn, FlagSql, new object[] { lineId }));
        }
    }

    // 生单：Insert 前后在同一事务里核对一行：已入库数量加了本次数量；入库满额时 BFLAG 为 1。对不上就回滚（409）。
    internal sealed class RejLineGuard
    {
        readonly RejKind _kind;
        readonly int _lineId;
        readonly decimal _qty;
        readonly decimal _base;
        decimal _used;

        public RejLineGuard(RejKind kind, int lineId, decimal qty, decimal baseQty)
        {
            _kind = kind;
            _lineId = lineId;
            _qty = qty;
            _base = baseQty;
        }

        public void Before(object conn)
        {
            _used = RejSrc.Used(conn, _kind, _lineId);
        }

        // 返回保存后的已入库数量，给 CheckFlag。
        public decimal CheckUsed(object conn)
        {
            decimal used = RejSrc.Used(conn, _kind, _lineId);
            if (!MfgGen.Same(used, _used + _qty))
            {
                throw MfgGen.Mismatch("U8 没有把入库行挂到不良品处理单", _used, used);
            }
            return used;
        }

        public void CheckFlag(object conn, decimal used)
        {
            if (RejSrc.RejCheckFlag && used >= _base && RejSrc.Flag(conn, _lineId) != 1m)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有把不良品处理单标为已入库");
            }
        }

        public void After(object conn)
        {
            CheckFlag(conn, CheckUsed(conn));
        }
    }

    // 删除：入库单上挂的不良品处理单行，该行已无任何入库时 BFLAG 必须回到 0（U8 的回退未经实测，没退就回滚）。
    internal sealed class RejReverse
    {
        readonly RejKind _kind;
        readonly int _rdId;
        readonly List<int> _lines = new List<int>();

        public RejReverse(RejKind kind, int rdId)
        {
            _kind = kind;
            _rdId = rdId;
        }

        public void Before(object conn)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, _kind.RdSql, new object[] { _rdId }, 500);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                _lines.Add(CoRows.AsId(CoRows.Col(rows[i], "RejId")));
            }
        }

        public void After(object conn)
        {
            for (int i = 0; RejSrc.RejCheckFlag && i < _lines.Count; i++)
            {
                if (RejSrc.Used(conn, _kind, _lines[i]) == 0m && RejSrc.Flag(conn, _lines[i]) != 0m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有回退不良品处理单的已入库标记");
                }
            }
        }
    }
}
