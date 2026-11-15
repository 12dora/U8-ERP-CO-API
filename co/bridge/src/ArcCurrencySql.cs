using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 币种的修改、删除（受控 SQL；ICurrency 实测只提供新增）。一个事务：UPDLOCK, HOLDLOCK 读币种行（删除另锁它的汇率行），
    // 过闸门、写、提交，再在新连接上回读核对。
    // 闸门：本位币（iotherused=-1）一律 409；其他系统已使用（iotherused 非 0）409；被客户、供应商、汇率、科目、开户银行、凭证
    // 和主要单据引用（ArcGlRefs.Currency）时不能修改（409）。删除时汇率不算引用：没有其他引用的币种连同它的汇率行（exch）一起删。
    // 可改的只有折算方式 caltype → bcal、小数位数 precision → idec、最大误差 error → nerror；名称、符号不能改。
    internal static class ArcCurrencySql
    {
        const string LockSql = "SELECT cexch_name, bcal, idec, iotherused FROM foreigncurrency WITH (UPDLOCK, HOLDLOCK) WHERE cexch_name=?";
        const string ExchLockSql = "SELECT CONVERT(varchar(12), COUNT(*)) AS n FROM exch WITH (UPDLOCK, HOLDLOCK) WHERE cexch_name=?";
        const string ReadSql = "SELECT cexch_name, cexch_code, bcal, idec, CONVERT(varchar(30), CONVERT(decimal(28, 12), nerror)) AS nerror"
            + " FROM foreigncurrency WHERE cexch_name=?";
        // EAI 标签 → 列（与 CurrencyXmlRs.xml 一致）；bcal、idec 按整数写，nerror（real）按文本写，由 SQL Server 转换。
        static readonly string[] Columns = new string[] { "caltype", "bcal", "precision", "idec", "error", "nerror" };

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Dictionary<string, object> row = Rows.One(conn, LockSql, new object[] { req.Code });
                Gate(conn, req, row);
                if (req.Op == "delete")
                {
                    Rows.Scalar(conn, ExchLockSql, new object[] { req.Code });
                    GlSql.Exec(conn, "DELETE FROM exch WHERE cexch_name=?", new object[] { req.Code });
                    GlSql.Exec(conn, "DELETE FROM foreigncurrency WHERE cexch_name=?", new object[] { req.Code });
                }
                else
                {
                    Update(conn, req);
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
            CoRows.Note(ctx.Item, "foreigncurrency " + req.Op + " " + req.Code);
            return Readback(ctx, req);
        }

        // 不存在 404；本位币、其他系统已使用、被引用 409（删除时汇率不算引用）。
        static void Gate(object conn, ArcReq req, Dictionary<string, object> row)
        {
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            string flag = (ArcRead.Cell(row, "iotherused") ?? "").Trim();
            if (flag == "-1")
            {
                throw ArcGuard.State("币种 " + req.Code + " 是本位币，请在 U8 账套参数里维护");
            }
            string what = req.Op == "delete" ? "不能删除" : "不能修改";
            if (flag.Length > 0 && flag != "0")
            {
                throw ArcGuard.State("币种 " + req.Code + " 已被其他系统使用，" + what);
            }
            string used = req.Op == "delete" ? ArcGlRefs.CurrencyBesidesRates(conn, req.Code) : ArcGlRefs.Currency(conn, req.Code);
            if (used != null)
            {
                throw ArcGuard.State("币种 " + req.Code + " 已被" + used + "使用，" + what);
            }
        }

        // 只改调用方给的列；列名来自常量表。
        static void Update(object conn, ArcReq req)
        {
            StringBuilder sql = new StringBuilder("UPDATE foreigncurrency SET ");
            List<object> args = new List<object>();
            for (int i = 0; i + 1 < Columns.Length; i += 2)
            {
                string value = req.Fields.Get(Columns[i]);
                if (value == null)
                {
                    continue;
                }
                sql.Append(args.Count > 0 ? ", " : "").Append(Columns[i + 1]).Append("=?");
                args.Add(Columns[i] == "error" ? (object)value.Trim() : int.Parse(value, CultureInfo.InvariantCulture));
            }
            if (args.Count == 0)
            {
                throw ArcReq.Bad("币种只能修改 caltype、precision、error");
            }
            args.Add(req.Code);
            GlSql.Exec(conn, sql.Append(" WHERE cexch_name=?").ToString(), args.ToArray());
        }

        // 删除：读不到；修改：写的列与请求一致（Matches）。不一致 504。
        static ApiResult Readback(WorkContext ctx, ArcReq req)
        {
            Dictionary<string, string> row = Fresh(ctx, req);
            bool ok = req.Op == "delete" ? row == null : Matches(row, req.Fields);
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", "币种 " + req.Code + " 已提交，但回读与写入不一致，结果未知");
            }
            return ArcGl.Done(req, req.Op == "delete" ? "delete" : "diffedit");
        }

        // 新增（EAI）后也用：在新连接上读回，符号、折算方式、小数位数、最大误差与发出的一致，否则 504。
        internal static void CheckCreated(WorkContext ctx, ArcReq req, ArcBag sent)
        {
            if (!Matches(Fresh(ctx, req), sent))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已新增币种 " + req.Code + "，但回读的符号或精度与发出的不一致，结果未知");
            }
        }

        static Dictionary<string, string> Fresh(WorkContext ctx, ArcReq req)
        {
            return ArcGl.Fresh(ctx, req, delegate(object c)
            {
                return ArcGl.Texts(Rows.One(c, ReadSql, new object[] { req.Code }));
            });
        }

        // want 里给了的标签逐个比：code 不分大小写，caltype、precision 按文本，error 按数值（real 列，相对误差 1e-6）。
        static bool Matches(Dictionary<string, string> row, ArcBag want)
        {
            if (row == null)
            {
                return false;
            }
            string code = want.Get("code");
            if (code != null && !string.Equals(ArcGl.Cell(row, "cexch_code"), code.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            return Same(row, want.Get("caltype"), "bcal") && Same(row, want.Get("precision"), "idec") && Near(row, want.Get("error"));
        }

        static bool Same(Dictionary<string, string> row, string want, string column)
        {
            return want == null || ArcGl.Cell(row, column) == want.Trim();
        }

        static bool Near(Dictionary<string, string> row, string want)
        {
            double a;
            double b;
            if (want == null)
            {
                return true;
            }
            if (!double.TryParse(want.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out a)
                || !double.TryParse(ArcGl.Cell(row, "nerror"), NumberStyles.Float, CultureInfo.InvariantCulture, out b))
            {
                return false;
            }
            return Math.Abs(a - b) <= 1e-6 * Math.Max(1e-6, Math.Abs(a));
        }
    }
}
