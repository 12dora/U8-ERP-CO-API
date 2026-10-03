using System;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // GL_accvouch 的一行在记账前的记账标志。Book 为 null 表示 ibook 是 NULL，Poster 为 null 表示 cbook 是 NULL。
    internal sealed class GlPostRow
    {
        public int Id;
        public int Year;
        public int Period;
        public int Seq;
        public int No;
        public int? Book;
        public string Poster;
    }

    // U8 的缺陷（BalanceDao.AccVouchPost，实测）：回写记账标志的
    //   update gl_accvouch set ibook=1, cbook=N'<操作员>' from gl_accvouch INNER JOIN GL_mpostcond1 ON (iperiod, isignseq, ino_id)
    // 没带 iyear，也不限 GL_mpostcond1 的年度。多年度账套库里，别的年度期间、类别、凭证号相同的凭证会被改成本操作员记账，
    // GL_mpostcond1 里别的年度留下的记账范围（U8 记完不清）还会把这些键上本年度或别的年度未记账的凭证标成已记账
    // （科目总账不动）。桥在同一事务里：VouchPostAll 之前快照这个连接能碰到的所有非本次凭证的行，之后按快照改回，再核对。
    internal static class GlPostSnap
    {
        const int Chunk = 400;
        // UPDLOCK, HOLDLOCK：快照到改回之间别的会话（例如 U8 客户端取消记账）不能改这些行，否则改回会盖掉它的结果。
        const string SnapSql = "SELECT v.i_id, v.iyear, v.iperiod, v.isignseq, v.ino_id, v.ibook, v.cbook"
            + " FROM GL_accvouch v WITH (UPDLOCK, HOLDLOCK) WHERE EXISTS (SELECT 1 FROM GL_mpostcond1 m WHERE m.iperiod=v.iperiod AND m.isignseq=v.isignseq"
            + " AND m.ino_id=v.ino_id)";

        public static List<GlPostRow> Take(string conn, GlPostReq req)
        {
            List<GlPostRow> rows = new List<GlPostRow>();
            foreach (GlPostRow row in Read(conn))
            {
                if (!Target(req, row))
                {
                    rows.Add(row);
                }
            }
            return rows;
        }

        static List<GlPostRow> Read(string conn)
        {
            List<GlPostRow> rows = new List<GlPostRow>();
            foreach (object[] v in GlPostSql.Rows(conn, SnapSql, null))
            {
                GlPostRow row = new GlPostRow();
                row.Id = GlPostSql.Int(v[0]);
                row.Year = GlPostSql.Int(v[1]);
                row.Period = GlPostSql.Int(v[2]);
                row.Seq = GlPostSql.Int(v[3]);
                row.No = GlPostSql.Int(v[4]);
                row.Book = GlPostSql.Book(v[5]);
                row.Poster = GlPostSql.Str(v[6], true);
                rows.Add(row);
            }
            return rows;
        }

        static bool Target(GlPostReq req, GlPostRow row)
        {
            return req.Has(row.Year, row.Period, row.Seq, row.No);
        }

        // U8 把这些行一律写成 ibook=1、cbook=<操作员>；本来就是这个值的行不用改。其余按（年度、原值）分组，
        // 每组按 i_id 分批改回，受影响行数必须等于批内行数。
        public static void Restore(string conn, List<GlPostRow> snap, string poster)
        {
            Dictionary<string, List<GlPostRow>> groups = new Dictionary<string, List<GlPostRow>>(StringComparer.Ordinal);
            foreach (GlPostRow row in snap)
            {
                if (row.Book == 1 && row.Poster == poster)
                {
                    continue;
                }
                string key = row.Year.ToString(CultureInfo.InvariantCulture) + "|" + (row.Book.HasValue ? row.Book.Value.ToString(CultureInfo.InvariantCulture) : "n")
                    + "|" + (row.Poster == null ? "n" : "s" + row.Poster);
                List<GlPostRow> list;
                if (!groups.TryGetValue(key, out list))
                {
                    list = new List<GlPostRow>();
                    groups.Add(key, list);
                }
                list.Add(row);
            }
            foreach (List<GlPostRow> list in groups.Values)
            {
                for (int start = 0; start < list.Count; start += Chunk)
                {
                    Put(conn, list, start, Math.Min(Chunk, list.Count - start));
                }
            }
        }

        static void Put(string conn, List<GlPostRow> list, int start, int count)
        {
            GlPostRow first = list[start];
            List<SqlParameter> args = new List<SqlParameter>();
            args.Add(GlPostSql.P("@b", first.Book.HasValue ? (object)first.Book.Value : null));
            args.Add(GlPostSql.P("@c", first.Poster));
            args.Add(GlPostSql.P("@y", first.Year));
            StringBuilder ids = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                string name = "@i" + i.ToString(CultureInfo.InvariantCulture);
                ids.Append(i == 0 ? "" : ",").Append(name);
                args.Add(GlPostSql.P(name, list[start + i].Id));
            }
            string sql = "UPDATE GL_accvouch SET ibook=@b, cbook=@c WHERE iyear=@y AND i_id IN (" + ids.ToString() + ")";
            int changed = GlPostSql.Exec(conn, sql, args.ToArray());
            if (changed != count)
            {
                throw new BridgeException(500, "internal", "改回其他凭证的记账标志时行数不符（应 "
                    + count.ToString(CultureInfo.InvariantCulture) + " 行，实 " + changed.ToString(CultureInfo.InvariantCulture)
                    + " 行），已回滚，未记账");
            }
        }

        // 本次的凭证每一行 ibook=1、cbook=操作员；其余行与快照完全一致，行数也一致。不符就抛，整个事务回滚。
        // 快照行已加锁，不符说明 U8 组件的行为与预期不同，按桥内部错误 500 报，不当成调用方能处理的 409。
        public static void Verify(string conn, GlPostReq req, List<GlPostRow> snap)
        {
            Dictionary<int, GlPostRow> before = new Dictionary<int, GlPostRow>();
            foreach (GlPostRow row in snap)
            {
                before[row.Id] = row;
            }
            HashSet<string> posted = new HashSet<string>(StringComparer.Ordinal);
            int others = 0;
            foreach (GlPostRow row in Read(conn))
            {
                if (Target(req, row))
                {
                    Must(row.Book == 1 && row.Poster == req.Poster, "凭证的记账标志没有写上");
                    posted.Add(GlPostReq.Pair(row.Seq, row.No));
                    continue;
                }
                GlPostRow old;
                Must(before.TryGetValue(row.Id, out old) && old.Book == row.Book && old.Poster == row.Poster,
                    "其他凭证的记账标志没能改回");
                others++;
            }
            Must(others == snap.Count, "其他凭证的行数与快照不符");
            Must(posted.Count == req.Items.Count, "有凭证没有记账");
        }

        static void Must(bool ok, string what)
        {
            if (!ok)
            {
                throw new BridgeException(500, "internal", "记账后核对不符：" + what + "，已回滚，未记账");
            }
        }
    }
}
