using System;
using System.Collections.Generic;

namespace U8Co
{
    // 项目档案删除（受控 SQL，同新增、修改）：U8 没有单个项目的删除组件，桥在一个事务里删 fitemss<大类> 的一行。
    // 顺序：锁大类（fitem 行）→ 有额外栏目或子表的大类 409（同新增、修改）→ 带 XLOCK, ROWLOCK, HOLDLOCK 数出该编码的行
    // （0 行 404，多于 1 行 409）→ 查引用（ArcProjectUse，被用 409；引用表多于上限时 500，不猜）→ DELETE
    // → 同一事务里再数一次，不是 0 就回滚 500 → 提交 → 新连接上回读，还读得到是 504 outcome_unknown。
    // 剩余窗口：排他锁只挡住读写这一行的连接。U8 保存单据、凭证时只写引用表，不一定先按锁读项目行（NOLOCK 或快照读更不等锁），
    // 所以查完引用到提交之间，别的连接新写进来的引用桥看不到，项目仍会被删掉。窗口只有这一个事务的长度；
    // 引用表的逐表探查不加锁（锁 200 多张业务表代价太大）。
    // 权限与新增、修改相同（AS029 / AS029M；UFMeta 里项目目录表单 GL_frmXmML 的 Delete 按钮就是 AS029M）。
    internal static class ArcProjectDel
    {
        public static ApiResult Delete(WorkContext ctx, ArcReq req)
        {
            ArcProjectWrite.Permit(ctx);
            string[] parts = ArcProject.Split(req.Code, "code");
            string item = parts[1];
            object conn = ctx.Conn;
            string table;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                table = ArcProjectSql.LockClass(conn, req.Code);
                ArcProjectSql.RequireNoExtra(conn, table);
                int found = ArcProjectSql.Count(conn, table, item, true);
                if (found == 0)
                {
                    throw new BridgeException(404, "not_found", "档案不存在");
                }
                if (found > 1)
                {
                    throw new BridgeException(409, "state_mismatch", "项目编码 " + req.Code + " 有重复行，请在 U8 客户端处理");
                }
                ArcProjectUse.Require(ctx, table, item);
                ArcProjectSql.Delete(conn, table, item);
                if (ArcProjectSql.Count(conn, table, item, false) != 0)
                {
                    throw new BridgeException(500, "internal", "项目删除后仍能查到，已回滚");
                }
                ArcDryRun.Project(conn, req, table, item);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            CoRows.Note(ctx.Item, "项目 delete " + table + " " + item);
            Gone(ctx, req, table, item);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        // 已提交；新连接上还读得到、或回读失败，都是 504 outcome_unknown（先 get 核对，不要直接重发）。
        static void Gone(WorkContext ctx, ArcReq req, string table, string item)
        {
            Dictionary<string, object> row;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                CoTrans.LockWait(conn);
                row = ArcProjectSql.Read(conn, table, item);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "项目回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "项目 " + req.Code + " 已提交删除，但回读失败，结果未知");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (row != null)
            {
                throw new BridgeException(504, "outcome_unknown", "项目 " + req.Code + " 已提交删除，但回读仍能查到，结果未知");
            }
        }
    }

    // 删除前的引用检查。引用表不写死：从账套库的目录（sys.tables / sys.columns）找出同时有项目编码列（citemcode、citem_id、
    // citemid）和项目大类列（citem_class、citemclass）、两列都是字符型的 dbo 用户表，逐表按（大类, 编码）探一行。
    // 涉及的表很多：总账（GL_accvouch、GL_accass、GL_CashTable 等）、应收应付、销售、采购、库存、存货核算、质量、出口、
    // 服务、预算、项目对照 fitemcontrast 等，以及历史表、备份表（有引用同样拒绝）。U8 的临时表（UFTmpTable*、TMPUF_*）不查。
    // 表名、列名来自数据库目录并经 QUOTENAME，不来自请求；编码和大类只进 ? 参数。
    // 另查项目子表 fitemss<大类>sub（有就按 citemcode 探一行）。未覆盖：子表的 citemcode 是否就是父项目编码
    // （有子项目栏目的大类已被 RequireNoExtra 拦下）；其他列名的项目引用（如 cCItem_Class / cCItemCode 这类对方项目列）不查。
    internal static class ArcProjectUse
    {
        const string RefSql = "SELECT QUOTENAME(t.name) AS tbl, QUOTENAME(c.name) AS col, QUOTENAME(k.name) AS cls, t.name AS title"
            + " FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.columns k ON k.object_id=t.object_id"
            + " WHERE t.is_ms_shipped=0 AND t.schema_id=SCHEMA_ID(N'dbo')"
            + " AND LOWER(c.name) IN (N'citemcode', N'citem_id', N'citemid') AND LOWER(k.name) IN (N'citem_class', N'citemclass')"
            + " AND c.system_type_id IN (167, 175, 231, 239) AND k.system_type_id IN (167, 175, 231, 239)"
            + " AND t.name NOT LIKE N'UFTmpTable%' AND t.name NOT LIKE N'TMPUF[_]%' ORDER BY t.name, c.name";
        const string SubSql = "SELECT CASE WHEN OBJECT_ID(?, N'U') IS NULL THEN N'0' ELSE N'1' END AS s";
        const int MaxTables = 2000;

        // table 是 LockClass 返回的 fitemss<大类>，大类取表名后缀（已按 fitem 校验）。
        // 目录里的引用表多于 MaxTables 时看不全，不删（500，细节进审计），宁可拒绝也不漏查。
        internal static void Require(WorkContext ctx, string table, string item)
        {
            object conn = ctx.Conn;
            string cls = table.Substring("fitemss".Length);
            List<Dictionary<string, object>> refs = Rows.Query(conn, RefSql, null, MaxTables + 1);
            if (refs.Count > MaxTables)
            {
                CoRows.Note(ctx.Item, "项目删除：引用表多于 " + MaxTables + " 张，无法查全引用");
                throw new BridgeException(500, "internal", "内部错误");
            }
            for (int i = 0; i < refs.Count; i++)
            {
                string sql = "SELECT TOP 1 1 AS x FROM dbo." + ArcRead.Cell(refs[i], "tbl") + " WHERE "
                    + ArcRead.Cell(refs[i], "cls") + "=? AND " + ArcRead.Cell(refs[i], "col") + "=?";
                if (Rows.One(conn, sql, new object[] { cls, item }) != null)
                {
                    throw Used(ArcRead.Cell(refs[i], "title"));
                }
            }
            if (Rows.Scalar(conn, SubSql, new object[] { "dbo." + table + "sub" }) == "1"
                && Rows.One(conn, "SELECT TOP 1 1 AS x FROM " + table + "sub WHERE citemcode=?", new object[] { item }) != null)
            {
                throw Used(table + "sub");
            }
        }

        static BridgeException Used(string where)
        {
            return new BridgeException(409, "state_mismatch", "项目已被使用（" + where + "），不能删除");
        }
    }
}
