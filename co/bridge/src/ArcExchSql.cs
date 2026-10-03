using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 汇率的修改、删除（受控 SQL；EAI 的 currencyrate 实测只提供新增），以及新增前后用的查询和闸门。
    // 一个事务：UPDLOCK, HOLDLOCK 读出该编码的汇率行（固定汇率是该期间的记账、调整汇率，浮动汇率是那一天的），过闸门、
    // 按 i_id 逐行写、事务内再读一遍核对，提交后在新连接上回读。单据、凭证里存的是各自的汇率，改删汇率行不改动已有单据。
    // 修改只改给了的那种汇率，那种汇率在该编码下必须恰好一行（没有 404，请用 create；多于一行 409）；删除删掉该编码下的全部行。
    internal static class ArcExchSql
    {
        const string Cols = "SELECT i_id, itype, cdate, nflat FROM exch";
        const string Where = " WHERE cexch_name=? AND iYear=? AND iperiod=?";
        const string CurSql = "SELECT CONVERT(varchar(12), iotherused) AS f FROM foreigncurrency WHERE cexch_name=?";
        const string MendSql = "SELECT CONVERT(varchar(4), CONVERT(int, bflag)) AS f FROM GL_mend WHERE iyear=? AND iperiod=?";
        const string SetSql = "UPDATE exch SET nflat=CONVERT(float, ?) WHERE i_id=? AND cexch_name=? AND iYear=? AND iperiod=? AND itype=?";
        const string DelSql = "DELETE FROM exch WHERE i_id=? AND cexch_name=? AND iYear=? AND iperiod=? AND itype=?";
        const int MaxRows = 50;

        public static ApiResult Write(WorkContext ctx, ArcReq req, ExchKey key)
        {
            object conn = ctx.Conn;
            List<ExchPlan> plans = ArcExchWrite.Plans(req, key);
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                List<Dictionary<string, object>> rows = Rows.Query(conn, Select(key, 0, true), Args(key, 0), MaxRows);
                if (rows.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "档案不存在");
                }
                Gate(conn, req, key);
                if (req.Op == "delete")
                {
                    Delete(conn, key, rows);
                }
                else
                {
                    Update(conn, req, key, rows, plans);
                }
                ArcDryRun.Set(req.Kind.Name, req.Code, req.Op, Preview(rows, plans));
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            CoRows.Note(ctx.Item, "exch " + req.Op + " " + req.Code);
            return Readback(ctx, req, key, plans);
        }

        // 币种存在、不是本位币；总账该年度该期间没有结账。
        internal static void Gate(object conn, ArcReq req, ExchKey key)
        {
            Dictionary<string, object> cur = Rows.One(conn, CurSql, new object[] { key.Currency });
            if (cur == null)
            {
                throw ArcReq.Bad("币种 " + key.Currency + " 不存在", "code");
            }
            if ((ArcRead.Cell(cur, "f") ?? "").Trim() == "-1")
            {
                throw ArcGuard.State("币种 " + key.Currency + " 是本位币，没有汇率");
            }
            Dictionary<string, object> mend = Rows.One(conn, MendSql, new object[] { key.Year, key.Period });
            if (mend != null && (ArcRead.Cell(mend, "f") ?? "").Trim() == "1")
            {
                string what = req.Op == "create" ? "新增" : req.Op == "delete" ? "删除" : "修改";
                throw ArcGuard.State("总账 " + key.Year.ToString(CultureInfo.InvariantCulture) + " 年第 "
                    + key.Period.ToString(CultureInfo.InvariantCulture) + " 期已结账，不能" + what + "汇率");
            }
        }

        // 该编码下某一种汇率的行（type 为 0 时是该编码的全部行）。
        internal static List<Dictionary<string, object>> Find(object conn, ExchKey key, int type)
        {
            return Rows.Query(conn, Select(key, type, false), Args(key, type), MaxRows);
        }

        // 固定汇率的编码不带日：记账、调整汇率（itype 2、3）；浮动汇率按日：itype 1，cdate 是编码的日，
        // 日写成 yyyy-mm-dd 时也认只存日数的写法（同 ArcExch.Get）。表名、列名都是常量。
        static string Select(ExchKey key, int type, bool locked)
        {
            StringBuilder sql = new StringBuilder(Cols);
            if (locked)
            {
                sql.Append(" WITH (UPDLOCK, HOLDLOCK)");
            }
            sql.Append(Where);
            if (key.Day != null)
            {
                sql.Append(" AND itype=1 AND cdate IN (?, ?, ?)");
            }
            else
            {
                sql.Append(type == 0 ? " AND itype IN (2, 3)" : " AND itype=?");
            }
            return sql.Append(" ORDER BY itype, i_id").ToString();
        }

        static object[] Args(ExchKey key, int type)
        {
            List<object> args = new List<object>(new object[] { key.Currency, key.Year, key.Period });
            if (key.Day != null)
            {
                string[] days = Days(key.Day);
                args.AddRange(days);
            }
            else if (type != 0)
            {
                args.Add(type);
            }
            return args.ToArray();
        }

        // "2026-10-05" → { "2026-10-05", "5", "05" }；其他写法原样三份。
        internal static string[] Days(string day)
        {
            DateTime d;
            if (!DateTime.TryParseExact(day, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
            {
                return new string[] { day, day, day };
            }
            return new string[] { day, d.Day.ToString(CultureInfo.InvariantCulture), d.Day.ToString("00", CultureInfo.InvariantCulture) };
        }

        static void Update(object conn, ArcReq req, ExchKey key, List<Dictionary<string, object>> rows, List<ExchPlan> plans)
        {
            for (int i = 0; i < plans.Count; i++)
            {
                Dictionary<string, object> row = One(req, rows, plans[i].Type);
                GlSql.Exec(conn, SetSql, Key(row, key, plans[i].Value));
            }
            List<Dictionary<string, object>> after = Rows.Query(conn, Select(key, 0, true), Args(key, 0), MaxRows);
            for (int i = 0; i < plans.Count; i++)
            {
                List<Dictionary<string, object>> got = OfType(after, plans[i].Type);
                if (got.Count != 1 || !Near(ArcRead.Cell(got[0], "nflat"), plans[i].Value))
                {
                    throw new BridgeException(500, "internal", "汇率修改后核对不一致，已回滚");
                }
            }
        }

        // 修改的那种汇率在该编码下恰好一行。
        static Dictionary<string, object> One(ArcReq req, List<Dictionary<string, object>> rows, int type)
        {
            List<Dictionary<string, object>> got = OfType(rows, type);
            if (got.Count == 0)
            {
                throw new BridgeException(404, "not_found", "汇率 " + req.Code + " 没有" + ArcExchWrite.Label(type) + "，请用 create 新增");
            }
            if (got.Count > 1)
            {
                throw ArcGuard.State("汇率 " + req.Code + " 的" + ArcExchWrite.Label(type) + "有重复行，请在 U8 外币设置里处理");
            }
            return got[0];
        }

        static void Delete(object conn, ExchKey key, List<Dictionary<string, object>> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                GlSql.Exec(conn, DelSql, Key(rows[i], key, null));
            }
            if (Rows.Query(conn, Select(key, 0, true), Args(key, 0), 1).Count != 0)
            {
                throw new BridgeException(500, "internal", "汇率删除后仍能读到，已回滚");
            }
        }

        // SetSql / DelSql 的参数：（汇率,）i_id、币种、年度、期间、itype。
        static object[] Key(Dictionary<string, object> row, ExchKey key, string rate)
        {
            List<object> args = new List<object>();
            if (rate != null)
            {
                args.Add(rate);
            }
            args.Add(Int(row, "i_id"));
            args.Add(key.Currency);
            args.Add(key.Year);
            args.Add(key.Period);
            args.Add(Int(row, "itype"));
            return args.ToArray();
        }

        static int Int(Dictionary<string, object> row, string col)
        {
            int n;
            if (!int.TryParse((ArcRead.Cell(row, col) ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                throw new BridgeException(500, "internal", "汇率行的 " + col + " 读不出");
            }
            return n;
        }

        static List<Dictionary<string, object>> OfType(List<Dictionary<string, object>> rows, int type)
        {
            string want = type.ToString(CultureInfo.InvariantCulture);
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                if ((ArcRead.Cell(rows[i], "itype") ?? "").Trim() == want)
                {
                    list.Add(rows[i]);
                }
            }
            return list;
        }

        // 预演的 after：修改后的汇率（标签 → 值），删除时为 null。
        static object Preview(List<Dictionary<string, object>> rows, List<ExchPlan> plans)
        {
            if (plans.Count == 0)
            {
                return null;
            }
            Dictionary<string, object> after = new Dictionary<string, object>();
            after["rows"] = rows.Count;
            for (int i = 0; i < plans.Count; i++)
            {
                after[plans[i].Tag] = plans[i].Value;
            }
            return after;
        }

        // 新连接上回读：删除后该编码没有行；修改后给了的每种汇率恰好一行、值一致。不符 504。
        static ApiResult Readback(WorkContext ctx, ArcReq req, ExchKey key, List<ExchPlan> plans)
        {
            bool ok = ArcPartnerRun.Fresh(ctx, req, delegate(object fresh)
            {
                List<Dictionary<string, object>> rows = Find(fresh, key, 0);
                if (req.Op == "delete")
                {
                    return rows.Count == 0;
                }
                for (int i = 0; i < plans.Count; i++)
                {
                    List<Dictionary<string, object>> got = OfType(rows, plans[i].Type);
                    if (got.Count != 1 || !Near(ArcRead.Cell(got[0], "nflat"), plans[i].Value))
                    {
                        return false;
                    }
                }
                return true;
            });
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", "汇率 " + req.Code + " 已提交，但回读与写入不一致，结果未知");
            }
            return ArcGl.Done(req, req.Op == "delete" ? "delete" : "diffedit");
        }

        // EAI 新增后的回读（新连接）：没有新行返回 false（按 U8 的应答判断）；恰好一行且汇率一致返回 true，浮动汇率按实际的
        // cdate 改写响应的编码；其余（多行、汇率不符）504，带上找到的行数。
        internal static bool Created(object conn, ArcReq req, ExchKey key, ExchPlan plan)
        {
            List<Dictionary<string, object>> rows = Find(conn, key, plan.Type);
            if (rows.Count == 0)
            {
                return false;
            }
            if (rows.Count != 1 || !Near(ArcRead.Cell(rows[0], "nflat"), plan.Value))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已新增汇率 " + req.Code + "，但回读到 "
                    + rows.Count.ToString(CultureInfo.InvariantCulture) + " 行或汇率不一致，请先 get 核对，不要直接重发");
            }
            string day = (ArcRead.Cell(rows[0], "cdate") ?? "").Trim();
            if (key.Day != null && day.Length > 0)
            {
                req.Code = key.Currency + ":" + key.Year.ToString(CultureInfo.InvariantCulture) + ":"
                    + key.Period.ToString(CultureInfo.InvariantCulture) + ":" + day;
            }
            return true;
        }

        // float 列与十进制文本比较：相对误差 1e-9。
        internal static bool Near(string got, string want)
        {
            double a;
            double b;
            if (got == null || want == null
                || !double.TryParse(got.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out a)
                || !double.TryParse(want.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out b))
            {
                return false;
            }
            return Math.Abs(a - b) <= 1e-9 * Math.Max(1e-9, Math.Abs(b));
        }
    }
}
