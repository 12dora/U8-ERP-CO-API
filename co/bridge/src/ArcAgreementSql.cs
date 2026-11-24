using System.Collections.Generic;

namespace U8Co
{
    // 收付款协议删除前的引用检查。引用表不写死：从账套库的目录（sys.tables / sys.columns）找出带协议栏的 dbo 用户表，
    // 逐表按协议编码探一行，有就 409。协议栏：客户的销售、出口、其他、合同、进口代理协议 cCusSAProtocol、cCusExProtocol、
    // cCusOtherProtocol、cCusCMProtocol、cCusImAgentProtocol，供应商的采购委外、进口、合同、其他协议 cVenPUOMProtocol、
    // cVenIMProtocol、cVenCMProtocol、cVenOtherProtocol，单据和往来明细上的收付款协议 cGatheringPlan（合同 CM_*、销售订单、发货单、
    // 销售发票、报价单、应收应付单和明细、出口单据等）、执行单上的 cGatheringPlanCode；它们在档案、对照表、申请单、采购和委外单据、
    // 入库单、发票、到货单、供应商资格审批等表里都有同名列，历史表同样算。
    // 不查：变更日志（*_Log）、导入暂存（*_import）、备份表（名字含 bak）和 U8 的临时表（UFTmpTable*、TMPUF_*）。
    // 表名、列名来自数据库目录并经 QUOTENAME，不来自请求；编码只进 ? 参数。
    // 目录查询（Catalog）在事务之前，只读目录；逐表探测（RequireUnused）在事务里，要扫数十张表，大账套上要几百毫秒到几秒，删除比其他档案写入慢。
    // 未覆盖：单据上自定义项等存了协议编码的列（调用方自己约定的）不查；排他锁只在协议行上，查完引用到提交之间别的连接新写进来的引用桥看不到。
    internal static class ArcAgreementSql
    {
        const string RefSql = "SELECT QUOTENAME(t.name) AS tbl, QUOTENAME(c.name) AS col, t.name AS title"
            + " FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id"
            + " WHERE t.is_ms_shipped=0 AND t.schema_id=SCHEMA_ID(N'dbo')"
            + " AND LOWER(c.name) IN (N'ccussaprotocol', N'ccusexprotocol', N'ccusotherprotocol', N'ccuscmprotocol', N'ccusimagentprotocol',"
            + " N'cvenpuomprotocol', N'cvenimprotocol', N'cvencmprotocol', N'cvenotherprotocol', N'cgatheringplan', N'cgatheringplancode')"
            + " AND c.system_type_id IN (167, 175, 231, 239)"
            + " AND t.name NOT LIKE N'UFTmpTable%' AND t.name NOT LIKE N'TMPUF[_]%' AND t.name NOT LIKE N'%[_]Log'"
            + " AND t.name NOT LIKE N'%[_]import' AND t.name NOT LIKE N'%bak%' ORDER BY t.name, c.name";
        const int MaxTables = 500;

        // 带协议栏的（表、列）清单。多于 MaxTables 个时看不全，不删（500，细节进审计），宁可拒绝也不漏查。
        internal static List<Dictionary<string, object>> Catalog(WorkContext ctx)
        {
            List<Dictionary<string, object>> refs = Rows.Query(ctx.Conn, RefSql, null, MaxTables + 1);
            if (refs.Count > MaxTables)
            {
                CoRows.Note(ctx.Item, "收付款协议删除：协议栏多于 " + MaxTables + " 个，无法查全引用");
                throw new BridgeException(500, "internal", "内部错误");
            }
            return refs;
        }

        internal static void RequireUnused(object conn, List<Dictionary<string, object>> refs, string code)
        {
            for (int i = 0; i < refs.Count; i++)
            {
                string sql = "SELECT TOP 1 1 AS x FROM dbo." + ArcRead.Cell(refs[i], "tbl") + " WHERE " + ArcRead.Cell(refs[i], "col") + "=?";
                if (Rows.One(conn, sql, new object[] { code }) != null)
                {
                    throw ArcGuard.State("收付款协议已被使用（" + ArcRead.Cell(refs[i], "title") + "），不能删除");
                }
            }
        }
    }
}
