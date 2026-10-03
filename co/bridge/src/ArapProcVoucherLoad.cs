using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 处理制单前的读取和闸门（在事务里，带锁）。拒绝一律 409 state_mismatch，批次不存在 404。
    // 同 U8 制单列表：批次的明细都还没有凭证号（cPZid 为空）、登记期间应收（应付）未结账；桥另外要求 9I / 9J / BZ 是本币。
    internal static class ArapProcVoucherLoad
    {
        public const int MaxRows = 2000;
        const string Styles = "(N'9I',N'9J',N'BZ',N'9M',N'9N',N'9A',N'9D',N'9E',N'9C',N'9G',N'9H')";
        const string RowCols = "convert(varchar(20), d.Auto_ID) as aid, d.Auto_ID as n, d.cProcStyle as ps, d.cCancelNo as cno, "
            + "isnull(d.cFlag,N'') as fl, convert(varchar(10), isnull(d.iFlag,0)) as iflag, d.cVouchType as vt, d.cVouchID as vid, "
            + "d.cDwCode, d.cCode, {dm} as dm, {cm} as cm, isnull(d.cexch_name,N'') as cur, convert(varchar(10), isnull(d.iPeriod,0)) as per, "
            + "convert(varchar(4), year(d.dRegDate)) as ry, convert(varchar(10), d.dRegDate, 23) as rd, isnull(d.cPZid,N'') as pz, "
            + "d.cDeptCode, d.cPerson, d.cItem_Class, d.cItemCode, d.cInvCode, d.cDigest";

        public static string RowSql(int count)
        {
            string inList = "(?" + new StringBuilder().Insert(0, ",?", count - 1).ToString() + ")";
            string cols = RowCols.Replace("{dm}", WriteoffSql.Dec("d.iDAmount", 2)).Replace("{cm}", WriteoffSql.Dec("d.iCAmount", 2));
            string where = " with (UPDLOCK, HOLDLOCK) where d.cCancelNo in " + inList + " and d.cProcStyle in " + Styles;
            return "select N'AR' as led, " + cols + " from Ar_Detail d" + where + " union all select N'AP' as led, " + cols
                + " from Ap_Detail d" + where + " order by led desc, n";
        }

        public static List<ProcVoucherRow> Load(object conn, ProcVoucherAsk ask)
        {
            object[] nos = ask.CancelNos.ToArray();
            List<Dictionary<string, object>> raw = Rows.Query(conn, RowSql(nos.Length), GlSql.With(nos, nos), MaxRows + 1);
            if (raw.Count > MaxRows)
            {
                throw ArapVoucherDoc.Refuse("这些批次的往来明细超过 " + MaxRows.ToString(CultureInfo.InvariantCulture) + " 行，请分几次制单");
            }
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            foreach (Dictionary<string, object> r in raw)
            {
                rows.Add(ProcVoucherRow.Of(r));
            }
            foreach (string no in ask.CancelNos)
            {
                CheckBatch(ask, no, rows);
            }
            return rows;
        }

        // 一个批次：存在；处理类型与批次号一致；应收冲应付 / 应付冲应收两张表都有行，并账、汇兑损益只在 flag 那张表；都未制单。
        internal static void CheckBatch(ProcVoucherAsk ask, string no, List<ProcVoucherRow> rows)
        {
            bool ar = false;
            bool ap = false;
            foreach (ProcVoucherRow row in rows)
            {
                if (row.CancelNo != no)
                {
                    continue;
                }
                if (row.Ledger == "AR")
                {
                    ar = true;
                }
                else
                {
                    ap = true;
                }
                CheckBatchRow(ask, no, row);
            }
            Sides(ask, no, ar, ap);
        }

        static void CheckBatchRow(ProcVoucherAsk ask, string no, ProcVoucherRow row)
        {
            if (row.Style != ask.Style)
            {
                throw ArapVoucherDoc.Refuse("批次 " + no + " 的处理类型是 " + row.Style + "，不是" + ArapProcVoucherReq.Title(ask.Style));
            }
            if (row.Pz.Length > 0)
            {
                throw ArapVoucherDoc.Refuse("批次 " + no + " 已制单（外部业务号 " + row.Pz + "），不能重复制单");
            }
        }

        static void Sides(ProcVoucherAsk ask, string no, bool ar, bool ap)
        {
            if (!ar && !ap)
            {
                throw new BridgeException(404, "not_found", "处理批次不存在：" + no);
            }
            // 票据背书（9E）：票据所在的账必有票据行，被背书单位的单据在另一张表（应收票据背书时是应付明细）。
            if (ask.Style == "9E")
            {
                if (!(ask.Flag == "AR" ? ar : ap))
                {
                    throw ArapVoucherDoc.Refuse("批次 " + no + " 缺票据所在一方的往来明细，请在 U8 客户端处理");
                }
                return;
            }
            Pair(ask, no, ar, ap);
        }

        static void Pair(ProcVoucherAsk ask, string no, bool ar, bool ap)
        {
            bool both = ask.Style == "9I" || ask.Style == "9J";
            if (both && !(ar && ap))
            {
                throw ArapVoucherDoc.Refuse("批次 " + no + " 只有一方的往来明细（应收冲应付 / 应付冲应收应在应收、应付两边都有），请在 U8 客户端处理");
            }
            bool other = ask.Flag == "AR" ? ap : ar;
            if (!both && other)
            {
                throw ArapVoucherDoc.Refuse("批次 " + no + " 在另一方的往来明细里也有行，请在 U8 客户端制单");
            }
        }

        // 逐行：登记过（iPeriod > 0）、登记期间该表的应收 / 应付未结账；9I / 9J / BZ 只收本币（9M 是外币单据的本币差额）。
        public static void CheckRows(object conn, ProcVoucherAsk ask, List<ProcVoucherRow> rows)
        {
            string local = WriteoffSql.LocalCurrency(conn);
            HashSet<string> open = new HashSet<string>(StringComparer.Ordinal);
            foreach (ProcVoucherRow row in rows)
            {
                if (row.Period <= 0)
                {
                    throw ArapVoucherDoc.Refuse("批次 " + row.CancelNo + " 的往来明细没有登记期间");
                }
                if (!ask.ExchangeGain && row.Cur.Length > 0 && row.Cur != local)
                {
                    throw ArapVoucherDoc.Refuse("批次 " + row.CancelNo + " 是外币（" + row.Cur + "），外币处理制单暂不支持");
                }
                string key = row.Ledger + "\u0001" + row.RegYear.ToString(CultureInfo.InvariantCulture) + "\u0001"
                    + row.Period.ToString(CultureInfo.InvariantCulture);
                if (open.Contains(key))
                {
                    continue;
                }
                if (WriteoffSql.Closed(conn, row.Ledger, row.RegYear, row.Period))
                {
                    throw ArapVoucherDoc.Refuse(Side(row.Ledger) + "已结账（批次 " + row.CancelNo + " 的处理登记期间）");
                }
                open.Add(key);
            }
        }

        // 数据权限按 flag 那张表的明细（往来单位、部门、业务员），同制单；另一张表（应收冲应付的应付方）是对方单位，不按本方规则查。
        public static void Allowed(WorkContext ctx, PermRule rule, ProcVoucherAsk ask, List<ProcVoucherRow> rows)
        {
            PermContext p = PermCheck.Of(ctx);
            foreach (ProcVoucherRow row in rows)
            {
                if (row.Ledger != ask.Flag)
                {
                    continue;
                }
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["cDwCode"] = row.Dw;
                one["cDeptCode"] = row.Dept;
                one["cPerson"] = row.Person;
                if (!PermCheck.RowAllowed(p, rule, one))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // 制单日期：没给取最晚的处理日期；不早于任何批次的处理日期，在登录年度内，总账该期间未结账，涉及的应收 / 应付该月未结账。
        public static string Date(object conn, ProcVoucherAsk ask, List<ProcVoucherRow> rows, int loginYear)
        {
            string last = "";
            foreach (ProcVoucherRow row in rows)
            {
                if (string.CompareOrdinal(row.RegDate, last) > 0)
                {
                    last = row.RegDate;
                }
            }
            string date = ask.Date.Length > 0 ? ask.Date : last;
            if (string.CompareOrdinal(date, last) < 0)
            {
                throw ArapVoucherDoc.Refuse("制单日期 " + date + " 不能早于处理日期 " + last);
            }
            DateTime day = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (day.Year != loginYear)
            {
                throw ArapVoucherDoc.Refuse("制单日期必须在登录年度 " + loginYear.ToString(CultureInfo.InvariantCulture)
                    + " 内（请求的 date 是登录日期，制单日期用 voucher_date）");
            }
            GlState.PeriodOpen(conn, day.Year, day.Month);
            List<string> ledgers = new List<string>();
            foreach (ProcVoucherRow row in rows)
            {
                if (!ledgers.Contains(row.Ledger))
                {
                    ledgers.Add(row.Ledger);
                    if (WriteoffSql.Closed(conn, row.Ledger, day.Year, day.Month))
                    {
                        throw ArapVoucherDoc.Refuse(Side(row.Ledger) + "已结账（制单日期所在月份）");
                    }
                }
            }
            return date;
        }

        // 防重复制单：总账里已有本系统（或 GL）生成、coutsign 相同、来源是这些单据的凭证，其外部业务号却没有任何往来明细引用——
        // 上次处理制单 504（凭证已导入、明细没回写）留下的。拒绝并点名该凭证。9M 的 coutid 是「单号 分录号」。
        public static void NoOrphan(object conn, ProcVoucherAsk ask, List<ProcVoucherRow> rows)
        {
            const string Sql = "select top 1 isnull(g.coutno_id,N'') as pz, convert(varchar(6), g.iyear) as y, convert(varchar(4), g.iperiod) as p, "
                + "g.csign as s, convert(varchar(12), g.ino_id) as n from GL_accvouch g where (g.coutsysname=? or g.coutsysname=N'GL') "
                + "and g.coutsign=? and g.coutbillsign=? and (g.coutid=? or left(g.coutid, len(?)+1)=?) "
                + "and not exists (select 1 from Ar_Detail d where d.cPZid=g.coutno_id) "
                + "and not exists (select 1 from Ap_Detail d where d.cPZid=g.coutno_id)";
            // 坏账处理：计提 9F 的凭证由坏账准备参数行引用。
            string sql = ask.Bad ? Sql + ArapProcVoucherBad.OrphanCond : Sql;
            string outSign = ArapProcVoucherReq.OutSign(ask.Style, ask.Flag);
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (ProcVoucherRow row in rows)
            {
                if (!seen.Add(row.VType + "\u0001" + row.VId))
                {
                    continue;
                }
                object[] args = new object[] { ask.Flag, outSign, row.VType, row.VId, row.VId, row.VId + " " };
                Dictionary<string, object> hit = Rows.One(conn, sql, args);
                if (hit == null)
                {
                    continue;
                }
                string at = CoRows.Col(hit, "y") + "年" + CoRows.Col(hit, "p") + "期 " + CoRows.Col(hit, "s") + "-" + CoRows.Col(hit, "n");
                string pz = CoRows.Col(hit, "pz");
                throw ArapVoucherDoc.Refuse("单据 " + row.VId + " 已有一张没有回写的" + ArapProcVoucherReq.Title(ask.Style) + "凭证 " + at
                    + (pz.Length > 0 ? "（外部业务号 " + pz + "）" : "")
                    + "，可能是上次制单结果不明时留下的；请先核对，确认后用 arap/voucher/delete 或在 U8 客户端删除该凭证再制单");
            }
        }

        internal static string Side(string ledger)
        {
            return ledger == "AP" ? "应付" : "应收";
        }
    }
}
