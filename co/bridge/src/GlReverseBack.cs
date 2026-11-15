using System;
using System.Collections.Generic;

namespace U8Co
{
    // 红字冲销保存之后：取外部业务号、补写关联列（含原凭证的 coutno_id）、提交，再在新连接上核对红字凭证逐行是原凭证取负。
    // 凭证已保存，失败一律 504。
    internal static class GlReverseBack
    {
        const string PatchSql = "UPDATE GL_accvouch SET coutno_id=?, cblueoutno_id=?, idoc=?" + GlState.KeyWhere
            + " AND ISNULL(ibook,0)=0";
        const string PatchCash = "UPDATE GL_CashTable SET csign=? WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=?"
            + " AND csign IS NULL";
        const string LinkedSql = "SELECT CONVERT(varchar(12), COUNT(*)) n FROM GL_accvouch" + GlState.KeyWhere
            + " AND coutno_id=? AND cblueoutno_id=?";
        const string CashSum = "SELECT CONVERT(varchar(12), COUNT(*)) n, CONVERT(varchar(40), ISNULL(SUM(md),0)) md,"
            + " CONVERT(varchar(40), ISNULL(SUM(mc),0)) mc FROM GL_CashTable WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=?";

        public static void Finish(WorkContext ctx, GlReversePlan plan)
        {
            GlKey red = plan.Red;
            CoRows.Note(ctx.Item, "红字凭证 " + red.Text() + " 冲销 " + plan.Source.Text());
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                ctx.Item.TranBefore = CoTrans.Count(conn);
                if (ctx.Item.TranBefore != "0")
                {
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "导入后连接上仍有未结束的事务"));
                }
                CoTrans.Begin(conn);
                open = true;
                GlReverse.Number(conn, plan);
                GlSql.Exec(conn, PatchSql, GlSql.With(new object[] { plan.RedNo, plan.BlueNo, plan.Attach }, GlSql.KeyArgs(red)));
                GlSql.Exec(conn, PatchCash, new object[] { red.Sign, red.Year, red.Period, plan.Seq, red.No });
                string n = Rows.Scalar(conn, LinkedSql, GlSql.With(GlSql.KeyArgs(red), new object[] { plan.RedNo, plan.BlueNo }));
                if ((n ?? "").Trim() != GlReverseReq.Int(plan.Draft.Lines.Count))
                {
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "补写冲销关联的行数不符"));
                }
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                if (ex is BridgeException && ((BridgeException)ex).Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "GlReversePatch " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(plan, "补写冲销关联失败"));
            }
        }

        public static ApiResult Readback(WorkContext ctx, GlReversePlan plan)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                string problem = Compare(plan, GlReverseSrc.ReadLines(fresh, plan.Red)) ?? CompareCash(fresh, plan);
                if (problem != null)
                {
                    CoRows.Note(ctx.Item, "GlReverseReadback " + problem);
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "回读红字凭证与原凭证不符（" + problem + "）"));
                }
                return ApiResult.Ok(Body(plan));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlReverseReadback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(plan, "回读失败"));
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        // 逐行：科目相同，本币、数量取负；原凭证有原币的取负；关联列是本次的外部业务号。不符时返回说明，相符返回 null。
        internal static string Compare(GlReversePlan plan, List<Dictionary<string, object>> rows)
        {
            List<Dictionary<string, object>> src = plan.SourceRows;
            if (rows.Count != src.Count)
            {
                return "行数 " + GlReverseReq.Int(rows.Count) + "，应为 " + GlReverseReq.Int(src.Count);
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (!SameLine(src[i], rows[i]) || GlSql.Col(rows[i], "blue") != plan.BlueNo || GlSql.Col(rows[i], "outno") != plan.RedNo)
                {
                    return "第 " + GlReverseReq.Int(i + 1) + " 行";
                }
            }
            return null;
        }

        static bool SameLine(Dictionary<string, object> src, Dictionary<string, object> red)
        {
            if (!GlSql.Col(src, "ccode").Equals(GlSql.Col(red, "ccode"), StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (GlSql.Money(red, "md") != -GlSql.Money(src, "md") || GlSql.Money(red, "mc") != -GlSql.Money(src, "mc"))
            {
                return false;
            }
            if (GlReverseSrc.Dec(red, "nd") != -GlReverseSrc.Dec(src, "nd") || GlReverseSrc.Dec(red, "nc") != -GlReverseSrc.Dec(src, "nc"))
            {
                return false;
            }
            return Fc(src, red, "md_f") && Fc(src, red, "mc_f");
        }

        static bool Fc(Dictionary<string, object> src, Dictionary<string, object> red, string name)
        {
            decimal want = GlSql.Money(src, name);
            return want == 0 || GlSql.Money(red, name) == -want;
        }

        static string CompareCash(object conn, GlReversePlan plan)
        {
            GlKey red = plan.Red;
            Dictionary<string, object> row = Rows.One(conn, CashSum, new object[] { red.Year, red.Period, plan.Seq, red.No });
            decimal md = 0;
            decimal mc = 0;
            foreach (Dictionary<string, object> c in plan.SourceCash)
            {
                md += GlSql.Money(c, "md");
                mc += GlSql.Money(c, "mc");
            }
            if (row == null || GlSql.Int(row, "n") != plan.SourceCash.Count || GlSql.Money(row, "md") != -md
                || GlSql.Money(row, "mc") != -mc)
            {
                return "现金流量";
            }
            return null;
        }

        internal static Dictionary<string, object> Body(GlReversePlan plan)
        {
            Dictionary<string, object> body = GlOps.Body(plan.Red);
            body["fiscal_year"] = plan.Red.Year;
            body["voucher_date"] = plan.Date;
            body["lines"] = plan.Draft.Lines.Count;
            body["out_no"] = plan.RedNo;
            Dictionary<string, object> source = new Dictionary<string, object>();
            source["fiscal_year"] = plan.Source.Year;
            source["period"] = plan.Source.Period;
            source["sign"] = plan.Source.Sign;
            source["no"] = plan.Source.No;
            source["out_no"] = plan.BlueNo;
            source["out_no_assigned"] = plan.BlueAssigned;
            body["reversal_of"] = source;
            return body;
        }

        // 预演：Prepare 的事务里拼好红字分录、预取外部业务号之后、提交之前交出计划；提交钩子随后回滚（取号一并撤销）。
        public static void Preview(GlReversePlan plan)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["op"] = GlReverseReq.Op;
            head["iyear"] = plan.Red.Year;
            head["iperiod"] = plan.Red.Period;
            head["csign"] = plan.Red.Sign;
            head["date"] = plan.Date;
            head["maker"] = plan.Maker;
            head["attachments"] = plan.Draft.Attachments;
            head["lines"] = GlDryRun.Lines(plan.Draft);
            head["lines_total"] = plan.Draft.Lines.Count;
            head["out_no"] = plan.RedNo;
            head["out_no_note"] = "预演取的外部业务号已随回滚撤销，实际冲销会重新取号";
            Dictionary<string, object> source = new Dictionary<string, object>();
            source["iyear"] = plan.Source.Year;
            source["iperiod"] = plan.Source.Period;
            source["csign"] = plan.Source.Sign;
            source["ino_id"] = plan.Source.No;
            source["out_no"] = plan.BlueNo;
            source["out_no_assigned"] = plan.BlueAssigned;
            head["reversal_of"] = source;
            DryRun.Set("voucher", head);
        }

        internal static string Unknown(GlReversePlan plan, string why)
        {
            return "红字凭证可能已保存（" + GlReverseReq.Int(plan.Red.Year) + "年" + GlReverseReq.Int(plan.Red.Period) + "期 "
                + plan.Red.Sign + " 字，冲销 " + plan.Source.Text() + "），" + why + "；请先查询再决定是否重试";
        }

        static string Saved(GlReversePlan plan, string why)
        {
            return "红字凭证已保存（" + plan.Red.Text() + "，冲销 " + plan.Source.Text() + "），" + why + "；请先查询，不要重复提交";
        }
    }
}
