using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 结转凭证的保存与标记。每张凭证：U8PzInsert.Transact 保存（自己提交）→ 桥的事务里照 U8 取外部业务号（Ap_CancelNo 的 PZ / GL 行，
    // 同 GlReverse）、补标记、补 GL_CashTable.csign，提交。全部保存后在新连接上回读核对。
    // 标记照 U8 生成的结转凭证：coutsign「期间损益」/「自定义转账」、coutno_id = GL + 13 位、idoc=-1、coutsysname 为空
    // （不是 GL）、ioutyear 为空、ioutperiod = 期间、doutbilldate = 凭证日期、bvouchAddordele=0。凭证未审核、未记账。
    // 第一张保存之后出错（含第二张被 U8 拒绝）一律 504 outcome_unknown，消息列出已保存的凭证和补救办法
    // （期间损益：已保存的那张审核、记账后重新调用只补生成缺的那张，见 GlTransferPnlDone）。
    internal static class GlTransferSave
    {
        const string PatchSql = "UPDATE GL_accvouch SET coutsign=?, coutno_id=?, idoc=-1, coutsysname=NULL, ioutyear=NULL,"
            + " ioutperiod=?, doutbilldate=CONVERT(datetime, CONVERT(date, ?, 23)), bvouchAddordele=0" + GlState.KeyWhere
            + " AND ISNULL(ibook,0)=0";
        const string MarkedSql = "SELECT CONVERT(varchar(12), COUNT(*)) n FROM GL_accvouch" + GlState.KeyWhere
            + " AND coutsign=? AND coutno_id=? AND idoc=-1";
        const string PatchCash = "UPDATE GL_CashTable SET csign=? WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=?"
            + " AND csign IS NULL";
        const string LineSql = "SELECT CONVERT(varchar(12), inid) inid, ccode, CONVERT(varchar(40), ISNULL(md,0)) md,"
            + " CONVERT(varchar(40), ISNULL(mc,0)) mc, ISNULL(coutsign,N'') outsign, ISNULL(coutno_id,N'') outno,"
            + " CONVERT(varchar(12), ISNULL(idoc,0)) idoc, ISNULL(coutsysname,N'') outsys FROM GL_accvouch" + GlState.KeyWhere
            + " ORDER BY inid";

        public static void All(WorkContext ctx, GlTransferPlan plan)
        {
            DryRun.Stop(ctx, "U8PzInsert.Transact");
            // 核对模式不查结账、未记账、重复三道闸门，绝不能走到保存（GlTransfer.Run 已拒绝，这里兜底）。
            if (plan.Ask.Exclude)
            {
                throw GlReq.Bad("exclude_existing 只能和 dry_run 一起用（只核对，不生成）", "exclude_existing");
            }
            for (int i = 0; i < plan.Vouchers.Count; i++)
            {
                GlTransferVoucher v = plan.Vouchers[i];
                string xml = GlXml.Envelope(true, v.Key, v.Draft, plan.Date, plan.Maker, null);
                GlReply reply;
                try
                {
                    reply = GlSave.Transact(ctx, xml, v.Key);
                }
                catch (BridgeException ex)
                {
                    if (i == 0)
                    {
                        throw;
                    }
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "第 " + Int(i + 1) + " 张没有保存（" + ex.Message + "）"));
                }
                if (reply.No <= 0 || (reply.Period > 0 && reply.Period != v.Key.Period) || (reply.Year > 0 && reply.Year != v.Key.Year))
                {
                    CoRows.Note(ctx.Item, "GlTransfer reply year=" + Int(reply.Year) + " period=" + Int(reply.Period) + " no=" + Int(reply.No));
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "第 " + Int(i + 1) + " 张 U8 没有返回凭证号"));
                }
                v.Key.No = reply.No;
                Mark(ctx, plan, v);
            }
        }

        static void Mark(WorkContext ctx, GlTransferPlan plan, GlTransferVoucher v)
        {
            CoRows.Note(ctx.Item, plan.Ask.OutSign + "凭证 " + v.Key.Text());
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
                v.OutNo = ArapVoucherNo.Allocate(conn, "GL");
                GlSql.Exec(conn, PatchSql, GlSql.With(new object[] { plan.Ask.OutSign, v.OutNo, v.Key.Period, plan.Date },
                    GlSql.KeyArgs(v.Key)));
                GlSql.Exec(conn, PatchCash, new object[] { v.Key.Sign, v.Key.Year, v.Key.Period, v.Seq, v.Key.No });
                string n = Rows.Scalar(conn, MarkedSql, GlSql.With(GlSql.KeyArgs(v.Key), new object[] { plan.Ask.OutSign, v.OutNo }));
                if ((n ?? "").Trim() != Int(v.Draft.Lines.Count))
                {
                    throw new BridgeException(504, "outcome_unknown", Saved(plan, "补写结转标记的行数不符"));
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
                CoRows.Note(ctx.Item, "GlTransferMark " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(plan, "补写结转标记失败"));
            }
        }

        public static ApiResult Readback(WorkContext ctx, GlTransferPlan plan)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                foreach (GlTransferVoucher v in plan.Vouchers)
                {
                    string problem = Compare(plan, v, Rows.Query(fresh, LineSql, GlSql.KeyArgs(v.Key), GlTransfer.MaxLines + 1));
                    if (problem != null)
                    {
                        CoRows.Note(ctx.Item, "GlTransferReadback " + v.Key.Text() + " " + problem);
                        throw new BridgeException(504, "outcome_unknown", Saved(plan, "回读 " + v.Key.Text() + " 不符（" + problem + "）"));
                    }
                }
                return ApiResult.Ok(Body(plan));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlTransferReadback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(plan, "回读失败"));
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        // 逐行：科目、借方、贷方与计划相同，标记是本次的。不符返回说明，相符返回 null。
        internal static string Compare(GlTransferPlan plan, GlTransferVoucher v, List<Dictionary<string, object>> rows)
        {
            List<GlLine> lines = v.Draft.Lines;
            if (rows.Count != lines.Count)
            {
                return "行数 " + Int(rows.Count) + "，应为 " + Int(lines.Count);
            }
            for (int i = 0; i < rows.Count; i++)
            {
                bool same = Same(rows[i], lines[i]);
                if (!same || !Marked(rows[i], plan.Ask.OutSign, v.OutNo))
                {
                    return "第 " + Int(i + 1) + " 行" + (same ? "标记" : "金额或科目");
                }
            }
            return null;
        }

        static bool Same(Dictionary<string, object> row, GlLine line)
        {
            return GlSql.Col(row, "ccode").Equals(line.Account, StringComparison.OrdinalIgnoreCase)
                && GlSql.Money(row, "md") == line.Debit && GlSql.Money(row, "mc") == line.Credit;
        }

        static bool Marked(Dictionary<string, object> row, string outSign, string outNo)
        {
            return GlSql.Col(row, "outsign") == outSign && GlSql.Col(row, "outno") == outNo
                && GlSql.Col(row, "idoc") == "-1" && GlSql.Col(row, "outsys").Length == 0;
        }

        internal static Dictionary<string, object> Body(GlTransferPlan plan)
        {
            Dictionary<string, object> body = GlTransfer.Head(plan);
            body["ok"] = true;
            List<object> list = new List<object>();
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                Dictionary<string, object> one = GlTransfer.Brief(v);
                one["period"] = v.Key.Period;
                one["no"] = v.Key.No;
                one["lines"] = v.Draft.Lines.Count;
                one["out_no"] = v.OutNo;
                list.Add(one);
            }
            body["vouchers"] = list;
            body["count"] = list.Count;
            return body;
        }

        // 已保存（含部分保存）时的消息：列出已拿到凭证号的凭证。
        static string Saved(GlTransferPlan plan, string why)
        {
            StringBuilder done = new StringBuilder();
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                if (v.Key != null && v.Key.No > 0)
                {
                    done.Append(done.Length > 0 ? "、" : "").Append(v.Key.Text());
                }
            }
            string head = done.Length > 0 ? "结转凭证已保存（" + done + "）" : "结转凭证可能已保存（" + plan.Ask.Title() + "）";
            return head + "，" + why + "；请先用 gl/vouchers/list 查询，不要重复提交" + Remedy(plan);
        }

        // 部分保存时的补救办法。
        internal static string Remedy(GlTransferPlan plan)
        {
            int saved = 0;
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                saved += v.Key != null && v.Key.No > 0 ? 1 : 0;
            }
            if (saved == 0 || saved >= plan.Vouchers.Count)
            {
                return "";
            }
            if (plan.Ask.Pnl)
            {
                return "。补救：已保存的凭证带有期间损益标记（coutsign）时，审核、记账后再调用 gl/transfer/pnl，桥只补生成缺的那张；"
                    + "没有标记或不想补生成时，作废并删除已保存的凭证后整体重做";
            }
            return "。补救：已保存的转账序号带有自定义转账标记时，审核、记账后再调用会按「本期已生成」跳过它、生成其余序号";
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
