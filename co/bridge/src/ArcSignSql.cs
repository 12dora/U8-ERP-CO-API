using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 凭证类别的受控 SQL：修改名称、删除一律走这里（IDsign 实测只提供新增）；新增只在 IDsign 不能用时作后备
    // （开关见 ArcSign.SqlOnly / SqlWhenNoTransact）。
    // 一个事务：先 UPDLOCK, HOLDLOCK 读整张 dsign（三五行的小表，锁住它就挡住并发的新增和排序号），
    // 在锁里重查名称、排序号和删除闸门，写一行，提交后在新连接上回读核对。
    // 新行照 U8 的现有行：itype=0（无限制）、iotherused=0、iAdjustFlag=0、cOtherName 为 NULL；i_id 是自增列。
    // 未覆盖：U8 客户端新增凭证类别时是否还写别的表（已知只有 dsign / dsigns 两张，dsigns 是限制科目，桥不写）。
    internal static class ArcSignSql
    {
        const string LockSql = "SELECT csign, ctext, isignseq FROM dsign WITH (UPDLOCK, HOLDLOCK)";
        // 编码按库的排序规则比较（不分大小写、全角半角），与 csign 主键一致。
        const string FindSql = "SELECT csign FROM dsign WITH (UPDLOCK, HOLDLOCK) WHERE csign=?";
        const string InsertSql = "INSERT INTO dsign (csign, isignseq, ctext, itype, iotherused, iAdjustFlag) VALUES (?, ?, ?, 0, 0, 0)";
        const string UpdateSql = "UPDATE dsign SET ctext=? WHERE csign=?";
        const string DeleteSql = "DELETE FROM dsign WHERE csign=?";

        // bag 里是 type_name（新增另有 order_code，在锁里重算）。
        public static ApiResult Write(WorkContext ctx, ArcReq req, ArcBag bag)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Apply(conn, req, bag, Locked(conn, req.Code));
                ArcDryRun.Set(req.Kind.Name, req.Code, req.Op, ArcDryRun.Row(conn, req.Kind, req.Code));
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                if (ArcGl.DuplicateKey(ex))
                {
                    throw ArcGuard.State("凭证类别字或名称与已有类别重复（U8 不区分大小写和全角半角）");
                }
                throw;
            }
            CoRows.Note(ctx.Item, "dsign " + req.Op + " " + req.Code);
            Dictionary<string, string> row = ArcGl.Fresh(ctx, req, ArcGl.Plain(req.Kind, req.Code));
            bool ok = req.Op == "delete" ? row == null
                : row != null && ArcGl.Cell(row, "ctext") == (bag.Get("type_name") ?? "").Trim();
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", "凭证类别 " + req.Code + " 已提交，但回读与写入不一致，结果未知");
            }
            return ArcGl.Done(req, req.Op == "delete" ? "delete" : "add");
        }

        // 锁住整张表，返回该类别字是否已存在（在 SQL 里按库的排序规则比较：Ｚ1 与 Z1、z1 算同一个）。
        static bool Locked(object conn, string sign)
        {
            Rows.Query(conn, LockSql, null, 1000);
            return Rows.One(conn, FindSql, new object[] { sign }) != null;
        }

        static void Apply(object conn, ArcReq req, ArcBag bag, bool found)
        {
            string name = (bag.Get("type_name") ?? "").Trim();
            if (req.Op == "create")
            {
                if (found)
                {
                    throw ArcGuard.State("档案编码已存在：" + req.Code);
                }
                ArcSign.NameFree(conn, req);
                int seq = int.Parse(ArcSign.Seq(conn, req), CultureInfo.InvariantCulture);
                GlSql.Exec(conn, InsertSql, new object[] { req.Code, seq, name });
                return;
            }
            if (!found)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            if (req.Op == "update")
            {
                ArcSign.NameFree(conn, req);
                GlSql.Exec(conn, UpdateSql, new object[] { name, req.Code });
                return;
            }
            ArcSign.Deletable(conn, req.Code);
            GlSql.Exec(conn, DeleteSql, new object[] { req.Code });
        }
    }
}
