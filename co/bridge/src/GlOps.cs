using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 作废、取消作废、审核、弃审、出纳签字、取消签字、删除：U8 没有无界面的 COM，按 U8 凭证界面执行的 SQL 写（实测核对）。
    // 同一事务里先加锁读状态、过闸门、写，再读一遍确认；提交后在新连接上回读。
    internal static class GlOps
    {
        const string Where = GlState.KeyWhere;
        const string NotPosted = " AND ISNULL(ibook,0)=0";

        const string VoidSql = "UPDATE GL_accvouch SET iflag=1, iBG_OverFlag=NULL, iBG_ControlResult=NULL, CErrReason=NULL"
            + Where + NotPosted + " AND ISNULL(ccheck,'')='' AND ISNULL(ccashier,'')=''";
        const string UnvoidSql = "UPDATE GL_accvouch SET iflag=NULL" + Where + NotPosted + " AND iflag=1";
        // 审核顺带清掉「有错」标记（iflag=2），与 U8 一致。
        const string VerifySql = "UPDATE GL_accvouch SET ccheck=?, daudit_date=CONVERT(datetime, CONVERT(date, ?, 23)),"
            + " iflag=CASE WHEN iflag=2 THEN NULL ELSE iflag END"
            + Where + NotPosted + " AND ISNULL(ccheck,'')='' AND ISNULL(iflag,0)<>1";
        const string UnverifySql = "UPDATE GL_accvouch SET ccheck=NULL, daudit_date=NULL" + Where + NotPosted;
        // 出纳签字也清掉「有错」标记，与 U8 一致。
        const string SignSql = "UPDATE GL_accvouch SET ccashier=?, iflag=CASE WHEN iflag=2 THEN NULL ELSE iflag END"
            + Where + NotPosted
            + " AND ISNULL(ccashier,'')='' AND ISNULL(iflag,0)<>1";
        const string UnsignSql = "UPDATE GL_accvouch SET ccashier=NULL" + Where + NotPosted
            + " AND ISNULL(ccheck,'')='' AND ccashier=?";
        const string DeleteCash = "DELETE FROM GL_CashTable WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=?";
        const string DeleteRemark = "DELETE FROM GL_CodeRemark WHERE iyear=? AND iPeriod=? AND csign=? AND iNo_id=?";
        const string DeleteVouch = "DELETE FROM GL_accvouch" + Where + NotPosted + " AND iflag=1";

        public static bool Known(string op)
        {
            return op == "void" || op == "unvoid" || op == "verify" || op == "unverify"
                || op == "sign" || op == "unsign" || op == "delete";
        }

        public static ApiResult Run(WorkContext ctx, GlKey key, string op)
        {
            object conn = ctx.Conn;
            int seq = GlState.SignSeq(conn, key.Sign);
            string user = GlState.Operator(ctx);
            string date = GlState.LoginDate(ctx);
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                GlHead head = GlState.Need(conn, key, true);
                GlState.Writable(conn, key, head, op == "void" || op == "unvoid" || op == "delete");
                GlState.Permit(ctx, op);
                Guard(conn, key, head, op, user, date);
                Apply(conn, key, seq, op, user, date);
                GlHead after = GlState.Read(conn, key, true);
                if (!Reached(op, after, user))
                {
                    throw GlState.Refuse("凭证状态已变化，未写入");
                }
                Preview(key, op, after);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            return Readback(ctx, key, op, user);
        }

        static void Guard(object conn, GlKey key, GlHead head, string op, string user, string date)
        {
            switch (op)
            {
                case "void":
                    GuardVoid(conn, head, user);
                    break;
                case "unvoid":
                    Must(head.Flag == 1, "凭证未作废");
                    GlState.OwnOrAllowed(conn, head, user);
                    break;
                case "verify":
                    GuardVerify(conn, key, head, user, date);
                    break;
                case "unverify":
                    GuardUnverify(conn, head, user);
                    break;
                case "sign":
                    Must(head.Flag != 1, "凭证已作废");
                    Must(head.Cashier.Length == 0, "凭证已出纳签字");
                    Must(GlState.HasCashLine(conn, key), "凭证没有现金或银行科目，不需要出纳签字");
                    break;
                case "unsign":
                    Must(head.Cashier.Length > 0, "凭证未出纳签字");
                    Must(head.Cashier == user, "只能取消本人的出纳签字（签字人 " + head.Cashier + "）");
                    Must(head.Checker.Length == 0, "凭证已审核，请先取消审核");
                    break;
                default:
                    GuardDelete(conn, head, user);
                    break;
            }
        }

        static void GuardVoid(object conn, GlHead head, string user)
        {
            Must(head.Flag != 1, "凭证已作废");
            Must(head.Checker.Length == 0, "凭证已审核，请先取消审核");
            Must(head.Cashier.Length == 0, "凭证已出纳签字，请先取消签字");
            Must(head.Marks == 0, "凭证已做银行对账或往来两清");
            GlState.NotReversed(conn, head);
            GlState.OwnOrAllowed(conn, head, user);
        }

        static void GuardVerify(object conn, GlKey key, GlHead head, string user, string date)
        {
            Must(head.Flag != 1, "凭证已作废，不能审核");
            Must(head.Checker.Length == 0, "凭证已审核");
            Must(string.CompareOrdinal(date, head.Date) >= 0, "审核日期（登录日期）早于制单日期 " + head.Date);
            if (head.Maker == user && !GlState.Option(conn, "bAllowSameCheck", false))
            {
                throw GlState.Refuse("制单人与审核人不能是同一人");
            }
            if (head.Cashier.Length == 0 && GlState.Option(conn, "bProofSign", false) && GlState.HasCashLine(conn, key))
            {
                throw GlState.Refuse("出纳凭证须先由出纳签字");
            }
        }

        static void GuardUnverify(object conn, GlHead head, string user)
        {
            Must(head.Checker.Length > 0, "凭证未审核");
            if (head.Checker != user && !GlState.Option(conn, "bAllowUnCheckOther", false))
            {
                throw GlState.Refuse("只能取消本人审核的凭证（审核人 " + head.Checker + "）");
            }
        }

        static void GuardDelete(object conn, GlHead head, string user)
        {
            Must(head.Flag == 1, "只能删除已作废的凭证，请先作废");
            Must(head.Checker.Length == 0 && head.Cashier.Length == 0, "凭证已审核或已出纳签字");
            Must(head.Marks == 0, "凭证已做银行对账或往来两清");
            GlState.NotReversed(conn, head);
            GlState.OwnOrAllowed(conn, head, user);
        }

        static void Apply(object conn, GlKey key, int seq, string op, string user, string date)
        {
            object[] args = GlSql.KeyArgs(key);
            switch (op)
            {
                case "void":
                    GlSql.Exec(conn, VoidSql, args);
                    break;
                case "unvoid":
                    GlSql.Exec(conn, UnvoidSql, args);
                    break;
                case "verify":
                    GlSql.Exec(conn, VerifySql, GlSql.With(new object[] { user, date }, args));
                    break;
                case "unverify":
                    GlSql.Exec(conn, UnverifySql, args);
                    break;
                case "sign":
                    GlSql.Exec(conn, SignSql, GlSql.With(new object[] { user }, args));
                    break;
                case "unsign":
                    GlSql.Exec(conn, UnsignSql, GlSql.With(args, new object[] { user }));
                    break;
                default:
                    GlSql.Exec(conn, DeleteCash, new object[] { key.Year, key.Period, seq, key.No });
                    GlSql.Exec(conn, DeleteRemark, args);
                    GlSql.Exec(conn, DeleteVouch, args);
                    break;
            }
        }

        static bool Reached(string op, GlHead head, string user)
        {
            if (op == "delete")
            {
                return head == null;
            }
            if (head == null || head.Mixed)
            {
                return false;
            }
            switch (op)
            {
                case "void":
                    return head.Flag == 1;
                case "unvoid":
                    return head.Flag == 0;
                case "verify":
                    return head.Checker == user;
                case "unverify":
                    return head.Checker.Length == 0;
                case "sign":
                    return head.Cashier == user;
                default:
                    return head.Cashier.Length == 0;
            }
        }

        // 预演：提交钩子会回滚，这里先交出事务内读到的结果（rollback）。
        static void Preview(GlKey key, string op, GlHead after)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> gl = new Dictionary<string, object>();
            gl["op"] = op;
            gl["iyear"] = key.Year;
            gl["iperiod"] = key.Period;
            gl["csign"] = key.Sign;
            gl["ino_id"] = key.No;
            if (after == null)
            {
                gl["deleted"] = true;
            }
            else
            {
                gl["lines"] = after.Lines;
                gl["state"] = GlState.StateOf(after);
            }
            DryRun.Set("gl", gl);
        }

        // 已提交。回读失败或读到的不是目标状态，一律 504，不能让调用方再投一次。
        static ApiResult Readback(WorkContext ctx, GlKey key, string op, string user)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                GlHead head = GlState.Read(fresh, key, false);
                if (!Reached(op, head, user))
                {
                    throw new BridgeException(504, "outcome_unknown", Unknown(key));
                }
                Dictionary<string, object> body = Body(key);
                if (op == "delete")
                {
                    body["deleted"] = true;
                }
                else
                {
                    body["state"] = GlState.StateOf(head);
                }
                return ApiResult.Ok(body);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlReadback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(key));
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        internal static Dictionary<string, object> Body(GlKey key)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["period"] = key.Period;
            body["sign"] = key.Sign;
            body["no"] = key.No;
            return body;
        }

        static string Unknown(GlKey key)
        {
            return "已提交，但回读凭证状态失败：" + key.Text() + "；请先查询再决定是否重试";
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw GlState.Refuse(message);
            }
        }
    }
}
