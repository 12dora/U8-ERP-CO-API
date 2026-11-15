using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消记账的 SQL（请求连接 ctx.Conn，只用参数 ?）。冲回科目总账的三组 UPDATE 与 U8 记账对科目总账、
    // 辅助总账、多辅助总账的更新一一对应（测试账套上与 U8「恢复记账前状态」后的余额、记账标志逐行核对一致）：同样的汇总口径、同样的连接键、
    // 同样的 SET 公式，只把本次汇总的发生额取负。U8 的公式对发生额是线性的，取负再执行一次就是精确的逆运算；
    // 区别只在 U8 用共享的实体表 GL_Acc_Temp*（各会话共用，桥不碰），这里用派生表。所有语句都带 iyear。
    // 「本批凭证」= GL_mpostcond 本年度的行（U8 记账时从 GL_mpostcond1 复制，csign 列存的是 isignseq 的文本）。
    internal static class GlUnpostSql
    {
        // 与记账同一把表锁（GlPostTx.LockSql）：U8 客户端的汇总、记账等我们提交。
        internal const string LockSql = "SELECT COUNT(*) n FROM GL_mpostcond1 WITH (UPDLOCK, HOLDLOCK, TABLOCK)";

        internal const string BatchSql = "SELECT CONVERT(varchar(4), m.iperiod) p, RTRIM(m.csign) s, CONVERT(varchar(8), m.ino_id) n,"
            + " ISNULL(d.csign, N'') sign FROM GL_mpostcond m WITH (UPDLOCK, HOLDLOCK)"
            + " LEFT JOIN dsign d ON CONVERT(nvarchar(8), d.isignseq)=RTRIM(m.csign) WHERE m.iyear=? ORDER BY m.iperiod, m.csign, m.ino_id";

        internal const string PeriodSql = "SELECT CONVERT(varchar(1), ISNULL(bflag,0)) f FROM GL_mend WITH (UPDLOCK, HOLDLOCK)"
            + " WHERE iyear=? AND iperiod=?";
        internal const string LaterClosedSql = "SELECT TOP 1 CONVERT(varchar(4), iperiod) p FROM GL_mend"
            + " WHERE iyear=? AND iperiod>? AND iperiod<=12 AND ISNULL(bflag,0)<>0";

        internal const string CurrencySql = "SELECT cCurName FROM UFSYSTEM..UA_Account WHERE cAcc_Id=?";

        // 本批凭证的分录（别名 v）与 GL_mpostcond（别名 m）的连接，带年度、期间。
        internal static string Batch(string v)
        {
            return " JOIN GL_mpostcond m ON m.iyear=" + v + ".iyear AND m.iperiod=" + v + ".iperiod AND RTRIM(m.csign)=CONVERT(nvarchar(8), "
                + v + ".isignseq) AND m.ino_id=" + v + ".ino_id";
        }

        // 本批分录加锁（UPDLOCK, HOLDLOCK），返回行数。参数：年度、期间。
        internal static readonly string LockRowsSql = "SELECT COUNT(*) n FROM GL_accvouch v WITH (UPDLOCK, HOLDLOCK)" + Batch("v")
            + " WHERE v.iyear=? AND v.iperiod=?";

        const string Hit = "SELECT TOP 1 CONVERT(varchar(4), v.iperiod) + N' ' + ISNULL(v.csign, N'') + N'-' + CONVERT(varchar(8), v.ino_id) k"
            + " FROM GL_accvouch v";

        // 拒绝条件，各取第一张凭证（参数：年度、期间）。
        internal static readonly string UnpostedSql = Hit + Batch("v") + " WHERE v.iyear=? AND v.iperiod=? AND ISNULL(v.ibook,0)<>1";
        internal static readonly string MarkedSql = Hit + Batch("v") + " WHERE v.iyear=? AND v.iperiod=?"
            + " AND (ISNULL(v.iflagbank,0)<>0 OR ISNULL(v.iflagPerson,0)<>0 OR ISNULL(v.cBankReconNo,N'')<>N'' OR ISNULL(v.cWLDZFlag,N'')<>N'')";
        internal static readonly string ReversedSql = Hit + Batch("v") + " WHERE v.iyear=? AND v.iperiod=? AND ISNULL(v.coutno_id,N'')<>N''"
            + " AND EXISTS (SELECT 1 FROM GL_accvouch r WHERE r.cblueoutno_id=v.coutno_id)";
        // 本批在 GL_accvouch 里没有任何行的凭证（参数：年度）。
        internal const string MissingSql = "SELECT TOP 1 CONVERT(varchar(4), m.iperiod) + N' #' + RTRIM(m.csign) + N'-' + CONVERT(varchar(8), m.ino_id) k"
            + " FROM GL_mpostcond m WHERE m.iyear=? AND NOT EXISTS (SELECT 1 FROM GL_accvouch v WHERE v.iyear=m.iyear AND v.iperiod=m.iperiod"
            + " AND CONVERT(nvarchar(8), v.isignseq)=RTRIM(m.csign) AND v.ino_id=m.ino_id)";
        // U8 凭证界面、EAI 编辑凭证时在 GL_mvcontrol 留的锁（参数：年度）。
        internal const string EditingSql = "SELECT TOP 1 CONVERT(varchar(4), c.iperiod) + N' ' + c.csign + N'-' + CONVERT(varchar(8), c.ino_id)"
            + " + N'（' + ISNULL(c.cuser, N'') + N'）' k FROM GL_mvcontrol c JOIN dsign d ON d.csign=c.csign"
            + " JOIN GL_mpostcond m ON RTRIM(m.csign)=CONVERT(nvarchar(8), d.isignseq) AND m.iperiod=c.iperiod AND m.ino_id=c.ino_id"
            + " WHERE m.iyear=? AND (c.iyear=m.iyear OR c.iyear IS NULL)";

        // 记账标志改回记账前（U8 汇总只收 ibook=0 的凭证，未记账凭证 cbook 为 NULL）。参数：年度、期间。
        internal static readonly string VouchSql = "UPDATE v SET ibook=0, cbook=NULL FROM GL_accvouch v" + Batch("v")
            + " WHERE v.iyear=? AND v.iperiod=? AND v.ibook=1";
        internal static readonly string LeftSql = "SELECT COUNT(*) n FROM GL_accvouch v" + Batch("v")
            + " WHERE v.iyear=? AND v.iperiod=? AND ISNULL(v.ibook,0)<>0";
        internal const string ClearSql = "DELETE FROM GL_mpostcond WHERE iyear=?";

        // ---- 冲回科目总账（GL_accsum）。参数：本币名称、年度、期间、年度，然后连接条件的期间、年度。----

        const string Bal = "((CASE WHEN cbegind_c=N'贷' THEN -mb ELSE mb END)+smd+md-smc-mc)";
        const string Open = "((CASE WHEN cbegind_c=N'贷' THEN -mb ELSE mb END)+smd-smc)";
        const string Bf = "(CASE WHEN cbegind_c=N'贷' THEN -mb_f ELSE mb_f END)";
        const string Bs = "(CASE WHEN cbegind_c=N'贷' THEN -nb_s ELSE nb_s END)";

        static string Dir(string bal, string debit, string flat, string credit)
        {
            return "(CASE WHEN " + bal + ">0 THEN N'" + debit + "' WHEN " + bal + "=0 THEN N'" + flat + "' ELSE N'" + credit + "' END)";
        }

        static string Follow(string bal, string value)
        {
            return "(CASE WHEN " + bal + ">=0 THEN " + value + " ELSE -(" + value + ") END)";
        }

        // 本期期末（U8 两处的写法分别是「=0 平」「>=0 平」，前面已判过 >0，结果相同）。
        static string EndSet()
        {
            return "cendd_c=" + Dir(Bal, "借", "平", "贷") + ", cendd_c_engl=" + Dir(Bal, "Dr", "-", "Cr") + ", me=ABS" + Bal
                + ", me_f=" + Follow(Bal, Bf + "+smd_f+md_f-smc_f-mc_f") + ", ne_s=" + Follow(Bal, Bs + "+smd_s+nd_s-smc_s-nc_s");
        }

        const string AmountSet = "md=smd+md, mc=smc+mc, md_f=smd_f+md_f, mc_f=smc_f+mc_f, nd_s=smd_s+nd_s, nc_s=smc_s+nc_s";

        static string OpenSet()
        {
            return "cbegind_c=" + Dir(Open, "借", "平", "贷") + ", cbegind_c_engl=" + Dir(Open, "Dr", "-", "Cr") + ", mb=ABS" + Open
                + ", mb_f=" + Follow(Open, Bf + "+smd_f-smc_f") + ", nb_s=" + Follow(Open, Bs + "+smd_s-smc_s");
        }

        const string Lines = " FROM code k JOIN GL_accvouch v ON v.ccode=k.ccode AND v.iyear=k.iyear";
        const string LineWhere = " WHERE v.iflag IS NULL AND v.iyear=? AND v.iperiod=?";
        const string Fx = "SUM(CASE WHEN k.cexch_name IS NULL THEN 0 ELSE v.md_f END)";
        const string FxC = "SUM(CASE WHEN k.cexch_name IS NULL THEN 0 ELSE v.mc_f END)";
        const string Qty = "SUM(CASE WHEN k.cmeasure IS NULL THEN 0 ELSE v.nd_s END)";
        const string QtyC = "SUM(CASE WHEN k.cmeasure IS NULL THEN 0 ELSE v.nc_s END)";

        // 科目总账的汇总：先按末级科目、币种汇总本批分录，再按 ccode 前缀滚到各级上级科目（同 U8）。
        // multi 是总账选项 IsUseMultiCurrency：开了按币种分行，外币科目没币种的分录记本币名称（参数）。
        static string SumTable(bool multi)
        {
            string exch = multi ? "CASE WHEN c.cexch_name IS NULL THEN NULL ELSE g.ccexch_name END" : "c.cexch_name";
            return "(SELECT c.ccode, " + exch + " AS ccexch_name, -SUM(g.mmd) AS smd, -SUM(g.mmc) AS smc, -SUM(g.mmd_f) AS smd_f,"
                + " -SUM(g.mmc_f) AS smc_f, -SUM(CASE WHEN c.cmeasure IS NULL THEN 0 ELSE g.mmd_s END) AS smd_s,"
                + " -SUM(CASE WHEN c.cmeasure IS NULL THEN 0 ELSE g.mmc_s END) AS smc_s FROM code c JOIN"
                + " (SELECT k.ccode, ISNULL(v.cexch_name, ?) AS ccexch_name, SUM(v.md) AS mmd, SUM(v.mc) AS mmc, " + Fx + " AS mmd_f, "
                + FxC + " AS mmc_f, " + Qty + " AS mmd_s, " + QtyC + " AS mmc_s" + Lines + Batch("v") + LineWhere
                + " GROUP BY k.ccode, v.cexch_name) g ON g.ccode LIKE c.ccode + '%' WHERE c.iyear=? GROUP BY c.ccode, " + exch + ", c.cmeasure) t";
        }

        static string SumJoin(bool multi)
        {
            return " INNER JOIN GL_accsum s ON t.ccode=s.ccode" + (multi ? " AND ISNULL(t.ccexch_name,N'')=ISNULL(s.cexch_name,N'')" : "");
        }

        internal static string SumPeriod(bool multi)
        {
            return "UPDATE s SET " + AmountSet + ", " + EndSet() + " FROM " + SumTable(multi) + SumJoin(multi) + " AND s.iperiod=? AND s.iyear=?";
        }

        internal static string SumLater(bool multi)
        {
            return "UPDATE s SET " + OpenSet() + ", " + EndSet() + " FROM " + SumTable(multi) + SumJoin(multi)
                + " AND s.iperiod>? AND s.iperiod<=12 AND s.iyear=?";
        }

        // ---- 辅助总账（GL_accass）与多辅助总账（GL_AccMultiAss，many 为真）。参数：年度、期间，然后连接条件的期间、年度。----

        static readonly string[] AssKeys = new string[] { "cexch_name", "cdept_id", "cperson_id", "ccus_id", "csup_id", "citem_id", "citem_class" };
        // U8 的多辅助汇总不带 cDefine4、cDefine6（日期型）。
        static readonly string[] MultiKeys = new string[]
        {
            "cname", "cDefine1", "cDefine2", "cDefine3", "cDefine5", "cDefine7", "cDefine8", "cDefine9", "cDefine10", "cDefine11", "cDefine12",
            "cDefine13", "cDefine14", "cDefine15", "cDefine16"
        };

        internal const string AssFlags = "(k.bdept=1 OR k.bperson=1 OR k.bitem=1 OR k.bcus=1 OR k.bsup=1)";

        internal static string MultiFlags()
        {
            string text = "(k.bdept=1 OR k.bperson=1 OR k.bitem=1 OR k.bcus=1 OR k.bsup=1";
            for (int i = 1; i <= 16; i++)
            {
                text += " OR k.bcDefine" + i.ToString(CultureInfo.InvariantCulture) + "=1";
            }
            return text + ")";
        }

        static string[] KeysOf(bool many)
        {
            if (!many)
            {
                return AssKeys;
            }
            List<string> all = new List<string>(AssKeys);
            all.AddRange(MultiKeys);
            return all.ToArray();
        }

        static string AssTable(bool many)
        {
            string[] keys = KeysOf(many);
            string cols = "";
            for (int i = 0; i < keys.Length; i++)
            {
                cols += ", v." + keys[i];
            }
            return "(SELECT k.ccode" + cols + ", -SUM(v.md) AS smd, -SUM(v.mc) AS smc, -" + Fx + " AS smd_f, -" + FxC + " AS smc_f, -"
                + Qty + " AS smd_s, -" + QtyC + " AS smc_s" + Lines + Batch("v") + LineWhere + " AND " + (many ? MultiFlags() : AssFlags)
                + " GROUP BY k.ccode" + cols + ") t";
        }

        static string AssJoin(bool many)
        {
            string[] keys = KeysOf(many);
            string text = " INNER JOIN " + (many ? "GL_AccMultiAss" : "GL_accass") + " s ON t.ccode=s.ccode";
            for (int i = 0; i < keys.Length; i++)
            {
                // 同 U8：IsNull(列,'') 两边比，数值列的 '' 隐式转成 0。
                text += " AND ISNULL(t." + keys[i] + ",'')=ISNULL(s." + keys[i] + ",'')";
            }
            return text;
        }

        const string AddSet = "addmd=smd+addmd, addmc=smc+addmc, addmd_f=smd_f+addmd_f, addmc_f=smc_f+addmc_f, addnd_s=smd_s+addnd_s,"
            + " addnc_s=smc_s+addnc_s";

        internal static string AssPeriod(bool many)
        {
            string set = many
                ? AmountSet + ", " + AddSet + ", me=me+smd-smc, me_f=me_f+smd_f-smc_f, ne_s=ne_s+smd_s-smc_s"
                : AmountSet + ", " + EndSet();
            return "UPDATE s SET " + set + " FROM " + AssTable(many) + AssJoin(many) + " AND s.iperiod=? AND s.iyear=?";
        }

        internal static string AssLater(bool many)
        {
            string set = many
                ? "mb=mb+smd-smc, mb_f=mb_f+smd_f-smc_f, nb_s=nb_s+smd_s-smc_s, " + AddSet
                    + ", me=mb+smd+md-smc-mc, me_f=mb_f+smd_f+md_f-smc_f-mc_f, ne_s=nb_s+smd_s+nd_s-smc_s-nc_s"
                : OpenSet() + ", " + EndSet();
            return "UPDATE s SET " + set + " FROM " + AssTable(many) + AssJoin(many) + " AND s.iperiod>? AND s.iperiod<=12 AND s.iyear=?";
        }
    }
}
