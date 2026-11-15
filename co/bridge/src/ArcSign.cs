using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 凭证类别（archive=voucher_sign，表 dsign）。新增走 EAI 分发表指向的 U8PzInsert.IDsign（见 ArcGl）；
    // IDsign 的 diffedit / delete 实测回「暂不提供此项功能」，修改名称和删除走受控 SQL（ArcSignSql，一个事务，锁表、重查、回读）。
    // 闸门：类别名称 ctext 表上唯一；排序号 isignseq 不能重复（新增缺省最大加一）；修改只收名称；
    // 删除拒绝 U8 预置的 收 / 付 / 转 / 记、账套里最后一个类别、以及被凭证、凭证草稿、常用凭证、自动转账、出纳、
    // 往来明细、固定资产凭证或限制科目引用的类别（ArcGlRefs.Sign，不分年度）。限制类型 itype 一律 0（无限制），不收限制科目。
    internal static class ArcSign
    {
        // 置 true 时新增也不调 IDsign，直接走受控 SQL。
        internal const bool SqlOnly = false;
        // IDsign 没有 Transact 成员（DISP_E_UNKNOWNNAME）时新增改走受控 SQL；置 false 则 503。
        internal const bool SqlWhenNoTransact = true;
        internal const string ProgId = "U8PzInsert.IDsign";

        static readonly string[] Preset = new string[] { "收", "付", "转", "记" };
        // 经 EAI 新增时 isignseq 取最大加一在库外算，U8 自己提交、桥锁不住表：同一桥进程里的新增在这里串行，
        // 避免两个并发新增拿到同一排序号（isignseq 没有唯一索引）。U8 客户端同时新增仍可能撞号，读回后核对。
        static readonly object CreateGate = new object();
        const string NameSql = "SELECT TOP 1 csign FROM dsign WHERE ctext=? AND csign<>?";
        const string SeqSql = "SELECT TOP 1 csign FROM dsign WHERE isignseq=? AND csign<>?";
        const string MaxSql = "SELECT CONVERT(varchar(12), ISNULL(MAX(isignseq), 0) + 1) AS n FROM dsign";
        const string CountSql = "SELECT CONVERT(varchar(12), COUNT(*)) AS n FROM dsign";

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            if (req.Op != "create")
            {
                return ArcSignSql.Write(ctx, req, Bag(req));
            }
            lock (CreateGate)
            {
                return Create(ctx, req);
            }
        }

        static ApiResult Create(WorkContext ctx, ArcReq req)
        {
            object conn = ctx.Conn;
            if (ArcRead.Row(conn, req.Kind, req.Code) != null)
            {
                throw ArcGuard.State("档案编码已存在：" + req.Code);
            }
            NameFree(conn, req);
            ArcGlCall call = new ArcGlCall();
            call.ProgId = ProgId;
            call.Proc = "add";
            call.Read = ArcGl.Plain(req.Kind, req.Code);
            call.AllowMissing = SqlWhenNoTransact;
            call.Bag.Put("type_name", req.Fields.Get("type_name"));
            call.Bag.Put("order_code", Seq(conn, req));
            ApiResult done = SqlOnly ? null : ArcGl.Run(ctx, req, call);
            if (done != null)
            {
                SeqUnique(ctx, req);
                return done;
            }
            CoRows.Note(ctx.Item, "dsign 走受控 SQL");
            return ArcSignSql.Write(ctx, req, call.Bag);
        }

        static ArcBag Bag(ArcReq req)
        {
            ArcBag bag = new ArcBag();
            bag.Put("type_name", req.Fields.Get("type_name"));
            return bag;
        }

        // 新增后在新连接上核对排序号没有和别的类别重复（U8 客户端同时新增时可能撞号）；重复只记审计，类别已建好。
        static void SeqUnique(WorkContext ctx, ArcReq req)
        {
            try
            {
                Dictionary<string, string> row = ArcGl.Fresh(ctx, req, ArcGl.Plain(req.Kind, req.Code));
                string seq = ArcGl.Cell(row, "isignseq");
                int n;
                if (seq.Length > 0 && int.TryParse(seq, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
                {
                    object conn = ctx.OpenFresh();
                    try
                    {
                        if (Rows.Scalar(conn, SeqSql, new object[] { n, req.Code }) != null)
                        {
                            CoRows.Note(ctx.Item, "dsign 排序号 " + seq + " 与其他类别重复");
                        }
                    }
                    finally
                    {
                        AdoXml.Close(conn);
                    }
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "dsign 排序号核对失败 " + ex.Message);
            }
        }

        internal static void NameFree(object conn, ArcReq req)
        {
            string name = req.Fields.Get("type_name");
            string owner = name == null ? null : Rows.Scalar(conn, NameSql, new object[] { name.Trim(), req.Code });
            if (owner != null)
            {
                throw ArcGuard.State("凭证类别名称 " + name.Trim() + " 已被类别 " + owner.Trim() + " 使用");
            }
        }

        // 排序号：给了就查重，没给取最大加一。
        internal static string Seq(object conn, ArcReq req)
        {
            string given = req.Fields.Get("order_code");
            if (given == null)
            {
                return Rows.Scalar(conn, MaxSql, null) ?? "1";
            }
            string owner = Rows.Scalar(conn, SeqSql, new object[] { int.Parse(given, CultureInfo.InvariantCulture), req.Code });
            if (owner != null)
            {
                throw ArcGuard.State("凭证类别排序号 " + given + " 已被类别 " + owner.Trim() + " 使用");
            }
            return given;
        }

        internal static void Deletable(object conn, string sign)
        {
            if (Array.IndexOf(Preset, sign) >= 0)
            {
                throw ArcGuard.State("凭证类别 " + sign + " 是 U8 预置类别，不能删除");
            }
            string used = ArcGlRefs.Sign(conn, sign);
            if (used != null)
            {
                throw ArcGuard.State("凭证类别 " + sign + " 已被" + used + "使用，不能删除");
            }
            if ((Rows.Scalar(conn, CountSql, null) ?? "0").Trim() == "1")
            {
                throw ArcGuard.State("凭证类别 " + sign + " 是账套里最后一个类别，不能删除");
            }
        }
    }
}
