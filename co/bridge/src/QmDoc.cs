using System;
using System.Collections.Generic;

namespace U8Co
{
    // 删除、审核、弃审前后读单据状态与下游（报检单 / 检验单）。列别名：code、verifier、verify_date（CoRows.StateOf 用）。
    internal static class QmDoc
    {
        const string InsSql = "select CINSPECTCODE as code, CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " CVENCODE, CDEPCODE, '0' as vs, '0' as sumq from QMINSPECTVOUCHER where ID=? and CVOUCHTYPE=?";
        const string InsLinesSql = "select CINVCODE, CWHCODE from QMINSPECTVOUCHERS where ID=?";
        const string ChkSql = "select CCHECKCODE as code, CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " CVENCODE, CDEPCODE, CINVCODE, CWHCODE, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs,"
            + " convert(varchar(40), isnull(FSUMQUANTITY,0)) as sumq from QMCHECKVOUCHER where ID=? and CVOUCHTYPE=?";
        // 报检单已被检验：行上累计检验数量大于 0，或有检验单挂在它上面。
        const string InsDownSql = "select count(*) from (select top 1 ID from QMINSPECTVOUCHERS where ID=?"
            + " and isnull(FSUMCHECKQTY,0)>0 union all select top 1 ID from QMCHECKVOUCHER where INSPECTID=?) x";
        // 检验单的下游：采购入库 / 产成品入库行的 iCheckIdBaks，不良品处理单的 CHECKID。
        const string ChkDownSql = "select top 1 x.kind from (select 'in' as kind from rdrecords01 where iCheckIdBaks=?"
            + " union all select 'in' as kind from rdrecords10 where iCheckIdBaks=?"
            + " union all select 'reject' as kind from QMREJECTVOUCHER where CHECKID=?) x";

        public static Dictionary<string, object> Read(object conn, QmSpec spec, int id)
        {
            return QmSql.One(conn, spec.Inspect ? InsSql : ChkSql, id, spec.VouchType);
        }

        public static Dictionary<string, object> Need(object conn, QmSpec spec, int id)
        {
            Dictionary<string, object> doc = Read(conn, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return doc;
        }

        public static bool Verified(Dictionary<string, object> doc)
        {
            return CoRows.Col(doc, "verifier").Length > 0;
        }

        // 数据权限：报检单按每一行（存货、仓库）加表头供应商、部门；检验单只有一行。
        public static List<Dictionary<string, object>> PermRows(object conn, QmSpec spec, int id,
            Dictionary<string, object> doc)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (!spec.Inspect)
            {
                rows.Add(doc);
                return rows;
            }
            List<Dictionary<string, object>> lines = QmSql.Many(conn, InsLinesSql, 1000, id);
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(lines[i], StringComparer.OrdinalIgnoreCase);
                row["CVENCODE"] = CoRows.Col(doc, "CVENCODE");
                row["CDEPCODE"] = CoRows.Col(doc, "CDEPCODE");
                rows.Add(row);
            }
            return rows;
        }

        public static void RequireNoCheck(object conn, int id, string what)
        {
            if (QmSql.Dec(QmSql.Scalar(conn, InsDownSql, id, id)) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "已有检验单，不能" + what);
            }
        }

        // 检验单删除：未提交审批（iVerifyStateNew=0）、未审核、没有入库和不良品处理。
        public static void RequireCheckFree(object conn, int id, Dictionary<string, object> doc)
        {
            if (CoRows.Col(doc, "vs") != "0" || Verified(doc))
            {
                throw new BridgeException(409, "state_mismatch", "检验单已提交审批或已审核，不能删除");
            }
            if (QmSql.Dec(CoRows.Col(doc, "sumq")) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "检验单已入库，不能删除");
            }
            string down = QmSql.Scalar(conn, ChkDownSql, id, id, id);
            if (down == "in")
            {
                throw new BridgeException(409, "state_mismatch", "检验单已被入库单引用，不能删除");
            }
            if (down == "reject")
            {
                throw new BridgeException(409, "state_mismatch", "检验单已生成不良品处理单，不能删除");
            }
        }

        // 新连接上重读；读不到（含异常）返回 null，由调用方按结果未知处理。
        public static Dictionary<string, object> Fresh(WorkContext ctx, QmSpec spec, int id, out bool read)
        {
            read = false;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> doc = Read(conn, spec, id);
                read = true;
                return doc;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmDoc " + ex.Message);
                return null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
