using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据新增 / 删除 / 审核用到的 SQL。只用参数 ?；表名、视图名只来自 QmSpec 常量。
    internal static class QmSql
    {
        internal const string ArrHeadSql = "select convert(varchar(20), h.ID) as ID, h.cCode, convert(varchar(10), h.dDate, 23) as dDate,"
            + " h.cVenCode, h.cDepCode, h.cverifier, convert(varchar(10), isnull(h.iBillType,0)) as iBillType"
            + " from PU_ArrivalVouch h where h.ID=?";
        internal const string ArrLineSql = "select convert(varchar(20), s.Autoid) as Autoid, s.cInvCode,"
            + " convert(varchar(40), isnull(s.iQuantity,0)) as Total, convert(varchar(40), isnull(s.fInspectQuantity,0)) as Used,"
            + " convert(varchar(5), isnull(s.bGsp,0)) as bGsp, convert(varchar(5), isnull(s.bInspect,0)) as bInspect,"
            + " s.cbcloser as Closer, s.cWhCode, s.cBatch, s.cordercode,"
            + " s.cUnitID, convert(varchar(40), isnull(s.iinvexchrate,0)) as Rate,"
            + " convert(varchar(10), isnull(i.iTestStyle,3)) as TestStyle"
            + " from PU_ArrivalVouchs s left join Inventory i on i.cInvCode=s.cInvCode where s.Autoid=? and s.ID=?";
        internal const string ArrUsedSql = "select convert(varchar(40), isnull(fInspectQuantity,0))"
            + " from PU_ArrivalVouchs with (updlock, holdlock) where Autoid=?";
        internal const string MoHeadSql = "select convert(varchar(20), MoId) as ID, MoCode from mom_order where MoId=?";
        internal const string MoLineSql = "select convert(varchar(20), d.MoDId) as Autoid, d.InvCode as cInvCode,"
            + " convert(varchar(40), isnull(d.Qty,0)) as Total, convert(varchar(40), isnull(d.DeclaredQty,0)) as Used,"
            + " convert(varchar(10), isnull(d.Status,0)) as Status, convert(varchar(5), isnull(d.QcFlag,0)) as QcFlag,"
            + " d.CloseUser as Closer, d.WhCode as cWhCode,"
            + " d.MDeptCode, convert(varchar(10), d.SortSeq) as SortSeq, d.AuxUnitCode as cUnitID,"
            + " convert(varchar(40), isnull(d.ChangeRate,0)) as Rate, convert(varchar(10), isnull(i.iTestStyle,3)) as TestStyle"
            + " from mom_orderdetail d left join Inventory i on i.cInvCode=d.InvCode where d.MoDId=? and d.MoId=?";
        internal const string MoUsedSql = "select convert(varchar(40), isnull(DeclaredQty,0))"
            + " from mom_orderdetail with (updlock, holdlock) where MoDId=?";
        internal const string InsHeadSql = "select convert(varchar(20), h.ID) as ID, h.CINSPECTCODE, h.CSOURCE,"
            + " convert(varchar(20), h.CSOURCEID) as CSOURCEID, h.CSOURCECODE, h.CVENCODE, h.CINSPECTDEPCODE, h.CDEPCODE,"
            + " h.CMAKER, h.CVERIFIER, convert(varchar(10), h.DDATE, 23) as DDATE, h.CTIME,"
            + " convert(varchar(10), h.DARRIVALDATE, 23) as DARRIVALDATE, h.CCHECKTYPECODE, h.CCUSCODE"
            + " from QMINSPECTVOUCHER h where h.ID=? and h.CVOUCHTYPE=?";
        internal const string InsLineSql = "select convert(varchar(20), b.AUTOID) as AUTOID,"
            + " convert(varchar(20), b.SOURCEAUTOID) as SOURCEAUTOID, b.CINVCODE, b.CWHCODE, b.CBATCH, b.CPOCODE, b.CUNITID,"
            + " convert(varchar(40), isnull(b.FCHANGRATE,0)) as Rate, convert(varchar(40), isnull(b.FQUANTITY,0)) as Total,"
            + " convert(varchar(40), isnull(b.FSUMCHECKQTY,0)) as Used, convert(varchar(10), isnull(b.ITESTSTYLE,3)) as TestStyle,"
            + " convert(varchar(20), b.IPROORDERID) as IPROORDERID, b.CPROORDERCODE,"
            + " convert(varchar(20), b.IPROORDERAUTOID) as IPROORDERAUTOID, i.cComUnitCode, i.cGroupCode,"
            + " convert(varchar(10), isnull(i.iTestRule,1)) as TestRule"
            + " from QMINSPECTVOUCHERS b left join Inventory i on i.cInvCode=b.CINVCODE where b.AUTOID=? and b.ID=?";
        internal const string InsUsedSql = "select convert(varchar(40), isnull(FSUMCHECKQTY,0)) from QMINSPECTVOUCHERS where AUTOID=?";
        internal const string ProjectSql = "select convert(varchar(20), ID) as ID, CPROJECTCODE, CPROJECTNAME, CCHKNORMALCODE,"
            + " convert(varchar(5), isnull(BSTOPUSE,0)) as Stopped from QMCHECKPROJECT where CPROJECTCODE=?";
        internal const string ProjectByIdSql = "select convert(varchar(20), ID) as ID, CPROJECTCODE, CPROJECTNAME, CCHKNORMALCODE,"
            + " convert(varchar(5), isnull(BSTOPUSE,0)) as Stopped from QMCHECKPROJECT where ID=?";
        internal const string LastProjectSql = "select top 1 convert(varchar(20), PROJECTID) from QMCHECKVOUCHER"
            + " where CINVCODE=? and CVOUCHTYPE=? and PROJECTID is not null order by ID desc";
        internal const string ProjectLinesSql = "select CCHKITEMCODE, CCHKGUIDECODE, CSTANDARD, CGUIDEUNIT,"
            + " convert(varchar(5), isnull(BGUIDETYPE,0)) as BGUIDETYPE, convert(varchar(5), isnull(IDTMETHOD,1)) as IDTMETHOD,"
            + " convert(varchar(5), isnull(BMUSTCHECK,0)) as BMUSTCHECK, CBUGGRADE"
            + " from QMCHECKPROJECTS where ID=? order by IVOUCHROWNO, AUTOID";
        internal const string PersonSql = "select cPersonName from Person where cPersonCode=?";
        internal const string DeptSql = "select cDepName from Department where cDepCode=?";
        internal const string CheckTypeSql = "select top 1 CCHECKTYPENAME from qmchecktype_base where CCHECKTYPECODE=?";
        internal const string FlowSql = "select count(*) from AuditBizObjectFlows where cBizObjectId=? and nStatus=3";
        internal const string WhSql = "select cWhName from Warehouse where cWhCode=?";
        internal const string HandNoSql = "select convert(varchar(5), isnull(bAllowHandWork,0)) from VoucherNumber where CardNumber=?";
        internal const string RequiredSql = "select distinct FieldName, CardSection from voucheritems where VT_ID=? and IsNull=1";

        internal static Dictionary<string, object> One(object conn, string sql, params object[] args)
        {
            return Rows.One(conn, sql, args);
        }

        internal static List<Dictionary<string, object>> Many(object conn, string sql, int max, params object[] args)
        {
            return Rows.Query(conn, sql, args, max);
        }

        internal static string Scalar(object conn, string sql, params object[] args)
        {
            string text = Rows.Scalar(conn, sql, args);
            return text == null ? "" : text.Trim();
        }

        internal static int MaxId(object conn, string table)
        {
            string sql = "select convert(varchar(20), isnull(max(ID),0)) from " + CoRows.Ident(table);
            return CoRows.AsId(Scalar(conn, sql));
        }

        // SQL 读出的数值文本（convert(varchar) 或 FormatCell），按不变区域解析；读不出为 0。
        internal static decimal Dec(object value)
        {
            decimal d;
            string text = Values.Text(value).Trim();
            return decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out d) ? d : 0m;
        }

        internal static string Num(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        internal static string DeptName(object conn, string code)
        {
            if (code == null || code.Length == 0)
            {
                return "";
            }
            string name = Rows.Scalar(conn, DeptSql, new object[] { code });
            if (name == null)
            {
                throw new BridgeException(400, "bad_request", "部门不存在 " + code);
            }
            return name.Trim();
        }

        internal static string CheckTypeName(object conn, string code)
        {
            return code == null || code.Length == 0 ? "" : Scalar(conn, CheckTypeSql, code);
        }

        // 该单据类型有已启用的审批流程时，新单据 IsWfControlled=1（列不许为 NULL）。
        internal static bool FlowOn(object conn, string vouchType)
        {
            return Dec(Scalar(conn, FlowSql, vouchType)) > 0m;
        }

        // U8 单据模板上设为必输的字段（voucheritems.IsNull=1）：CardSection T 是表头，B 是表体。
        internal static List<Dictionary<string, object>> Required(object conn, int vt)
        {
            return Many(conn, RequiredSql, 500, vt);
        }
    }
}
