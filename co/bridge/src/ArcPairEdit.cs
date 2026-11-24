using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 自定义项档案（UserDefine）、客户存货对照（CusInvContrapose）的修改。U8 的 EAI 不提供（edit 回「…不提供修改功能！」，
    // diffedit 回「该修改的记录可能被删除！」），桥用受控 SQL 在一个事务里改非主键列：
    // UPDLOCK, HOLDLOCK 数出该编码的行（0 行 404；客户存货对照同一（客户、存货）有多条、编码不唯一时 409，请在 U8 客户端改）
    // → UPDATE 调用方给的列 → 事务内读回核对 → 提交 → 新连接上再读回核对，不符 504 outcome_unknown。
    // 自定义项档案另按 U8（CheckcAliasUnique）查代码、条码在同一自定义项下不重复，409 state_mismatch。
    // 可改的列就是 EAI 字段表里除编码两段以外的标签：自定义项的代码 alias、条码 barcode；客户存货对照的客户存货编码、名称和检验设置。
    // 两表的 pubufts 是 timestamp，UPDATE 时 SQL Server 自己推进。新增、删除仍走 EAI（ArcWrite）。
    internal static class ArcPairEdit
    {
        // 自定义项 UserDefine：cAlias nvarchar(20)、cBarCode nvarchar(30)，都可空。
        static readonly ArcCol[] DefineCols = new ArcCol[]
        {
            ArcCol.Text("alias", 20, true), ArcCol.Text("barcode", 30, true)
        };

        // 客户存货对照 CusInvContrapose：文本列和 smallint、float、bit 列（客户存货名称 ccusinvname 是新增必填的，修改也不能清空；boutinvdt 不可空）。
        static readonly ArcCol[] ContraCols = new ArcCol[]
        {
            ArcCol.Text("ccusinvcode", 40, true), ArcCol.Text("ccusinvname", 100, false),
            ArcCol.Of("iteststyle", 's', true), ArcCol.Of("idtmethod", 's', true), ArcCol.Of("fdtrate", 'f', true),
            ArcCol.Of("fdtnum", 'f', true), ArcCol.Text("cdtunit", 35, true), ArcCol.Of("idtstyle", 's', true),
            ArcCol.Of("idtlevel", 's', true), ArcCol.Text("cdtaql", 20, true), ArcCol.Of("boutinvdt", 'b', false),
            ArcCol.Text("crulecode", 20, true)
        };

        internal static bool Is(ArcKind kind)
        {
            return kind != null && (kind.Name == ArcPairWrite.Define || kind.Name == ArcPairWrite.Contra);
        }

        static ArcCol[] Cols(ArcKind kind)
        {
            return kind.Name == ArcPairWrite.Define ? DefineCols : ContraCols;
        }

        // 登录前：修改的字段必须是可改的列，取值合法。
        internal static void Check(ArcReq req)
        {
            if (req.Op == "update" && Is(req.Kind))
            {
                ArcCol.CheckAll(req, Cols(req.Kind));
            }
        }

        public static ApiResult Update(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            ArcCol[] cols = Cols(req.Kind);
            object[] key = ArcPair.KeyArgs(req.Kind, req.Code);
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Lock(conn, req, key);
                RequireUnique(conn, req, key);
                object[] args;
                string sql = Sql(req, cols, key, out args);
                GlSql.Exec(conn, sql, args);
                if (!Matches(req, cols, ArcRead.Row(conn, req.Kind, req.Code)))
                {
                    throw new BridgeException(500, "internal", "档案修改后核对不一致，已回滚");
                }
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
            CoRows.Note(ctx.Item, req.Kind.Table + " update " + req.Code);
            bool ok = ArcPartnerRun.Fresh(ctx, req, delegate(object fresh) { return Matches(req, cols, ArcRead.Row(fresh, req.Kind, req.Code)); });
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", ArcPartnerRun.Unknown(req));
            }
            return ArcPartnerRun.Done(req);
        }

        // 带锁数出该编码的行：恰好一行。
        static void Lock(object conn, ArcReq req, object[] key)
        {
            string sql = "SELECT COUNT(*) AS n FROM " + req.Kind.Table + " WITH (UPDLOCK, HOLDLOCK) WHERE " + ArcPair.KeyWhere(req.Kind);
            int found = int.Parse(Rows.Scalar(conn, sql, key) ?? "0", System.Globalization.CultureInfo.InvariantCulture);
            if (found == 0)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            if (found > 1)
            {
                throw ArcGuard.State("编码 " + req.Code + " 对应 " + found.ToString(System.Globalization.CultureInfo.InvariantCulture)
                    + " 条记录（同一组合有几条客户存货编码），请在 U8 客户端修改");
            }
        }

        // 自定义项档案：同一自定义项下，代码 alias、条码 barcode 不能与别的档案值重复（U8 的 CheckcAliasUnique）。
        // 带锁查，和后面的 UPDATE 在同一事务里，挡住并发的同值写入。
        static void RequireUnique(object conn, ArcReq req, object[] key)
        {
            if (req.Kind.Name != ArcPairWrite.Define)
            {
                return;
            }
            string[] tags = new string[] { "alias", "barcode" };
            string[] labels = new string[] { "代码", "条码" };
            for (int i = 0; i < tags.Length; i++)
            {
                string value = req.Fields.Get(tags[i]);
                if (string.IsNullOrEmpty(value))
                {
                    continue;
                }
                string other = Rows.Scalar(conn, UniqueSql(req.Map.Column(tags[i])), new object[] { key[0], value, key[1] });
                if (other != null)
                {
                    throw ArcGuard.State(labels[i] + "已被档案值 " + other.Trim() + " 使用");
                }
            }
        }

        // 同一自定义项下别的档案值是否已用这个代码（条码）；参数依次是自定义项号、值、本档案值。
        internal static string UniqueSql(string column)
        {
            return "SELECT TOP 1 cValue AS v FROM UserDefine WITH (UPDLOCK, HOLDLOCK) WHERE cID=? AND " + column + "=? AND cValue<>?";
        }

        // UPDATE <表> SET <列>=<值表达式>, … WHERE <主键>；args 是 SET 的参数加主键参数（列名只来自 RsXml 字段表）。
        static string Sql(ArcReq req, ArcCol[] cols, object[] key, out object[] args)
        {
            StringBuilder sql = new StringBuilder("UPDATE ").Append(req.Kind.Table).Append(" SET ");
            List<object> list = new List<object>();
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                ArcCol col = ArcCol.Find(cols, tags[i]);
                sql.Append(i > 0 ? ", " : "").Append(req.Map.Column(tags[i])).Append('=').Append(col.Expr());
                list.Add(col.Param(req.Fields.Get(tags[i])));
            }
            sql.Append(" WHERE ").Append(ArcPair.KeyWhere(req.Kind));
            list.AddRange(key);
            args = list.ToArray();
            return sql.ToString();
        }

        // 调用方给的每一列与表里一致。
        static bool Matches(ArcReq req, ArcCol[] cols, Dictionary<string, string> row)
        {
            if (row == null)
            {
                return false;
            }
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string stored;
                row.TryGetValue(req.Map.Column(tags[i]), out stored);
                if (!ArcCol.Find(cols, tags[i]).Same(req.Fields.Get(tags[i]), stored))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
