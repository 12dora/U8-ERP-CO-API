using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 存货核算制单后的回写（同 U8 frmMakePz 的 IA_PZWriteBackIAsub，生产数据核对）和取消制单的清除（同 U8 删除凭证的 SQL），
    // 都由调用方包在 CoTrans 里。表名、列名都是常量；值只进参数；日期参数一律 cast(convert(date, ?, 23) as datetime)。
    // 写：存货明细账 IA_Subsidiary 的 cPZID / iPZDate / cPZtype / cPZdigest / cInvHead / cOppHead；销售出库单（发出商品）的
    // 发出商品明细 IA_EnSubsidiary（cOutHead = 发出商品科目、cOppHead = 存货科目）；采购入库、其他入库 / 出库、产成品入库、材料出库单表头 cPZID。
    // 「凭证号参照」IA_PZNo 是视图（IA_Subsidiary 连 GL_accvouch），不用写。
    internal static class IaVoucherBack
    {
        const string Day = "cast(convert(date, ?, 23) as datetime)";
        const int Chunk = 200;
        static readonly string[] HeadTables = new string[] { "01", "08", "09", "10", "11", "32" };

        // 补齐凭证来源列（同 U8 存货核算凭证）：coutsysname=IA、coutsign=存货核算、coutno_id、coutaccset、ioutyear、ioutperiod、
        // doutbilldate=制单日期、bvouchAddordele=1；每行 coutbillsign / coutid / doutdate 取合并进来的明细中最大的单据类型、单号、单据日期，
        // ccodecontrol、bvalueedit 按科目（IaVoucherLines.New），存货方、发出商品方的分录 bcodeedit=0、其余 1（同 U8）。GL_CashTable.csign 导入器不填，补上（同总账新增）。
        public static void PatchGl(object conn, IaPlan plan, string acc)
        {
            GlKey key = plan.Key;
            string sql = "update GL_accvouch set coutsysname=N'IA', coutsign=?, coutno_id=?, coutaccset=?, ioutyear=?, ioutperiod=?, "
                + "doutbilldate=" + Day + ", bvouchAddordele=1" + GlState.KeyWhere + " and isnull(ibook,0)=0";
            GlSql.Exec(conn, sql, GlSql.With(new object[] { IaVoucherSave.OutSign, plan.PzId, acc ?? "", key.Year, key.Period, plan.Date },
                GlSql.KeyArgs(key)));
            string line = "update GL_accvouch set coutbillsign=?, coutid=?, doutdate=cast(convert(date, nullif(?,N''), 23) as datetime), "
                + "ccodecontrol=?, bvalueedit=?, bcodeedit=?" + GlState.KeyWhere + " and inid=? and isnull(ibook,0)=0";
            for (int i = 0; i < plan.Src.Count; i++)
            {
                IaLineSrc src = plan.Src[i];
                object[] head = new object[] { src.BillType, src.BillCode, src.BillDate, src.Control, src.Editable ? 1 : 0, src.CodeEditable ? 1 : 0 };
                GlSql.Exec(conn, line, GlSql.With(GlSql.With(head, GlSql.KeyArgs(key)), new object[] { i + 1 }));
            }
            GlSql.Exec(conn, "update GL_CashTable set csign=? where iyear=? and iPeriod=? and iSignSeq=? and iNo_id=? and csign is null",
                new object[] { key.Sign, key.Year, key.Period, plan.Seq, key.No });
        }

        // 回写存货明细账、发出商品明细、单据表头。
        public static void Mark(object conn, IaPlan plan)
        {
            Dictionary<string, List<object>> subs = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            Dictionary<string, List<object>> ens = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            Dictionary<string, List<object>> heads = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            foreach (IaBill bill in plan.Bills)
            {
                foreach (IaRow row in bill.Rows)
                {
                    string values = row.Digest + "\u0001" + row.InHead + "\u0001" + row.OppHead;
                    Put(subs, values, row.Aid);
                    if (row.Type.En && row.En > 0)
                    {
                        Put(ens, values, row.Sid);
                    }
                }
                if (bill.Type.MarkHead)
                {
                    Put(heads, bill.Type.Table, bill.Id);
                }
            }
            object[] mark = new object[] { plan.PzId, plan.Date, plan.Key.Sign };
            Groups(conn, "update IA_Subsidiary set cPZID=?, iPZDate=" + Day + ", cPZtype=?, cPZdigest=?, cInvHead=?, cOppHead=? "
                + "where cPZID is null and AutoID in ", mark, subs);
            // 发出商品明细：cOutHead 是发出商品科目（存货明细账的对方科目），cOppHead 是存货科目。
            Groups(conn, "update IA_EnSubsidiary set cPZID=?, iPZDate=" + Day + ", cPZtype=?, cPZdigest=?, cOppHead=?, cOutHead=? "
                + "where cVouType=N'3201' and iYear=" + Int(plan.Key.Year) + " and cPZID is null and InID in ", mark, ens);
            foreach (KeyValuePair<string, List<object>> pair in heads)
            {
                if (Array.IndexOf(HeadTables, pair.Key) < 0)
                {
                    throw new BridgeException(500, "internal", "单据表无效");
                }
                Chunks(conn, "update RdRecord" + pair.Key + " set cPZID=? where isnull(cPZID,N'')=N'' and ID in ", new object[] { plan.PzId },
                    pair.Value);
            }
        }

        static void Put(Dictionary<string, List<object>> map, string key, object id)
        {
            List<object> ids;
            if (!map.TryGetValue(key, out ids))
            {
                ids = new List<object>();
                map[key] = ids;
            }
            ids.Add(id);
        }

        // 同一组值（摘要、存货科目、对方科目）的行一条语句；值按 SQL 里的列序接在 mark 后面。
        static void Groups(object conn, string sql, object[] mark, Dictionary<string, List<object>> groups)
        {
            foreach (KeyValuePair<string, List<object>> pair in groups)
            {
                string[] v = pair.Key.Split('\u0001');
                Chunks(conn, sql, GlSql.With(mark, new object[] { v[0], v[1], v[2] }), pair.Value);
            }
        }

        static void Chunks(object conn, string sql, object[] head, List<object> ids)
        {
            for (int start = 0; start < ids.Count; start += Chunk)
            {
                List<object> part = ids.GetRange(start, Math.Min(Chunk, ids.Count - start));
                GlSql.Exec(conn, sql + "(?" + new StringBuilder().Insert(0, ",?", part.Count - 1) + ")", GlSql.With(head, part.ToArray()));
            }
        }

        // 提交前核对：先查撞号（结果记在 plan.Clash），再查回写行数。不符返回原因。
        public static string Verify(object conn, IaPlan plan)
        {
            string clash = Clash(conn, plan);
            return clash != null ? clash : Mismatch(conn, plan);
        }

        // 提交前核对：存货明细账、发出商品明细带上本凭证号的行数正是本次选中的，凭证行都是本次的。不符返回原因。
        public static string Mismatch(object conn, IaPlan plan)
        {
            int subs = Count(conn, "select convert(varchar(12), count(*)) from IA_Subsidiary where cPZID=?", new object[] { plan.PzId });
            if (subs != plan.RowCount())
            {
                return "存货明细账回写了 " + Int(subs) + " 行，应为 " + Int(plan.RowCount()) + " 行（单据可能已在 U8 客户端制单）";
            }
            int ens = Count(conn, "select convert(varchar(12), count(*)) from IA_EnSubsidiary where cPZID=? and iYear=?",
                new object[] { plan.PzId, plan.Key.Year });
            if (ens != plan.EnCount())
            {
                return "发出商品明细回写了 " + Int(ens) + " 行，应为 " + Int(plan.EnCount()) + " 行";
            }
            int lines = Count(conn, "select convert(varchar(12), count(*)) from GL_accvouch" + GlState.KeyWhere
                + " and coutno_id=? and coutsysname=N'IA' and coutsign=?", GlSql.With(GlSql.KeyArgs(plan.Key), new object[] { plan.PzId, IaVoucherSave.OutSign }));
            return lines == plan.Draft.Lines.Count ? null : "凭证来源列补写了 " + Int(lines) + " 行，应为 " + Int(plan.Draft.Lines.Count) + " 行";
        }

        // 外部业务号撞号（U8 客户端取号不等桥的锁）：总账里别的凭证、存货明细账里多出来的行也用着它。撞号返回原因并记在 plan 上。
        public static string Clash(object conn, IaPlan plan)
        {
            object[] args = GlSql.With(new object[] { plan.PzId }, GlSql.KeyArgs(plan.Key));
            int foreign = Count(conn, "select convert(varchar(12), count(*)) from GL_accvouch where coutno_id=? "
                + "and not (iyear=? and iperiod=? and csign=? and ino_id=?)", args);
            int subs = Count(conn, "select convert(varchar(12), count(*)) from IA_Subsidiary where cPZID=?", new object[] { plan.PzId });
            plan.Clash = foreign != 0 || subs > plan.RowCount();
            return plan.Clash ? "外部业务号 " + plan.PzId + " 已被别的凭证或单据占用（U8 客户端可能同时在制单）" : null;
        }

        // 防重复制单：总账里已有本单的存货核算凭证（coutbillsign = 单据类型、coutid = 单号），其外部业务号却没有存货明细账、
        // 发出商品明细引用——上次制单结果不明（504）或恢复记账后留下的。拒绝并点名该凭证。
        public static void NoOrphan(object conn, IaBill bill)
        {
            const string Sql = "select top 1 isnull(g.coutno_id,N'') pz, convert(varchar(6), g.iyear) y, convert(varchar(4), g.iperiod) p, "
                + "g.csign s, convert(varchar(12), g.ino_id) n from GL_accvouch g "
                + "where (g.coutsysname=N'IA' or g.coutno_id like N'[0-9][0-9][0-9][0-9]IA%') and g.coutbillsign=? and g.coutid=? "
                + "and not exists (select 1 from IA_Subsidiary s where s.cPZID=g.coutno_id) "
                + "and not exists (select 1 from IA_EnSubsidiary e where e.cPZID=g.coutno_id)";
            Dictionary<string, object> row = Rows.One(conn, Sql, new object[] { bill.Type.Code, bill.Code });
            if (row == null)
            {
                return;
            }
            string at = CoRows.Col(row, "y") + "年" + CoRows.Col(row, "p") + "期 " + CoRows.Col(row, "s") + "-" + CoRows.Col(row, "n");
            throw IaVoucherLoad.Refuse("单据 " + bill.Label() + " 在总账里已有一张没有回写的存货核算凭证 " + at + "（外部业务号 "
                + CoRows.Col(row, "pz") + "），可能是上次制单结果不明或恢复记账后留下的；请先核对，确认后用 ia/voucher/delete 删除再制单");
        }

        // 取消制单：同 U8 删除存货核算凭证后对单据的清除。
        public static void Clear(object conn, string pzId)
        {
            object[] args = new object[] { pzId };
            GlSql.Exec(conn, "update IA_Subsidiary set cPZID=null, iPZDate=null, cPZtype=null, cPZdigest=null, cInvHead=null, cDifHead=null, "
                + "cOppHead=null where cPZID=?", args);
            GlSql.Exec(conn, "update IA_EnSubsidiary set cPZID=null, iPZDate=null, cPZtype=null, cPZdigest=null, cOppHead=null, cOutHead=null "
                + "where cPZID=? and iYear=?", new object[] { pzId, PzYear(pzId) });
            foreach (string table in HeadTables)
            {
                GlSql.Exec(conn, "update RdRecord" + table + " set cPZID=null where cPZID=?", args);
            }
        }

        // 清除后核对：总账、存货明细账、发出商品明细、单据表头都不再引用该外部业务号。
        public static int Left(object conn, string pzId)
        {
            StringBuilder sql = new StringBuilder("select convert(varchar(12), (select count(*) from GL_accvouch where coutno_id=?) "
                + "+ (select count(*) from IA_Subsidiary where cPZID=?) + (select count(*) from IA_EnSubsidiary where cPZID=? and iYear=?)");
            List<object> args = new List<object>(new object[] { pzId, pzId, pzId, PzYear(pzId) });
            foreach (string table in HeadTables)
            {
                sql.Append(" + (select count(*) from RdRecord").Append(table).Append(" where cPZID=?)");
                args.Add(pzId);
            }
            sql.Append(")");
            return Count(conn, sql.ToString(), args.ToArray());
        }

        // 外部业务号的年度（前 4 位，同凭证和存货明细账的年度）；发出商品明细按年度存，清除、核对都带上。
        internal static int PzYear(string pzId)
        {
            return int.Parse(pzId.Substring(0, 4), NumberStyles.Integer, CultureInfo.InvariantCulture);
        }

        static int Count(object conn, string sql, object[] args)
        {
            return CoRows.AsId(Rows.Scalar(conn, sql, args));
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
