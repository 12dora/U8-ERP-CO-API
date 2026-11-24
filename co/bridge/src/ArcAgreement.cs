using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 收付款协议（pay_agreement，表 AA_Agreement）的新增、修改、删除。U8 的 EAI 目录里没有这个对象（Dir.xml、RsXml 都没有），
    // 协议条款都在这张表的列上、没有子表，所以桥用受控 SQL 在一个事务里写：UPDLOCK, HOLDLOCK 锁住编码（挡住并发的同号新增和修改）
    // → 写 → 事务内再读一遍 → 提交 → 新连接上回读核对，不符 504 outcome_unknown（同项目档案 ArcProjectWrite）。
    // fields 的标签就是表列名（与 get 返回的一致）：cName 名称（新增必填）、cMemo 说明（不存 NULL，没给或清空都是空串）、dEDate 停用日期（空串清为 NULL），
    // 以及条款列 iLZYJ、iLZFS、iMon、iDay1–3、iLZJZ、iPLNum、iZQ、dblZQNum、iGDSZR1–3（整数）；条款列的含义以 U8 收付款协议档案界面为准，
    // 桥只校验类型，新增时没给的条款列写 0。制单日期 dtDate、制单人 cMaker 由桥按登录日期和操作员写，rowversion ufts 由库维护，都不能写。
    // 删除先查引用（ArcAgreementSql.RequireUnused）：客户、供应商档案的各类协议栏和单据上的协议栏用到的协议不能删，409。
    internal static class ArcAgreement
    {
        internal const string Name = "pay_agreement";
        internal const int CodeMax = 10;
        const int MakerMax = 60;
        const string LockSql = "SELECT cCode FROM AA_Agreement WITH (UPDLOCK, HOLDLOCK) WHERE cCode=?";

        // 条款列都是 int（dblZQNum 是 decimal(18,0)），库里可空，这里不可空：新增缺省 0，不能清为 NULL。
        static readonly ArcCol[] Cols = new ArcCol[]
        {
            ArcCol.Text("cName", 60, false), ArcCol.Memo("cMemo", 255), ArcCol.Of("iLZYJ", 'i', false),
            ArcCol.Of("iLZFS", 'i', false), ArcCol.Of("iMon", 'i', false), ArcCol.Of("iDay1", 'i', false),
            ArcCol.Of("iDay2", 'i', false), ArcCol.Of("iDay3", 'i', false), ArcCol.Of("iLZJZ", 'i', false),
            ArcCol.Of("iPLNum", 'i', false), ArcCol.Of("iZQ", 'i', false), ArcCol.Of("dblZQNum", 'n', false),
            ArcCol.Of("iGDSZR1", 'i', false), ArcCol.Of("iGDSZR2", 'i', false), ArcCol.Of("iGDSZR3", 'i', false),
            ArcCol.Of("dEDate", 'd', true)
        };

        // ArcKindRo.Append 调用：可写，get、list 仍按表列名返回（RoRead）；字段表是固定的列名表。
        internal static ArcKind Kind(ArcKind k)
        {
            k.ReadOnly = false;
            k.NameTag = "cName";
            string[] pairs = new string[Cols.Length * 2];
            for (int i = 0; i < Cols.Length; i++)
            {
                pairs[i * 2] = Cols[i].Tag;
                pairs[i * 2 + 1] = Cols[i].Tag;
            }
            k.SqlMap = ArcMap.Fixed(pairs);
            return k;
        }

        internal static bool Is(ArcKind kind)
        {
            return kind != null && kind.Name == Name;
        }

        // 登录前：不收 template；字段取值合法。
        internal static void Check(ArcReq req)
        {
            if (req.Template != null)
            {
                throw ArcReq.Bad("收付款协议不支持 template", "template");
            }
            ArcCol.CheckAll(req, Cols);
        }

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            object conn = ctx.Conn;
            // 引用栏的目录查询放在事务之前（只读目录，不占锁），逐表探测在事务里。
            List<Dictionary<string, object>> refs = req.Op == "delete" ? ArcAgreementSql.Catalog(ctx) : null;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                bool found = Rows.One(conn, LockSql, new object[] { req.Code }) != null;
                Apply(ctx, req, found, refs);
                ArcDryRun.Set(req.Kind.Name, req.Code, req.Op, ArcDryRun.Row(conn, req.Kind, req.Code));
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            CoRows.Note(ctx.Item, "AA_Agreement " + req.Op + " " + req.Code);
            bool ok = ArcPartnerRun.Fresh(ctx, req, delegate(object fresh)
            {
                Dictionary<string, string> row = ArcRead.Row(fresh, req.Kind, req.Code);
                return req.Op == "delete" ? row == null : Matches(req, row);
            });
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", ArcPartnerRun.Unknown(req));
            }
            return ArcPartnerRun.Done(req);
        }

        static void Apply(WorkContext ctx, ArcReq req, bool found, List<Dictionary<string, object>> refs)
        {
            object conn = ctx.Conn;
            if (req.Op == "create")
            {
                if (found)
                {
                    throw ArcGuard.State("档案编码已存在：" + req.Code);
                }
                Insert(ctx, req);
                return;
            }
            if (!found)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            if (req.Op == "update")
            {
                Update(conn, req);
                CheckInTran(conn, req);
                return;
            }
            ArcAgreementSql.RequireUnused(conn, refs, req.Code);
            GlSql.Exec(conn, "DELETE FROM AA_Agreement WHERE cCode=?", new object[] { req.Code });
            if (Rows.One(conn, LockSql, new object[] { req.Code }) != null)
            {
                throw new BridgeException(500, "internal", "收付款协议删除后仍能读到，已回滚");
            }
        }

        // INSERT：调用方给的列取其值，没给的取缺省（条款列 0，说明空串），其余不写（NULL）；制单日期取登录日期，制单人取本操作员。
        static void Insert(WorkContext ctx, ArcReq req)
        {
            StringBuilder cols = new StringBuilder("cCode");
            StringBuilder marks = new StringBuilder("?");
            List<object> args = new List<object>();
            args.Add(req.Code);
            for (int i = 0; i < Cols.Length; i++)
            {
                string given = req.Fields.Get(Cols[i].Tag) ?? Cols[i].Default();
                if (given == null)
                {
                    continue;
                }
                cols.Append(", ").Append(Cols[i].Tag);
                marks.Append(", ").Append(Cols[i].Expr());
                args.Add(Cols[i].Param(given));
            }
            string maker = (ctx.OperatorName ?? "").Trim();
            cols.Append(", dtDate, cMaker");
            marks.Append(", CAST(CONVERT(date, ?, 23) AS datetime), ?");
            args.Add(ctx.Item.Date);
            args.Add(maker.Length > MakerMax ? maker.Substring(0, MakerMax) : maker);
            GlSql.Exec(ctx.Conn, "INSERT INTO AA_Agreement (" + cols + ") VALUES (" + marks + ")", args.ToArray());
            CheckInTran(ctx.Conn, req);
        }

        static void Update(object conn, ArcReq req)
        {
            StringBuilder sql = new StringBuilder("UPDATE AA_Agreement SET ");
            List<object> args = new List<object>();
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                ArcCol col = ArcCol.Find(Cols, tags[i]);
                sql.Append(i > 0 ? ", " : "").Append(col.Tag).Append('=').Append(col.Expr());
                args.Add(col.Param(req.Fields.Get(tags[i])));
            }
            sql.Append(" WHERE cCode=?");
            args.Add(req.Code);
            GlSql.Exec(conn, sql.ToString(), args.ToArray());
        }

        static void CheckInTran(object conn, ArcReq req)
        {
            if (!Matches(req, ArcRead.Row(conn, req.Kind, req.Code)))
            {
                throw new BridgeException(500, "internal", "收付款协议写入后核对不一致，已回滚");
            }
        }

        // 调用方给的每一列与表里一致。
        static bool Matches(ArcReq req, Dictionary<string, string> row)
        {
            if (row == null)
            {
                return false;
            }
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string stored;
                row.TryGetValue(tags[i], out stored);
                if (!ArcCol.Find(Cols, tags[i]).Same(req.Fields.Get(tags[i]), stored))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
