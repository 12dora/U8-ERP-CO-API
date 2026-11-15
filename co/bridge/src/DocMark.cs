using System;

namespace U8Co
{
    // 预演（DryRun）登记：新单主键拿到之后、CommitSeen 之前调用，预演时提交钩子按登记回读事务内的单据映像。
    // 不是预演时什么都不做，也不查库。主键拿不到（Save 没回写）时按单号在本连接（事务内）查。
    // 新单同时登记它的上游单据（DocMarkLinks：生单来源、回写累计数量的单据）。
    internal static class DocMark
    {
        public static void Created(object conn, string kindName, int id, string code)
        {
            Created(conn, Kinds.Find(kindName), id, code);
        }

        public static void Created(object conn, VoucherKind kind, int id, string code)
        {
            if (!DryRun.Active || kind == null)
            {
                return;
            }
            if (id <= 0)
            {
                id = ByCode(conn, kind, code);
            }
            if (id > 0)
            {
                DryRun.Created(kind, id);
                DocMarkLinks.Touch(conn, kind, id);
            }
        }

        // 同一张表里单号不唯一（到货单与退货单、专用与普通发票）时，由调用方给按单号找主键的查询。
        public static void Created(object conn, string kindName, int id, string idSql, object[] args)
        {
            if (!DryRun.Active)
            {
                return;
            }
            if (id <= 0)
            {
                id = Lookup(conn, idSql, args);
            }
            Created(conn, Kinds.Find(kindName), id, "");
        }

        // 修改、删除等改动已有单据时，在调用 U8 之前登记它的上游单据（回写的累计数量会变）。
        public static void Upstream(object conn, VoucherKind kind, int id)
        {
            if (!DryRun.Active)
            {
                return;
            }
            DocMarkLinks.Touch(conn, kind, id);
        }

        public static void Touched(string kindName, int id)
        {
            Touched(Kinds.Find(kindName), id);
        }

        public static void Touched(VoucherKind kind, int id)
        {
            if (!DryRun.Active || kind == null || id <= 0)
            {
                return;
            }
            DryRun.Touched(kind, id);
        }

        static int ByCode(object conn, VoucherKind kind, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0 || kind.CodeColumn == null || kind.CodeColumn.Length == 0)
            {
                return 0;
            }
            string sql = "select convert(varchar(20), " + CoRows.Ident(kind.IdColumn) + ") from "
                + CoRows.Ident(kind.HeadTable) + " where " + CoRows.Ident(kind.CodeColumn) + "=?";
            return Lookup(conn, sql, new object[] { no });
        }

        static int Lookup(object conn, string sql, object[] args)
        {
            if (sql == null || args == null)
            {
                return 0;
            }
            try
            {
                return CoRows.AsId(Rows.Scalar(conn, sql, args));
            }
            catch (Exception)
            {
                // 只影响预演回读哪几张单，不影响写入本身。
                return 0;
            }
        }
    }
}
