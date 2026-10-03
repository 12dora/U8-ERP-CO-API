using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 新增与修改走 U8 的凭证导入 U8PzInsert.clsPZInsert.Transact。它自己提交，不能包在 CoTrans 里：
    // 先把能查的都查完，调用之后补 GL_CashTable.csign，再在新连接上回读。
    internal static class GlSave
    {
        const string PatchCash = "UPDATE GL_CashTable SET csign=? WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=?"
            + " AND csign IS NULL";

        public static ApiResult Create(WorkContext ctx, GlDraft draft)
        {
            object conn = ctx.Conn;
            GlKey key = new GlKey();
            key.Year = GlState.LoginYear(ctx);
            key.Sign = draft.Sign;
            string date = draft.Date.Length > 0 ? draft.Date : GlState.LoginDate(ctx);
            key.Period = PeriodOf(date, key.Year);
            int seq = GlState.SignSeq(conn, key.Sign);
            GlState.PeriodOpen(conn, key.Year, key.Period);
            GlCheck.Validate(conn, key.Year, draft);
            GlCheck.Order(conn, key, date, false);
            string maker = GlState.Operator(ctx);
            GlState.Permit(ctx, "create");
            string xml = GlXml.Envelope(true, key, draft, date, maker, null);
            GlDryRun.Voucher(ctx, key, draft, date, maker, "create");
            GlReply reply = Transact(ctx, xml, key);
            if (reply.No <= 0 || (reply.Period > 0 && reply.Period != key.Period) || (reply.Year > 0 && reply.Year != key.Year))
            {
                CoRows.Note(ctx.Item, "GlCreate reply year=" + Int(reply.Year) + " period=" + Int(reply.Period)
                    + " no=" + Int(reply.No));
                throw new BridgeException(504, "outcome_unknown", Unknown(key, "U8 没有返回凭证号"));
            }
            key.No = reply.No;
            return Finish(ctx, key, seq, draft.Lines.Count);
        }

        public static ApiResult Update(WorkContext ctx, GlKey key, GlDraft draft)
        {
            object conn = ctx.Conn;
            if (draft.Sign != key.Sign)
            {
                throw GlReq.Bad("不能修改凭证类别，head.sign 必须与 sign 相同");
            }
            int seq = GlState.SignSeq(conn, key.Sign);
            GlHead head = GlState.Need(conn, key, false);
            GlState.Writable(conn, key, head, true);
            GlState.Permit(ctx, "update");
            GuardUpdate(conn, head, GlState.Operator(ctx));
            GlCarry carry = GlCarry.Read(conn, key);
            carry.Match(draft);
            string date = draft.Date.Length > 0 ? draft.Date : head.Date;
            if (PeriodOf(date, key.Year) != key.Period)
            {
                throw GlReq.Bad("凭证日期必须在第 " + Int(key.Period) + " 期内");
            }
            GlCheck.Validate(conn, key.Year, draft);
            GlCheck.Order(conn, key, date, true);
            // 修改是整张替换；制单人沿用原凭证的（U8 修改凭证也不改 cbill）。
            string maker = head.Maker.Length > 0 ? head.Maker : GlState.Operator(ctx);
            string xml = GlXml.Envelope(false, key, draft, date, maker, carry);
            GlDryRun.Voucher(ctx, key, draft, date, maker, "update");
            GlReply reply = Transact(ctx, xml, key);
            SameKey(ctx, key, reply);
            return Finish(ctx, key, seq, draft.Lines.Count);
        }

        // 修改后 U8 回的年度、期间、凭证号（有就比）必须与请求相同。
        static void SameKey(WorkContext ctx, GlKey key, GlReply reply)
        {
            if ((reply.No > 0 && reply.No != key.No) || (reply.Year > 0 && reply.Year != key.Year)
                || (reply.Period > 0 && reply.Period != key.Period))
            {
                CoRows.Note(ctx.Item, "GlUpdate reply year=" + Int(reply.Year) + " period=" + Int(reply.Period)
                    + " no=" + Int(reply.No));
                throw new BridgeException(504, "outcome_unknown", Unknown(key, "U8 返回的年度、期间或凭证号与请求不符"));
            }
        }

        static void GuardUpdate(object conn, GlHead head, string user)
        {
            if (head.Flag == 1)
            {
                throw GlState.Refuse("凭证已作废");
            }
            if (head.Checker.Length > 0)
            {
                throw GlState.Refuse("凭证已审核，请先取消审核");
            }
            if (head.Cashier.Length > 0)
            {
                throw GlState.Refuse("凭证已出纳签字，请先取消签字");
            }
            // 重插会丢掉 iflagbank / iflagPerson。
            if (head.Marks != 0)
            {
                throw GlState.Refuse("凭证已做银行对账或往来两清");
            }
            GlState.NotReversed(conn, head);
            GlState.OwnOrAllowed(conn, head, user);
        }

        // 期间取日期的月份，只收登录年度内的日期。
        internal static int PeriodOf(string date, int year)
        {
            DateTime parsed = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (parsed.Year != year)
            {
                throw GlReq.Bad("凭证日期必须在登录年度 " + Int(year) + " 内");
            }
            return parsed.Month;
        }

        internal static GlReply Transact(WorkContext ctx, string xml, GlKey key)
        {
            object pz = ComUtil.Create("U8PzInsert.clsPZInsert");
            if (pz == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 凭证导入组件 U8PzInsert 未注册");
            }
            string text;
            try
            {
                ComUtil.Set(pz, "ToEAICon", ctx.Conn);
                // 登录 by-ref 交给自己提交的导入组件，本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                object[] args = new object[] { xml, ctx.Session.Login };
                text = Values.Text(ComUtil.CallRef(pz, "Transact", args, new int[] { 1 }));
            }
            catch (Exception ex)
            {
                // Transact 自己提交，抛错时不知道凭证写没写进去。
                CoRows.Note(ctx.Item, "GlTransact " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(key, "U8 凭证导入调用异常"));
            }
            finally
            {
                ComUtil.Final(pz);
            }
            GlReply reply = GlXml.Parse(text);
            if (reply == null)
            {
                CoRows.Note(ctx.Item, "GlTransact reply " + Clip(text));
                throw new BridgeException(504, "outcome_unknown", Unknown(key, "U8 返回内容无法解析"));
            }
            if (reply.Succeed != "0")
            {
                throw new BridgeException(409, "u8_rejected", reply.Dsc.Length > 0 ? reply.Dsc : "U8 拒绝保存凭证");
            }
            return reply;
        }

        // 已保存。导入器不填 GL_CashTable.csign（U8 自己的界面会填），在事务里补上，再在新连接上回读。
        static ApiResult Finish(WorkContext ctx, GlKey key, int seq, int lines)
        {
            CoRows.Note(ctx.Item, "凭证 " + key.Text());
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                ctx.Item.TranBefore = CoTrans.Count(conn);
                if (ctx.Item.TranBefore != "0")
                {
                    throw new BridgeException(504, "outcome_unknown", Saved(key, "导入后连接上仍有未结束的事务"));
                }
                CoTrans.Begin(conn);
                open = true;
                GlSql.Exec(conn, PatchCash, new object[] { key.Sign, key.Year, key.Period, seq, key.No });
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
                CoRows.Note(ctx.Item, "GlPatchCash " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(key, "补写现金流量凭证类别失败"));
            }
            return Readback(ctx, key, lines);
        }

        static ApiResult Readback(WorkContext ctx, GlKey key, int lines)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                GlHead head = GlState.Read(fresh, key, false);
                if (head == null || head.Lines != lines)
                {
                    throw new BridgeException(504, "outcome_unknown", Saved(key, "回读行数不符"));
                }
                return ApiResult.Ok(GlOps.Body(key));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlSaveReadback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Saved(key, "回读失败"));
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        static string Unknown(GlKey key, string why)
        {
            string at = Int(key.Year) + "年" + Int(key.Period) + "期 " + key.Sign
                + (key.No > 0 ? "-" + Int(key.No) : " 字");
            return "凭证可能已保存（" + at + "），" + why + "；请先查询再决定是否重试";
        }

        // U8 已回 succeed=0：凭证已保存，凭证号已知。
        static string Saved(GlKey key, string why)
        {
            return "凭证已保存（" + key.Text() + "），" + why + "；请先查询，不要重复提交";
        }

        static string Clip(string text)
        {
            string value = text ?? "";
            return value.Length > 200 ? value.Substring(0, 200) : value;
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
