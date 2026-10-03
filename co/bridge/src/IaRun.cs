using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货核算的正常单据记账 / 恢复记账（ia/post）和期末处理 / 取消期末处理（ia/period_end）。
    // U8 这几步没有可无界面调用的组件：桥在请求连接、请求事务里整批执行内嵌脚本 sql/ia/*.sql（照 U8 界面调用的存储过程，
    // 实测与 U8 自己的结果一致），参数只经 #ia_args（SqlScript）。记账人是登录操作员的姓名（cUserName），记账日期是该月最后一天。
    // 只做实测过的核算设置（按仓库核算、各仓库全月平均法、单到回冲，销售成本按发出商品或销售出库单），其余 409「接口暂不支持」。
    // 一个请求一个事务：检查 → 脚本 → 提交；提交后在新连接上回读该月的明细账行数、期末处理标记和结账标志，不符 504。
    // 预演（rollback）照样走到提交点，由提交钩子回滚。只对配置为测试账套的账套开放（IaReq.RequireTestAccount）。
    internal static class IaRun
    {
        const string PostTables = "sql/ia/post_tables.sql";
        const string PostScript = "sql/ia/post.sql";
        const string UnpostScript = "sql/ia/unpost.sql";
        const string PeriodEndScript = "sql/ia/period_end.sql";
        const string PeriodEndCancelScript = "sql/ia/period_end_cancel.sql";
        const int AccounterMax = 20;
        const string NotSupported = "接口暂不支持";

        // 核算设置：核算方式、暂估方式、销售成本核算方式、第一个不是全月平均法的仓库（没填按全月平均法，与脚本一致）。
        const string OptionSql = "SELECT"
            + " (SELECT TOP 1 cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'cValueStyle') vs,"
            + " (SELECT TOP 1 cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'cEstimate') est,"
            + " (SELECT TOP 1 ISNULL(cValue, cDefault) FROM AccInformation WHERE cSysID=N'IA' AND cName=N'bSaleType') st,"
            + " (SELECT TOP 1 cWhCode FROM Warehouse WHERE ISNULL(cWhValueStyle,N'全月平均法')<>N'全月平均法' ORDER BY cWhCode) wh";

        // 期间：存货核算启用日期、总账期间表有没有该期。
        const string MonthSql = "SELECT"
            + " (SELECT TOP 1 cValue FROM AccInformation WHERE cSysID=N'IA' AND cName=N'dIAStartDate') sd,"
            + " (SELECT COUNT(*) FROM GL_mend WHERE iyear=? AND iperiod=?) gl";
        // 50061 的消息里最多举几个存货（旧版 API、客户端会丢掉 detail）。
        const int UncostedShown = 5;

        // 提交后的回读：该月明细账行数、已期末处理的汇总行数、结账标志。
        const string FingerSql = "SELECT"
            + " (SELECT COUNT(*) FROM IA_Subsidiary WHERE iYear=? AND iMonth=?) sub,"
            + " (SELECT COUNT(*) FROM IA_Summary WHERE iYear=? AND iMonth=? AND ISNULL(iPeriod,0)=1) p1,"
            + " (SELECT TOP 1 CONVERT(int, ISNULL(bflag_IA,0)) FROM GL_mend WHERE iyear=? AND iperiod=?) flag";

        // 脚本拒绝编号 → 中文 409（{0} 是英文提示最后一个冒号后的期间或行数）。英文原文只进审计。
        static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50002] = "存货核算没有设置启用日期，无法记账；请在 U8 存货核算选项里检查";
            map[50003] = "该月存货核算已结账，不能记账";
            map[50010] = "该月已做期末处理，请先取消期末处理再恢复记账";
            map[50011] = "发出商品还有关联的销售发票（或以后月份的发出商品出库）没有一并恢复，不能恢复记账；请连同销售发票一起恢复";
            map[50020] = "该月存货核算已结账，不能做期末处理";
            map[50021] = "该月还没有记账数据，请先正常单据记账（ia/post）";
            map[50030] = "该月存货核算已结账，请先取消结账再取消期末处理";
            map[50040] = "该月存货核算已结账";
            map[50041] = "上一月份（{0}）存货核算还没结账，请先结上一月份";
            map[50043] = "本月还有 {0} 行已审核、未记账的出入库单据，请先正常单据记账（ia/post）";
            map[50044] = "存货核算选项要求审核后记账：本月还有 {0} 行未审核、未记账的出入库单据，请先审核并记账";
            map[50045] = "本月还没做期末处理，请先做期末处理（ia/period_end）";
            map[50046] = "总账期间表（GL_mend）缺少该期间，请在 U8 里检查会计期间";
            map[50050] = "该月存货核算还没结账";
            map[50051] = "下一月份存货核算已结账，请先取消下一月份的结账";
            map[50052] = "总账期间表（GL_mend）缺少该期间，请在 U8 里检查会计期间";
            map[50060] = "本月有直接供应的材料出库，" + NotSupported + "；请在 U8 里记账";
            map[50061] = "有存货 U8 无法确定成本（U8 界面会要求手工输入单价），没有记账：共 {0} 个，例如 {1}；可传 on_uncosted=skip 先记其余单据";
            return map;
        }

        public static ApiResult Run(WorkContext ctx)
        {
            string path = ctx.Item.Path;
            IaAsk ask = IaReq.Parse(path, ctx.Item.Body);
            // 入队后再查一次测试账套名单（登录前已查过）。
            IaReq.RequireTestAccount(ctx.Item);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(IaReq.RuleKey(ask)));
            if (!PeriodModules.StartedSubs(ctx).Contains(IaReq.Sub))
            {
                throw Refuse("存货核算未启用");
            }
            CoRows.Note(ctx.Item, IaReq.Title(ask) + " " + IaReq.MonthKey(ask));
            Dictionary<string, object> counts = Tran(ctx, ask);
            Reread(ctx, ask, counts);
            return ApiResult.Ok(Body(ask, counts));
        }

        static Dictionary<string, object> Tran(WorkContext ctx, IaAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Check(conn, ask);
                Dictionary<string, object> counts = Script(ctx, ask);
                if (DryRun.Active)
                {
                    DryRun.Set("action", ask.ActionText());
                    DryRun.Set("counts", counts);
                    string note = Nothing(ask, counts);
                    if (note != null)
                    {
                        DryRun.Set("message", note);
                    }
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                BridgeException broken = AfterRollback(ctx.Item, true);
                if (broken != null)
                {
                    throw broken;
                }
                CoTrans.CommitSeen(conn);
                open = false;
                return counts;
            }
            catch (Exception)
            {
                BeforeRollback(ctx.Item);
                bool wasOpen = open;
                CoRows.CatchTran(conn, ctx.Item, open);
                BridgeException unknown = AfterRollback(ctx.Item, wasOpen);
                if (unknown != null)
                {
                    throw unknown;
                }
                throw;
            }
        }

        // ADO 的 BeginTrans 到第一条语句才真正开事务：刚开时 @@TRANCOUNT 可能读到 0，之后是 1。
        // 事务完好：之后的层数是 max(1, 之前)。纯函数，--selftest 用。
        // 局限：之前读到 0 时，「桥的事务一直开着」与「U8 自行提交后又开了一个事务」都读到 1，这里分不出来；
        // 之前读到 1（事务已真正开始）才能可靠发现自行提交。采购结算（PuSettleGen / PuSettleDel）同样受此限。
        internal static bool TranIntact(int before, int after)
        {
            return after == Math.Max(1, before);
        }

        // 回滚之前（以及脚本做完之后）把看门狗放宽到已执行的脚本总时长 + 回滚预留（至少 3 分钟），大回滚不会被当成卡死。
        // 存货月末结账（PeriodClose 的事务）也用。
        internal static void BeforeRollback(WorkItem item)
        {
            if (item != null && item.IaStarted)
            {
                Watchdog.Extend(IaReq.AfterScriptTicks(item));
            }
        }

        // 执行过存货核算脚本、事务开着时层数不对（U8 过程或服务器自行提交、回滚过）：写入可能已部分生效，返回 504
        // outcome_unknown，调用方抛它代替原来的结果（含 ia_timeout、409）。没执行过脚本、预演已结束（钩子已回滚）、
        // 事务没开、层数没读到时返回 null。调用方在提交前（TranAfter 已读）和 catch 里 CoRows.CatchTran 之后调用。
        // 存货月末结账（PeriodClose 的事务）也用。
        internal static BridgeException AfterRollback(WorkItem item, bool open)
        {
            if (item == null || !open || !item.IaStarted || DryRun.Finished || item.TranBefore == null || item.TranAfter == null)
            {
                return null;
            }
            int before;
            int after;
            if (int.TryParse(item.TranBefore, out before) && int.TryParse(item.TranAfter, out after) && TranIntact(before, after))
            {
                return null;
            }
            CoRows.Note(item, "存货核算事务层数 " + item.TranBefore + " → " + item.TranAfter);
            return new BridgeException(504, "outcome_unknown", "存货核算脚本自行结束了事务，结果未知；请先在 U8 里核对该月的记账和期末处理");
        }

        // 脚本之前的检查：核算设置、启用月份、总账期间表。
        static void Check(object conn, IaAsk ask)
        {
            string refusal = Unsupported(conn);
            Dictionary<string, object> row = Rows.One(conn, MonthSql, new object[] { ask.Year, ask.Period });
            refusal = refusal ?? BeforeStart(CoRows.Col(row, "sd"), ask.Year, ask.Period);
            if (refusal == null && GlSql.Int(row, "gl") == 0)
            {
                refusal = "总账期间表（GL_mend）没有 " + IaReq.MonthKey(ask) + "，请在 U8 里检查会计期间";
            }
            if (refusal != null)
            {
                throw Refuse(refusal);
            }
        }

        // 接口不支持的核算设置（查本连接）：返回中文拒绝原因（含「接口暂不支持」），支持时返回 null。存货月末结账（PeriodIa）也用。
        internal static string Unsupported(object conn)
        {
            Dictionary<string, object> row = Rows.One(conn, OptionSql, new object[0]);
            return UnsupportedReason(CoRows.Col(row, "vs"), CoRows.Col(row, "est"), CoRows.Col(row, "st"), CoRows.Col(row, "wh"));
        }

        // 同上的判断部分：空值按 U8 缺省（与脚本一致）。纯函数，--selftest 用。
        internal static string UnsupportedReason(string valueStyle, string estimate, string saleType, string badWarehouse)
        {
            string vs = Trim(valueStyle);
            if (vs.Length > 0 && vs != "按仓库核算")
            {
                return "存货核算方式是「" + vs + "」，" + NotSupported + "（只支持按仓库核算）；请在 U8 里处理";
            }
            string wh = Trim(badWarehouse);
            if (wh.Length > 0)
            {
                return "仓库 " + wh + " 的计价方式不是全月平均法，" + NotSupported + "；请在 U8 里处理";
            }
            string est = Trim(estimate);
            if (est.Length > 0 && est != "单到回冲")
            {
                return "存货暂估方式是「" + est + "」，" + NotSupported + "（只支持单到回冲）；请在 U8 里处理";
            }
            string st = Trim(saleType);
            if (st.Length > 0 && st != "发出商品" && st != "销售出库单")
            {
                return "销售成本核算方式是「" + st + "」，" + NotSupported + "；请在 U8 里处理";
            }
            return null;
        }

        // 请求的月份早于存货核算启用月份时返回拒绝原因。启用日期读不出来时不拦（脚本另查 50002）。纯函数，--selftest 用。
        internal static string BeforeStart(string startDate, int year, int period)
        {
            DateTime start;
            if (!DateTime.TryParseExact(Trim(startDate), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out start))
            {
                return null;
            }
            if (year * 100 + period >= start.Year * 100 + start.Month)
            {
                return null;
            }
            return "该月早于存货核算启用月份（" + start.ToString("yyyy-MM", CultureInfo.InvariantCulture) + "）";
        }

        static Dictionary<string, object> Script(WorkContext ctx, IaAsk ask)
        {
            IaArgs args = new IaArgs();
            args.Year = ask.Year;
            args.Month = ask.Period;
            args.KeepDate = PeriodStock.LastDay(ask.Year, ask.Period);
            args.Accounter = Accounter(ctx);
            args.OnUncosted = ask.OnUncosted;
            try
            {
                return Counts(RunScripts(ctx, Scripts(ask), args, FinalKey(ask)).Counts);
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "存货核算脚本 " + PeriodGate.N(ex.Number) + " " + ex.Message);
                throw Refusal(ex);
            }
        }

        // 每个动作的最后一个脚本必须给出的计数键（缺了说明脚本没跑完，SqlScript 报 500 并由调用方回滚）。
        // 记账、恢复记账：subsidiary_month；期末处理、取消期末处理：summary_iPeriod1。
        internal static string FinalKey(IaAsk ask)
        {
            return ask.PeriodEnd ? "summary_iPeriod1" : "subsidiary_month";
        }

        // 在请求连接上逐个执行脚本（每个一批），返回最后一个的结果。每个脚本的 CommandTimeout 按请求级预算算
        // （IaReq.ScriptSeconds：min(iaCommandSeconds, 剩余 − 回滚预留)），执行期间看门狗放宽到执行 + 回滚 + 预留，做完恢复 3 分钟。
        // 预算不够再开始一个脚本时 409（调用方回滚）。最后一个脚本要求计数里有 finalKey（SqlScript 的带键重载，缺了 500）。
        // 拒绝（ScriptRefusal）原样抛出，由调用方记审计后用 Refusal 转换。存货月末结账（PeriodIa）也用。
        internal static ScriptResult RunScripts(WorkContext ctx, string[] names, IaArgs args, string finalKey)
        {
            ScriptResult last = null;
            for (int i = 0; i < names.Length; i++)
            {
                int seconds = IaReq.ScriptSeconds(ctx.Item);
                if (seconds == 0)
                {
                    throw Refuse("本次请求剩余时间不足以再执行一个存货核算脚本，已回滚；请分段处理");
                }
                Watchdog.Extend(IaReq.WatchTicks(seconds));
                ctx.Item.IaStarted = true;
                long started = DateTime.UtcNow.Ticks;
                try
                {
                    string[] one = new string[] { names[i] };
                    last = i == names.Length - 1 ? SqlScript.Run(ctx.Conn, one, args, seconds, finalKey)
                        : SqlScript.Run(ctx.Conn, one, args, seconds);
                }
                finally
                {
                    // 不回到 3 分钟：后面可能要回滚已做的全部脚本工作（through 后面的步骤拒绝时也是）。
                    ctx.Item.IaWorkTicks += DateTime.UtcNow.Ticks - started;
                    BeforeRollback(ctx.Item);
                }
            }
            return last;
        }

        // 没有要处理的单据时给调用方的说明（脚本照常返回计数，area / restore_rows 为 0），其余返回 null。纯函数，--selftest 用。
        internal static string Nothing(IaAsk ask, Dictionary<string, object> counts)
        {
            if (ask.PeriodEnd)
            {
                return null;
            }
            string key = ask.Undo ? "restore_rows" : "area";
            object value;
            if (counts == null || !counts.TryGetValue(key, out value) || !(value is long) || (long)value != 0)
            {
                return null;
            }
            return ask.Undo ? "本月没有已记账的单据，未做任何改动" : "本月没有可记账的单据，未做任何改动";
        }

        // 各动作执行的脚本（按顺序）。记账先建会话临时表；期末处理的脚本自己建表。
        internal static string[] Scripts(IaAsk ask)
        {
            if (ask.PeriodEnd)
            {
                return new string[] { ask.Undo ? PeriodEndCancelScript : PeriodEndScript };
            }
            return ask.Undo ? new string[] { UnpostScript } : new string[] { PostTables, PostScript };
        }

        // 记账人：登录操作员姓名。U8 在单据上先写 "IA_ASSUSER" + 姓名（30 个字符的列），姓名最多 20 个字符。
        internal static string Accounter(WorkContext ctx)
        {
            string name = (ctx.OperatorName ?? "").Trim();
            if (name.Length == 0)
            {
                throw new BridgeException(500, "internal", "读不到登录操作员的姓名");
            }
            if (name.Length > AccounterMax)
            {
                throw Refuse("操作员姓名超过 20 个字符，U8 存货核算不能记录记账人；请换一个操作员");
            }
            return name;
        }

        // 脚本拒绝 → 409（调用方先把英文原文记进审计）。存货月末结账（PeriodIa）也用。
        internal static BridgeException Refusal(ScriptRefusal ex)
        {
            return Refusal(ex.Number, ex.Message, ex.Rows);
        }

        // 拒绝编号 → 409。50000 是 U8 存储过程自己的中文提示（去掉过程名前缀，前缀只进审计）。
        // 50061 的消息举最多 5 个存货，另带 detail.uncosted（最多 20 个：仓库、存货、批次）和 detail.uncosted_total。
        // 纯函数，--selftest 用。
        internal static BridgeException Refusal(int number, string text, List<Dictionary<string, object>> rows)
        {
            if (number == 50000)
            {
                return new BridgeException(409, "u8_rejected", "U8 拒绝：" + AfterColon(text, true));
            }
            string pattern;
            if (!Texts.TryGetValue(number, out pattern))
            {
                return Refuse("存货核算拒绝本次操作（编号 " + PeriodGate.N(number) + "）");
            }
            if (number != 50061)
            {
                return Refuse(pattern.Replace("{0}", Tail(text)));
            }
            Dictionary<string, object> detail = Uncosted(rows);
            int total = (int)detail["uncosted_total"];
            string message = pattern.Replace("{0}", PeriodGate.N(total)).Replace("{1}", Examples((List<object>)detail["uncosted"], total));
            return Refuse(message).WithDetail(detail).WithHint("核对这些存货的入库成本后重试；或用 on_uncosted=skip 跳过它们，其余照记");
        }

        // 「仓库 06 存货 91020001 批号 X、…」，最多 UncostedShown 个，没举全加「等」；一个都没有时给「（未列出）」。
        internal static string Examples(List<object> items, int total)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < items.Count && i < UncostedShown; i++)
            {
                Dictionary<string, object> item = (Dictionary<string, object>)items[i];
                string batch = (string)item["batch"];
                parts.Add("仓库 " + item["wh"] + " 存货 " + item["inv"] + (batch.Length > 0 ? " 批号 " + batch : ""));
            }
            if (parts.Count == 0)
            {
                return "（未列出）";
            }
            return string.Join("、", parts.ToArray()) + (total > parts.Count ? " 等" : "");
        }

        // 50061 之前脚本列出的行（cWhDepCode、cInvCode、cBatchia、total）。
        internal static Dictionary<string, object> Uncosted(List<Dictionary<string, object>> rows)
        {
            List<object> items = new List<object>();
            int total = 0;
            if (rows != null)
            {
                foreach (Dictionary<string, object> row in rows)
                {
                    Dictionary<string, object> item = new Dictionary<string, object>();
                    item["wh"] = Cell(row, "cWhDepCode");
                    item["inv"] = Cell(row, "cInvCode");
                    item["batch"] = Cell(row, "cBatchia");
                    items.Add(item);
                    int n;
                    if (int.TryParse(Cell(row, "total"), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
                    {
                        total = n;
                    }
                }
            }
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["uncosted"] = items;
            detail["uncosted_total"] = Math.Max(total, items.Count);
            return detail;
        }

        // 列名按不区分大小写取（ADO 给的是脚本里写的大小写）。
        static string Cell(Dictionary<string, object> row, string name)
        {
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value == null || pair.Value is DBNull ? "" : Trim(Convert.ToString(pair.Value, CultureInfo.InvariantCulture));
                }
            }
            return "";
        }

        // 英文提示最后一个冒号后的值（期间、行数），只留数字和短横线；取不到给「若干」。
        internal static string Tail(string text)
        {
            string tail = AfterColon(text, false);
            if (tail.Length == 0)
            {
                return "若干";
            }
            foreach (char c in tail)
            {
                if (!char.IsDigit(c) && c != '-')
                {
                    return "若干";
                }
            }
            return tail;
        }

        // first：取第一个 ": " 之后（RAISERROR 的「过程名: U8 原文」，原文里可能还有冒号）；否则取最后一个之后。
        internal static string AfterColon(string text, bool first)
        {
            string t = text ?? "";
            int at = first ? t.IndexOf(": ", StringComparison.Ordinal) : t.LastIndexOf(": ", StringComparison.Ordinal);
            if (at < 0)
            {
                return first ? t.Trim() : "";
            }
            return t.Substring(at + 2).Trim();
        }

        // 脚本的诊断计数转成整数（转不了的保留原文）。纯函数，--selftest 用。
        internal static Dictionary<string, object> Counts(Dictionary<string, string> counts)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            if (counts == null)
            {
                return map;
            }
            foreach (KeyValuePair<string, string> pair in counts)
            {
                long n;
                if (long.TryParse(pair.Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
                {
                    map[pair.Key] = n;
                }
                else
                {
                    map[pair.Key] = pair.Value;
                }
            }
            return map;
        }

        // 已提交。新连接回读该月的指纹；读不出来或与脚本最后的计数不符都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, IaAsk ask, Dictionary<string, object> counts)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = Rows.One(conn, FingerSql, new object[] { ask.Year, ask.Period, ask.Year, ask.Period, ask.Year, ask.Period });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "存货核算回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读存货核算数据，请先在 U8 里核对该月的记账和期末处理");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            string drift = Drift(counts, GlSql.Int(row, "sub"), GlSql.Int(row, "p1"), GlSql.Int(row, "flag"));
            if (drift != null)
            {
                CoRows.Note(ctx.Item, "存货核算回读不符 " + drift);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交，但回读时存货核算数据与执行结果不符；请先在 U8 里核对该月的记账和期末处理，不要直接重试");
            }
        }

        // 回读与脚本计数比对：明细账行数（subsidiary_month）、已期末处理的汇总行数（summary_iPeriod1），脚本给了才比；
        // 结账标志必须仍是 0（四个动作都不改它）。一致返回 null，否则返回不符的项。纯函数，--selftest 用。
        internal static string Drift(Dictionary<string, object> counts, int sub, int periodDone, int flag)
        {
            if (flag != 0)
            {
                return "bflag_IA=" + PeriodGate.N(flag);
            }
            if (Differs(counts, "subsidiary_month", sub))
            {
                return "subsidiary_month=" + PeriodGate.N(sub);
            }
            if (Differs(counts, "summary_iPeriod1", periodDone))
            {
                return "summary_iPeriod1=" + PeriodGate.N(periodDone);
            }
            return null;
        }

        static bool Differs(Dictionary<string, object> counts, string key, int actual)
        {
            object value;
            if (counts == null || !counts.TryGetValue(key, out value) || !(value is long))
            {
                return false;
            }
            return (long)value != actual;
        }

        internal static Dictionary<string, object> Body(IaAsk ask, Dictionary<string, object> counts)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["action"] = ask.ActionText();
            body["fiscal_year"] = ask.Year;
            body["period"] = ask.Period;
            if (!ask.PeriodEnd && !ask.Undo)
            {
                body["on_uncosted"] = ask.OnUncosted;
            }
            body["counts"] = counts;
            string note = Nothing(ask, counts);
            if (note != null)
            {
                body["message"] = note;
            }
            return body;
        }

        static string Trim(string text)
        {
            return text == null ? "" : text.Trim();
        }

        static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
