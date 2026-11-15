using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票应付审核 / 销售发票应收审核（ArapAudit）的类型参数。表名、列名只来自这里，调用方的值只进参数。
    internal sealed class ArapAuditSpec
    {
        public const string Refused = "只有采购发票、销售发票支持应收应付审核";

        public string Kind;
        // 登录子系统，也是 clsAccount_AP / clsPub_AP 的 Init 子系统参数。
        public string Sub;
        // 文案里的「应付」/「应收」。
        public string Side;
        public string Detail;
        public string MendFlag;
        // Sign 条件里的主键属性名（大小写敏感；写成 cLink 会报「使用 Null 无效」）。
        public string IdAttr;
        public string CanSign;
        public string Sign;
        public string CancelSign;
        // 事务里带锁读表头（UPDLOCK, HOLDLOCK），过闸门用。
        public string HeadSql;
        // 不带锁读审核状态：事务里提交前核对、新连接回读、vouchers/load 的 state。
        public string StateSql;
        public string[] Types;
        public string VerifyRule;
        public string UnverifyRule;

        public static bool Supports(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            return name == "purchase_invoice" || name == "sale_invoice";
        }

        public static ArapAuditSpec Of(VoucherKind kind)
        {
            if (!Supports(kind))
            {
                throw new BridgeException(400, "bad_request", Refused);
            }
            return kind.Name == "purchase_invoice" ? Purchase() : Sale();
        }

        // PurBillVouchs.cClue：已应付审核的发票表体都有（AP 线索号），U8 Sign 的 WHERE 带 cClue Is Null。
        static ArapAuditSpec Purchase()
        {
            ArapAuditSpec s = Make("purchase_invoice", "AP", "应付", "PBVID", "PU");
            s.Sign = "Sign_PurBill";
            s.CancelSign = "CancelSign_PurBill";
            s.Types = new string[] { "01", "02" };
            s.HeadSql = "select convert(varchar(20), h.PBVID) as id, h.cPBVCode as code, h.cPBVBillType as vtype, "
                + "h.cVerifier as reviewer, h.cPBVVerifier as auditor, "
                + "case when isnull(h.IsWfControlled,0)<>0 then 1 else 0 end as wf, "
                + "case when isnull(h.iNetLock,0)<>0 then 1 else 0 end as locked, "
                + "(select count(*) from PurBillVouchs b where b.PBVID=h.PBVID and b.cClue is not null) as clue, "
                + "h.cVenCode, h.cDepCode from PurBillVouch h with (UPDLOCK, HOLDLOCK) where h.PBVID=?";
            s.StateSql = "select h.cPBVVerifier as auditor, convert(varchar(10), h.dverifydate, 23) as audit_date, "
                + Counts("h.cPBVBillType", "h.cPBVCode", s.Detail) + " from PurBillVouch h where h.PBVID=?";
            return s;
        }

        // 销售发票：复核人是 cChecker，应收审核人是 cVerifier（日期 dArverifydate）。表头没有网络锁列，不查。
        static ArapAuditSpec Sale()
        {
            ArapAuditSpec s = Make("sale_invoice", "AR", "应收", "SBVID", "SA");
            s.Sign = "Sign_SaleBill";
            s.CancelSign = "CancelSign_SaleBill";
            s.Types = new string[] { "26", "27" };
            s.HeadSql = "select convert(varchar(20), h.SBVID) as id, h.cSBVCode as code, h.cVouchType as vtype, "
                + "h.cChecker as reviewer, h.cVerifier as auditor, "
                + "case when isnull(h.iswfcontrolled,0)<>0 then 1 else 0 end as wf, 0 as locked, "
                + "(select count(*) from SaleBillVouchs b where b.SBVID=h.SBVID and b.cClue is not null) as clue, "
                + "h.cCusCode, h.cDepCode from SaleBillVouch h with (UPDLOCK, HOLDLOCK) where h.SBVID=?";
            s.StateSql = "select h.cVerifier as auditor, convert(varchar(10), h.dArverifydate, 23) as audit_date, "
                + Counts("h.cVouchType", "h.cSBVCode", s.Detail) + " from SaleBillVouch h where h.SBVID=?";
            return s;
        }

        static ArapAuditSpec Make(string kind, string sub, string side, string idAttr, string canPrefix)
        {
            ArapAuditSpec s = new ArapAuditSpec();
            s.Kind = kind;
            s.Sub = sub;
            s.Side = side;
            s.IdAttr = idAttr;
            s.CanSign = canPrefix + "VouchCanSign";
            s.Detail = sub == "AR" ? "Ar_Detail" : "Ap_Detail";
            s.MendFlag = sub == "AR" ? "bflag_AR" : "bflag_AP";
            s.VerifyRule = "write:" + kind + ":arap_verify";
            s.UnverifyRule = "write:" + kind + ":arap_unverify";
            return s;
        }

        // own：本单审核登记的原始行（cProcStyle = cVouchType）；pz：本单任一行已制单。
        static string Counts(string type, string code, string detail)
        {
            string where = " d where d.cVouchType=" + type + " and d.cVouchID=" + code;
            return "(select count(*) from " + detail + where + " and d.cProcStyle=d.cVouchType) as own, "
                + "(select count(*) from " + detail + where + " and isnull(d.cPZid,N'')<>N'') as pz";
        }
    }

    // 闸门查询和状态读取。
    internal static class ArapAuditSql
    {
        public static Dictionary<string, object> Locked(object conn, ArapAuditSpec spec, int id)
        {
            Dictionary<string, object> head = Rows.One(conn, spec.HeadSql, new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return head;
        }

        public static Dictionary<string, object> State(object conn, ArapAuditSpec spec, int id)
        {
            return Rows.One(conn, spec.StateSql, new object[] { id });
        }

        public static bool HasVoucher(object conn, ArapAuditSpec spec, string type, string code)
        {
            string sql = "select top 1 convert(varchar(20), d.Auto_ID) from " + spec.Detail
                + " d where d.cVouchType=? and d.cVouchID=? and isnull(d.cPZid,N'')<>N''";
            return Rows.Scalar(conn, sql, new object[] { type, code }) != null;
        }

        // 本单的非原始行（核销、转账、汇兑等），或本单作为对方单据出现在别的单据的行上。
        // 现结行（cProcStyle='XJ'，随发票登记的现结 / 现付）不算：U8 的 CancelSign 只拒绝「不是本单也不是 XJ」的处理方式
        // （未经实测，按此保守处理）。
        public static bool HasOther(object conn, ArapAuditSpec spec, string type, string code)
        {
            string sql = "select top 1 convert(varchar(20), d.Auto_ID) from " + spec.Detail + " d where "
                + "(d.cVouchType=? and d.cVouchID=? and isnull(d.cProcStyle,N'') not in (d.cVouchType, N'XJ')) or "
                + "(d.cCoVouchType=? and d.cCoVouchID=? and not (d.cVouchType=? and d.cVouchID=?))";
            return Rows.Scalar(conn, sql, new object[] { type, code, type, code, type, code }) != null;
        }

        // 审核登记行所在期间（年取 dRegDate）已结账。
        public static bool OwnPeriodClosed(object conn, ArapAuditSpec spec, string type, string code)
        {
            string sql = "select top 1 convert(varchar(10), d.iPeriod) from " + spec.Detail + " d inner join GL_mend m "
                + "on m.iyear=year(d.dRegDate) and m.iperiod=d.iPeriod where d.cVouchType=? and d.cVouchID=? "
                + "and d.cProcStyle=d.cVouchType and isnull(m." + spec.MendFlag + ",0)<>0";
            return Rows.Scalar(conn, sql, new object[] { type, code }) != null;
        }

        // 登录日期所在年、月在 GL_mend 上已结账（U8 按登录日期登记审核行，iRegDateStyle=1）。
        public static bool LoginPeriodClosed(object conn, ArapAuditSpec spec, string date)
        {
            DateTime day;
            if (date == null || !DateTime.TryParseExact(date.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", "登录日期无效");
            }
            string sql = "select top 1 convert(varchar(10), iperiod) from GL_mend where iyear=? and iperiod=? and isnull("
                + spec.MendFlag + ",0)<>0";
            return Rows.Scalar(conn, sql, new object[] { day.Year, day.Month }) != null;
        }

        public static Dictionary<string, object> StateOf(Dictionary<string, object> row)
        {
            string auditor = CoRows.Col(row, "auditor");
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["arap_verified"] = auditor.Length > 0;
            state["arap_verifier"] = auditor;
            state["arap_verified_at"] = auditor.Length > 0 ? CoRows.Col(row, "audit_date") : "";
            state["gl_voucher"] = Count(row, "pz") > 0;
            return state;
        }

        public static int Count(Dictionary<string, object> row, string name)
        {
            int n;
            if (int.TryParse(CoRows.Col(row, name), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }
    }
}
