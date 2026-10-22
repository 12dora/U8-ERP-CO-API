using System;
using System.Collections.Generic;

namespace U8Co
{
    // 回滚模式（请求带 undo）的 SQL 数据源：同一事务里按 id 取下游行并加行锁（UPDLOCK, ROWLOCK），
    // 先核对「属于这张发货单」再读当前值；判断本身在 HeadFixUndo.Build（纯逻辑，自检覆盖）。
    internal sealed class HeadFixDb : IHeadFixSource
    {
        const string OutLinkSql = "SELECT COUNT(*) AS n FROM rdrecords32 r JOIN DispatchLists l ON l.iDLsID=r.iDLsID"
            + " WHERE r.ID=? AND l.DLID=?";
        const string InvLinkSql = "SELECT COUNT(*) AS n FROM SaleBillVouchs b JOIN DispatchLists l ON l.iDLsID=b.iDLsID"
            + " WHERE b.SBVID=? AND l.DLID=?";
        const string ArSql = "SELECT LTRIM(RTRIM(ISNULL(cVouchType,''))) AS vt, LTRIM(RTRIM(ISNULL(cVouchID,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cPerson,''))) AS cPerson FROM Ar_Detail WITH (UPDLOCK, ROWLOCK) WHERE Auto_ID=?";
        // 发票（cVouchType + cSBVCode）有明细行来自这张发货单。
        const string ArLinkSql = "SELECT COUNT(*) AS n FROM SaleBillVouch s WHERE s.cVouchType=? AND s.cSBVCode=?"
            + " AND EXISTS (SELECT 1 FROM SaleBillVouchs b JOIN DispatchLists l ON l.iDLsID=b.iDLsID"
            + " WHERE b.SBVID=s.SBVID AND l.DLID=?)";
        readonly object _conn;
        readonly int _dlid;

        HeadFixDb(object conn, int dlid)
        {
            _conn = conn;
            _dlid = dlid;
        }

        public static List<HeadFixRow> Plan(object conn, HeadFixAsk ask, List<HeadFixField> changing)
        {
            return HeadFixUndo.Build(ask, changing, new HeadFixDb(conn, ask.Id));
        }

        public HeadFixDoc Doc(string table, int id)
        {
            bool inv = table == HeadFixUndo.InvTable;
            string link = inv ? InvLinkSql : OutLinkSql;
            if (VoucherHeadFixDown.Count(_conn, link, new object[] { id, _dlid }) == 0)
            {
                return null;
            }
            Dictionary<string, object> row = Rows.One(_conn, inv ? VoucherHeadFixDown.InvSql : VoucherHeadFixDown.OutSql,
                new object[] { id });
            if (row == null)
            {
                return null;
            }
            HeadFixDoc doc = VoucherHeadFixDown.Doc(table, id, row);
            doc.VouchType = inv ? GlSql.Col(row, "vt") : "";
            return doc;
        }

        public HeadFixArRow Ar(int autoId)
        {
            Dictionary<string, object> row = Rows.One(_conn, ArSql, new object[] { autoId });
            if (row == null)
            {
                return null;
            }
            HeadFixArRow ar = new HeadFixArRow();
            ar.AutoId = autoId;
            ar.VouchType = GlSql.Col(row, "vt");
            ar.Code = GlSql.Col(row, "code");
            ar.Person = GlSql.Col(row, "cPerson");
            bool linked = ar.VouchType.Length > 0 && ar.Code.Length > 0
                && VoucherHeadFixDown.Count(_conn, ArLinkSql, new object[] { ar.VouchType, ar.Code, _dlid }) > 0;
            return linked ? ar : null;
        }
    }
}
