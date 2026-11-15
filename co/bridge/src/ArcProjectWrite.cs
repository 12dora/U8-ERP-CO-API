using System;
using System.Collections.Generic;

namespace U8Co
{
    // 项目档案新增、修改：U8 没有单个项目的写入组件（EAI 的 IFitem 只导入大类），
    // 所以桥用受控 SQL 在一个事务里写 fitemss<大类>：先带 UPDLOCK, HOLDLOCK 锁住大类、所属分类和编码再写，
    // 提交后在新连接上回读核对。删除见 ArcProjectDel（同一套锁和回读，另查引用）。
    // 可写字段只有 name（citemname）、bclose、citemccode（所属分类，须是末级）；编码不能改。
    // fitem.crule 是项目分类的编码规则（如 22 → 分类 01、0101），不约束项目编码（账上有 8 位项目编码），桥不检查。
    // 新行的 iotherused 与 U8 客户端建的行一样留 NULL；有额外栏目或子表的大类（fitemstructure）一律 409，不写。
    internal static class ArcProjectWrite
    {
        const int MaxName = 255;
        const int MaxClassCode = 22;
        // 项目档案维护：「项目目录」AS029、「项目编辑」AS029M（按 U8 授权目录核对）。受控 SQL 不经 U8 组件，桥自己查。
        static readonly string[] Auths = new string[] { "AS029", "AS029M" };

        // 登录前：字段值格式。新增必须有 name、citemccode；不收 template。
        internal static void Check(ArcReq req)
        {
            if (req.Template != null)
            {
                throw ArcReq.Bad("项目档案不支持 template", "template");
            }
            string[] parts = ArcProject.Split(req.Code, "code");
            if (string.Equals(parts[0], "ch", StringComparison.OrdinalIgnoreCase))
            {
                throw ArcReq.Bad("项目大类 ch（存货核算）借用存货档案，不能在这里维护", "code");
            }
            if (req.Op == "create" && !req.Fields.Has("citemccode"))
            {
                throw ArcReq.Bad("缺少字段 citemccode（所属分类）", "fields.citemccode");
            }
            Text(req, "name", MaxName);
            Text(req, "citemccode", MaxClassCode);
            string close = req.Fields.Get("bclose");
            if (close != null && close != "0" && close != "1")
            {
                throw ArcReq.Bad("bclose 必须是布尔或 0 / 1", "fields.bclose");
            }
        }

        public static ApiResult Create(WorkContext ctx, ArcReq req)
        {
            Permit(ctx);
            string item = ArcProject.Split(req.Code, "code")[1];
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
                ArcProjectSql.RequireLeaf(conn, table, req.Fields.Get("citemccode"));
                if (ArcProjectSql.Lock(conn, table, item) != null)
                {
                    throw new BridgeException(409, "state_mismatch", "档案编码已存在：" + req.Code);
                }
                ArcProjectSql.Insert(conn, table, item, req.Fields);
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
            CoRows.Note(ctx.Item, "项目 insert " + table + " " + item);
            Dictionary<string, string> want = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            want["citemname"] = req.Fields.Get("name");
            want["citemccode"] = req.Fields.Get("citemccode");
            want["bclose"] = req.Fields.Get("bclose") ?? "0";
            return Readback(ctx, req, table, item, want);
        }

        public static ApiResult Update(WorkContext ctx, ArcReq req)
        {
            Permit(ctx);
            string item = ArcProject.Split(req.Code, "code")[1];
            object conn = ctx.Conn;
            string table;
            Dictionary<string, string> want;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                table = ArcProjectSql.LockClass(conn, req.Code);
                ArcProjectSql.RequireNoExtra(conn, table);
                Dictionary<string, object> row = ArcProjectSql.Lock(conn, table, item);
                if (row == null)
                {
                    throw new BridgeException(404, "not_found", "档案不存在");
                }
                if (req.Fields.Has("citemccode"))
                {
                    ArcProjectSql.RequireLeaf(conn, table, req.Fields.Get("citemccode"));
                }
                want = Merge(req, row);
                ArcProjectSql.Update(conn, table, item, req);
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
            CoRows.Note(ctx.Item, "项目 update " + table + " " + item);
            return Readback(ctx, req, table, item, want);
        }

        // 修改后应有的值：当前行叠调用方字段（列名 → 值）。bclose 为 NULL 视同 0。
        static Dictionary<string, string> Merge(ArcReq req, Dictionary<string, object> row)
        {
            Dictionary<string, string> want = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            want["citemname"] = ArcRead.Cell(row, "citemname") ?? "";
            want["citemccode"] = ArcRead.Cell(row, "citemccode") ?? "";
            want["bclose"] = ArcRead.Cell(row, "bclose") ?? "0";
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                want[req.Map.Column(tags[i])] = req.Fields.Get(tags[i]);
            }
            return want;
        }

        // 已提交；在新连接上回读。读不到、读失败或值不一致都是 504 outcome_unknown（事务已提交，先 get 核对，不要直接重发）。
        static ApiResult Readback(WorkContext ctx, ArcReq req, string table, string item, Dictionary<string, string> want)
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
                throw new BridgeException(504, "outcome_unknown", "项目 " + req.Code + " 已提交，但回读失败，结果未知");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (row == null)
            {
                throw new BridgeException(504, "outcome_unknown", "项目 " + req.Code + " 已提交，但回读查不到，结果未知");
            }
            foreach (KeyValuePair<string, string> pair in want)
            {
                string got = ArcRead.Cell(row, pair.Key) ?? (pair.Key == "bclose" ? "0" : "");
                if (got != pair.Value)
                {
                    CoRows.Note(ctx.Item, "项目回读不一致 " + pair.Key);
                    throw new BridgeException(504, "outcome_unknown", "项目 " + req.Code + " 已提交，但回读的 " + pair.Key + " 与写入不一致，结果未知");
                }
            }
            return ApiResult.Ok(ArcRead.Head(req));
        }

        internal static void Permit(WorkContext ctx)
        {
            if (!PermCheck.Of(ctx).HasAny(Auths))
            {
                throw new BridgeException(403, "no_permission", "没有项目档案维护权限");
            }
        }

        // 给了就必须非空白、不超长、不含控制字符（含换行、制表符）。
        static void Text(ArcReq req, string tag, int max)
        {
            string value = req.Fields.Get(tag);
            if (value == null)
            {
                return;
            }
            if (value.Trim().Length == 0 || value.Length > max)
            {
                throw ArcReq.Bad(tag + " 长度必须在 1 到 " + max.ToString(System.Globalization.CultureInfo.InvariantCulture) + " 之间且不能全是空白", FieldPath.Join("fields", tag));
            }
            for (int i = 0; i < value.Length; i++)
            {
                if (value[i] < ' ' || value[i] == '\x7f')
                {
                    throw ArcReq.Bad(tag + " 含有控制字符", FieldPath.Join("fields", tag));
                }
            }
        }
    }
}
