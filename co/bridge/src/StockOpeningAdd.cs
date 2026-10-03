using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一次期初导入的上下文：调用前的最大主键、制单人、单据日期、数量小数位。
    internal sealed class OpeningRun
    {
        public int Last;
        public string Maker;
        public string Day;
        public int Digits;
    }

    // 期初结存单新增（stock_opening create）：U8 官方 EAI 导入 storeqc（U8Distribute.iDistribute.ProcessEx，login 按引用 {1}）。
    // USERPCO.VoucherCO.Insert("34") 无界面时一律报「在对应所需名称或序数的集合中，未找到项目」，不用。
    // 导入自行提交、不在请求连接的事务里：调用前查完（字段、数字、日期、小数位、仓库、存货、批号、货位），预演停在调用之前（validate）。
    // 每行由 U8 建成一张单据（单号 U8 自编），返回的 u8key 为空：调用前读 rdrecord34 的 MAX(ID)（已持 new:stock_opening
    // 与全局写闸门），调用后在新连接上按 ID 大于它、本操作员制单、期初、单据日期、未审核取新单，按 ID 次序与成功的行一一对应
    // （StockOpeningMatch）。部分行被 U8 拒绝时删掉本次已导入的单据再 409；对不上、不是本次导入的或删不掉一律 504 outcome_unknown，
    // 消息带 U8 原文和找到的主键。
    internal static class StockOpeningAdd
    {
        const string MaxIdSql = "SELECT CONVERT(varchar(20), ISNULL(MAX(ID), 0)) FROM rdrecord34";
        // 本次导入的期初单：ID 大于调用前的最大值、本操作员制单、期初、单据日期、未审核（参数：最大主键、制单人、单据日期）。
        const string Own = " h.ID>? AND h.cVouchType=N'34' AND h.cMaker=? AND h.bIsSTQc=1"
            + " AND h.dDate=CONVERT(datetime, CONVERT(date, ?, 23)) AND h.cHandler IS NULL";
        const string NewSql = "SELECT h.ID, h.cCode, h.cWhCode, h.cMemo, s.cInvCode, CONVERT(varchar(40), s.iQuantity) AS iQuantity,"
            + " CONVERT(varchar(40), s.iUnitCost) AS iUnitCost, CONVERT(varchar(40), s.iPrice) AS iPrice, s.cBatch, s.cPosition,"
            + " s.cFree1, s.cFree2, s.cFree3, s.cFree4, s.cFree5, s.cFree6, s.cFree7, s.cFree8, s.cFree9, s.cFree10"
            + " FROM rdrecord34 h JOIN rdrecords34 s ON s.ID=h.ID WHERE" + Own + " ORDER BY h.ID, s.AutoID";
        const string MineSql = "SELECT CONVERT(varchar(10), COUNT(*)) FROM rdrecord34 h WHERE" + Own + " AND h.ID=?";
        const int MaxFound = 401;

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines,
            string day)
        {
            List<OpeningEntry> entries = StockOpeningEai.Entries(head, lines ?? new object[0], day);
            OpeningRun run = new OpeningRun();
            run.Day = day;
            run.Digits = StockOpeningCheck.Run(ctx.Conn, entries);
            run.Maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            string xml = StockOpeningEai.Xml(entries, Guid.NewGuid().ToString("N"));
            run.Last = MaxId(ctx.Conn);
            DryRun.Set("entries", entries.Count);
            DryRun.Stop(ctx, StockOpeningEai.ProgId + "." + StockOpeningEai.Method);
            List<OpeningReply> replies = StockOpeningEai.Parse(Call(ctx, xml));
            List<Dictionary<string, object>> found = Found(ctx, run);
            if (replies == null || replies.Count != entries.Count)
            {
                // 返回认不出：新单与全部行对得上就当成功，否则结果未知。
                return Done(ctx, kind, Matched(ctx, run, entries, found, ""), day);
            }
            List<OpeningEntry> bad = Pick(entries, replies, false);
            string reasons = Reasons(bad, replies, entries.Count > 1);
            if (bad.Count > 0 && found.Count == 0)
            {
                throw new BridgeException(409, "u8_rejected", reasons);
            }
            List<Dictionary<string, object>> docs = Matched(ctx, run, Pick(entries, replies, true), found, reasons);
            if (bad.Count > 0)
            {
                throw Refused(ctx, kind, run, reasons, docs);
            }
            return Done(ctx, kind, docs, day);
        }

        static int MaxId(object conn)
        {
            string text = Rows.Scalar(conn, MaxIdSql, new object[0]);
            int id;
            if (!int.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out id) || id < 0)
            {
                throw new BridgeException(500, "internal", "读不到期初结存单的最大主键");
            }
            return id;
        }

        // 组件没注册在调用前 503。调用一旦发出，抛错也可能已经导入（导入自行提交，异常可能在提交之后才出现，
        // 如返回值封送失败），所以不直接报错：记下原因、按「返回认不出」处理，由回读决定成功或 504。
        static string Call(WorkContext ctx, string xml)
        {
            if (ctx.Session == null || ctx.Session.Login == null)
            {
                throw new BridgeException(500, "internal", "期初导入缺少 U8 登录");
            }
            object eai = EaiDistribute.Open();
            try
            {
                string raw = EaiDistribute.Process(ctx, eai, xml);
                StockCall.AddDetail(ctx, "eai=" + StockOpeningEai.Short(raw));
                return raw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "期初导入调用异常：" + ex.Message);
                return null;
            }
            finally
            {
                ComUtil.Final(eai);
            }
        }

        // 新连接上回读本次导入的单据（一张一行）。读失败时导入可能已提交：504。
        static List<Dictionary<string, object>> Found(WorkContext ctx, OpeningRun run)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return Rows.Query(conn, NewSql, new object[] { run.Last, run.Maker, run.Day }, MaxFound);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "期初导入回读失败：" + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "期初结存已提交 U8 导入，但回读失败，请先按列表核对，不要直接重发");
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static List<OpeningEntry> Pick(List<OpeningEntry> entries, List<OpeningReply> replies, bool ok)
        {
            List<OpeningEntry> list = new List<OpeningEntry>();
            for (int i = 0; i < entries.Count; i++)
            {
                if (replies[i].Ok == ok)
                {
                    list.Add(entries[i]);
                }
            }
            return list;
        }

        static List<Dictionary<string, object>> Matched(WorkContext ctx, OpeningRun run, List<OpeningEntry> entries,
            List<Dictionary<string, object>> found, string reasons)
        {
            List<Dictionary<string, object>> docs = StockOpeningMatch.Match(entries, found, run.Digits);
            if (docs == null)
            {
                string ids = StockOpeningMatch.IdList(found);
                CoRows.Note(ctx.Item, "期初导入回读不符 ids=" + ids);
                throw new BridgeException(504, "outcome_unknown", Prefix(reasons) + "U8 期初导入的结果与请求对不上（本次新单主键："
                    + (ids.Length == 0 ? "无" : ids) + "），请先核对，不要直接重发");
            }
            return docs;
        }

        static string Prefix(string reasons)
        {
            return reasons.Length == 0 ? "" : "U8 拒绝：" + reasons + "；";
        }

        // U8 拒绝了部分行：先确认每张新单都是本次导入的（Mine），有一张不是就一张都不删、504；
        // 否则删掉（同一登录走 StockCo.Delete），全部删掉报 409 u8_rejected，删不掉的 504。
        static BridgeException Refused(WorkContext ctx, VoucherKind kind, OpeningRun run, string reasons,
            List<Dictionary<string, object>> docs)
        {
            List<string> left = new List<string>();
            for (int i = 0; i < docs.Count; i++)
            {
                if (!Mine(ctx.Conn, run, (int)docs[i]["id"]))
                {
                    left.Add(IdText(docs[i]));
                }
            }
            if (left.Count == 0)
            {
                left = Drop(ctx, kind, docs);
            }
            if (left.Count > 0)
            {
                return new BridgeException(504, "outcome_unknown", Prefix(reasons) + "其余行已导入，单据 "
                    + string.Join("、", left.ToArray()) + " 未能删除，请核对后处理");
            }
            return new BridgeException(409, "u8_rejected", docs.Count > 0 ? reasons + "（其余行已导入并已删除，没有留下单据）" : reasons);
        }

        static List<string> Drop(WorkContext ctx, VoucherKind kind, List<Dictionary<string, object>> docs)
        {
            List<string> left = new List<string>();
            for (int i = 0; i < docs.Count; i++)
            {
                try
                {
                    StockCo.Delete(ctx, kind, (int)docs[i]["id"]);
                }
                catch (Exception ex)
                {
                    CoRows.Note(ctx.Item, "期初导入补偿删除失败 id=" + IdText(docs[i]) + "：" + ex.Message);
                    left.Add(IdText(docs[i]));
                }
            }
            return left;
        }

        // 补偿删除前确认是本次导入的期初单（Own 的条件），恰好一张。
        static bool Mine(object conn, OpeningRun run, int id)
        {
            string text = Rows.Scalar(conn, MineSql, new object[] { run.Last, run.Maker, run.Day, id });
            return (text ?? "").Trim() == "1";
        }

        static string IdText(Dictionary<string, object> doc)
        {
            return ((int)doc["id"]).ToString(CultureInfo.InvariantCulture);
        }

        static string Reasons(List<OpeningEntry> bad, List<OpeningReply> replies, bool numbered)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < bad.Count; i++)
            {
                string dsc = replies[bad[i].Line].Dsc;
                parts.Add(numbered ? "第 " + (bad[i].Line + 1).ToString(CultureInfo.InvariantCulture) + " 行：" + dsc : dsc);
            }
            return string.Join("；", parts.ToArray());
        }

        // 响应：docs 每行一张（id、code、line 为请求下标、wh、inv、qty），顶层 id / code 取第一张（与其他类型的新增兼容）。
        static ApiResult Done(WorkContext ctx, VoucherKind kind, List<Dictionary<string, object>> docs, string day)
        {
            CoRows.Note(ctx.Item, "期初导入 ids=" + JoinIds(docs));
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name ?? "";
            body["id"] = docs[0]["id"];
            body["code"] = docs[0]["code"];
            body["docs"] = docs;
            body["count"] = docs.Count;
            body["date"] = day;
            return ApiResult.Ok(body);
        }

        static string JoinIds(List<Dictionary<string, object>> docs)
        {
            List<string> ids = new List<string>();
            for (int i = 0; i < docs.Count; i++)
            {
                ids.Add(IdText(docs[i]));
            }
            return string.Join(",", ids.ToArray());
        }
    }
}
