using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 生产订单参照销售订单生成之后在新连接上确认（MoGenSql.Created）：调用前最大 MoDId 之后、来源是请求里这些销售订单行的
    // ATO 订单行，制单人是本操作员且建于调用前的数据库时间之后，或者是 U8 结果里报告成功的 MoDId。U8 按行各自提交：
    //   全部对上（行数、同一张订单、每行数量 / 生产部门 / 类别、单号、销售订单已下达数量正好加了本行数量）→ 成功
    //   （保存调用本身抛了异常时加 warnings）；
    //   一行都没有、U8 也没报告任何一行成功 → 有逐行错误码时 409（数据权限 403）；保存调用异常（含 IPC）时一律 504，
    //   只有确认单号已被别的订单占用（首行重号，什么都没提交）时 409；
    //   其余（部分成功、U8 报告成功却回读不到、对不上）→ 504 outcome_unknown，列出已生成的行，绝不报成功，也不重试。
    internal static class MoGenCheck
    {
        public static ApiResult Settle(WorkContext ctx, VoucherKind kind, MoGenAsk ask, string user, MoGenCall call)
        {
            List<MoGenHit> hits = Hits(call.Result, ask.Lines.Count);
            List<int> reported = Reported(hits);
            bool taken;
            List<Dictionary<string, object>> found = Read(ctx, ask, user, reported, out taken);
            if (found.Count == 0)
            {
                throw Nothing(ask, call, hits, reported, taken);
            }
            string problem = Verify(ask, found);
            if (problem != null)
            {
                throw Partial(ask, found, hits, problem);
            }
            ApiResult result = MoGenOut.Result(kind, ask, found);
            if (call.Lost != null)
            {
                MoGenOut.Warn(result, "U8 保存后续步骤异常（" + MoApi.FirstLine(call.Lost.Message)
                    + "），生产订单已全部生成，条码、销售订单时间戳可能未更新");
            }
            return result;
        }

        static List<Dictionary<string, object>> Read(WorkContext ctx, MoGenAsk ask, string user, List<int> reported, out bool taken)
        {
            object conn = null;
            taken = false;
            try
            {
                conn = ctx.OpenFresh();
                List<Dictionary<string, object>> found = MoGenSql.Created(conn, ask, user, reported);
                taken = found.Count == 0 && MoGenSql.CodeTaken(conn, ask);
                return found;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoGen 回读 " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 生产订单生成但未能回读，结果未知，不要重投");
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // BF 的结果 Hashtable 按 0.. 下标，每行一项 {Id, Cnt, MoId, MoDId, MoCode}；Cnt 可能是数字或字符串（900031）。
        internal static List<MoGenHit> Hits(Hashtable result, int count)
        {
            List<MoGenHit> hits = new List<MoGenHit>();
            for (int i = 0; result != null && i < count; i++)
            {
                Hashtable one = result[i] as Hashtable;
                MoGenHit hit = new MoGenHit();
                hit.Known = one != null;
                if (one != null)
                {
                    hit.Cnt = CoRows.AsId(one["Cnt"]);
                    hit.MoDId = CoRows.AsId(one["MoDId"]);
                }
                hits.Add(hit);
            }
            return hits;
        }

        // U8 报告成功（Cnt=0）的行的 MoDId。
        internal static List<int> Reported(List<MoGenHit> hits)
        {
            List<int> ids = new List<int>();
            for (int i = 0; i < hits.Count; i++)
            {
                if (hits[i].Known && hits[i].Cnt == 0 && hits[i].MoDId > 0)
                {
                    ids.Add(hits[i].MoDId);
                }
            }
            return ids;
        }

        static BridgeException Nothing(MoGenAsk ask, MoGenCall call, List<MoGenHit> hits, List<int> reported, bool taken)
        {
            if (taken)
            {
                return new BridgeException(409, "state_mismatch", "生产订单号已存在：" + ask.Code);
            }
            if (call.Lost != null)
            {
                string text = MoApi.FirstLine(call.Lost.Message);
                return new BridgeException(504, "outcome_unknown",
                    "U8 生产订单生成调用异常，结果未知，不要重投" + (text.Length > 0 ? "：" + text : ""));
            }
            if (reported.Count > 0 || AnyOk(hits))
            {
                return new BridgeException(504, "outcome_unknown", "U8 报告已生成" + Ids(reported)
                    + "但回读不到，结果未知，请在 U8 核对，不要重投");
            }
            for (int i = 0; i < hits.Count; i++)
            {
                if (hits[i].Known && hits[i].Cnt != 0)
                {
                    return Refused(ask.Lines[i], hits[i].Cnt);
                }
            }
            return new BridgeException(504, "outcome_unknown", "U8 已返回成功但回读不到新生产订单，结果未知");
        }

        static bool AnyOk(List<MoGenHit> hits)
        {
            for (int i = 0; i < hits.Count; i++)
            {
                if (hits[i].Known && hits[i].Cnt == 0)
                {
                    return true;
                }
            }
            return false;
        }

        static string Ids(List<int> ids)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < ids.Count; i++)
            {
                Join(sb, ids[i].ToString(CultureInfo.InvariantCulture));
            }
            return sb.Length > 0 ? "（MoDId " + sb + "）" : "";
        }

        // 逐行错误码与 MO05001.GetError 的分类一致；文案取 Usp_MO_GenMoFromSo 与 BF 里的原意。
        internal static BridgeException Refused(MoGenLine line, int cnt)
        {
            string at = "第 " + line.SeqText + " 行：";
            if (cnt == 901027)
            {
                return new BridgeException(403, "no_permission", at + "没有生产部门 " + line.FinalDept + " 的数据权限");
            }
            if (cnt == 901028)
            {
                return new BridgeException(403, "no_permission", at + "没有该生产订单类别的数据权限");
            }
            return new BridgeException(409, "u8_rejected", at + CntText(cnt));
        }

        static readonly Dictionary<int, string> CntTexts = new Dictionary<int, string>
        {
            { 900021, "订单数量减预留数量后不大于 0" },
            { 90010, "BOM 展开失败" },
            { 900022, "BOM 展开失败" },
            { 900023, "客户 BOM 展开失败" },
            { 900024, "生成生产订单表头资料失败" },
            { 900025, "生成生产订单明细资料失败" },
            { 900027, "生成生产订单子件资料失败" },
            { 900028, "生成生产订单替代料资料失败" },
            { 900029, "生成生产订单客户 BOM 替代料资料失败" },
            { 900030, "销售订单已经发生变更，不能下达生产订单" },
            { 900031, "回写销售订单已下达数量失败（销售订单已被修改）" },
            { 900040, "存货没有生产单位" },
            { 90022, "生产订单号重复" }
        };

        internal static string CntText(int cnt)
        {
            string text;
            if (CntTexts.TryGetValue(cnt, out text))
            {
                return text;
            }
            return "U8 生成生产订单失败（代码 " + cnt.ToString(CultureInfo.InvariantCulture) + "）";
        }

        // 返回 null 表示全部对上；否则返回对不上的原因（进 504 文案）。
        internal static string Verify(MoGenAsk ask, List<Dictionary<string, object>> found)
        {
            if (found.Count != ask.Lines.Count)
            {
                return "生成 " + found.Count.ToString(CultureInfo.InvariantCulture) + " 行，请求 "
                    + ask.Lines.Count.ToString(CultureInfo.InvariantCulture) + " 行";
            }
            string moId = CoRows.Col(found[0], "MoId");
            for (int i = 0; i < found.Count; i++)
            {
                if (CoRows.Col(found[i], "MoId") != moId)
                {
                    return "生成了不止一张生产订单";
                }
            }
            if (ask.Code.Length > 0 && !Same(CoRows.Col(found[0], "MoCode"), ask.Code))
            {
                return "单号与请求不符";
            }
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                string bad = VerifyLine(ask, ask.Lines[i], found);
                if (bad != null)
                {
                    return "第 " + ask.Lines[i].SeqText + " 行" + bad;
                }
            }
            return null;
        }

        static string VerifyLine(MoGenAsk ask, MoGenLine line, List<Dictionary<string, object>> found)
        {
            Dictionary<string, object> row = null;
            for (int r = 0; r < found.Count; r++)
            {
                if (CoRows.AsId(CoRows.Col(found[r], "OrderDId")) == line.SoLineId)
                {
                    if (row != null)
                    {
                        return "生成了不止一行";
                    }
                    row = found[r];
                }
            }
            if (row == null)
            {
                return "没有生成";
            }
            int d = ask.QtyDigits;
            if (decimal.Round(MoCreateSql.Dec(CoRows.Col(row, "Qty")), d) != decimal.Round(line.Qty, d))
            {
                return "数量不符";
            }
            if (!Same(CoRows.Col(row, "MDeptCode"), line.FinalDept) || CoRows.AsId(CoRows.Col(row, "MoTypeId")) != ask.MoTypeId)
            {
                return "生产部门或类别不符";
            }
            decimal moved = MoCreateSql.Dec(CoRows.Col(row, "SoMoQty")) - line.MoQtyBefore;
            if (decimal.Round(moved, d) != decimal.Round(line.Qty, d))
            {
                return "销售订单已下达数量没有按本行数量回写";
            }
            return null;
        }

        // 部分成功或对不上：列出库里已有的行（生产订单号、MoDId），没找到的行带上 U8 的逐行错误。
        static BridgeException Partial(MoGenAsk ask, List<Dictionary<string, object>> found, List<MoGenHit> hits, string problem)
        {
            StringBuilder made = new StringBuilder();
            StringBuilder lost = new StringBuilder();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoGenLine line = ask.Lines[i];
                Dictionary<string, object> row = RowOf(found, line.SoLineId);
                if (row != null)
                {
                    Join(made, "第 " + line.SeqText + " 行 → 生产订单 " + CoRows.Col(row, "MoCode").Trim()
                        + "（MoDId " + CoRows.Col(row, "MoDId") + "）");
                    continue;
                }
                Join(lost, "第 " + line.SeqText + " 行" + Why(hits, i));
            }
            string text = "U8 生产订单生成结果未确认（" + problem + "）。已生成：" + (made.Length > 0 ? made.ToString() : "无")
                + (lost.Length > 0 ? "；未生成：" + lost : "") + "。请在 U8 核对后处理，不要重投";
            return new BridgeException(504, "outcome_unknown", text);
        }

        static string Why(List<MoGenHit> hits, int i)
        {
            if (i >= hits.Count || !hits[i].Known || hits[i].Cnt == 0)
            {
                return "";
            }
            return "：" + CntText(hits[i].Cnt);
        }

        static Dictionary<string, object> RowOf(List<Dictionary<string, object>> found, int soLineId)
        {
            for (int r = 0; r < found.Count; r++)
            {
                if (CoRows.AsId(CoRows.Col(found[r], "OrderDId")) == soLineId)
                {
                    return found[r];
                }
            }
            return null;
        }

        static void Join(StringBuilder sb, string part)
        {
            if (sb.Length > 0)
            {
                sb.Append("，");
            }
            sb.Append(part);
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }
    }

    internal sealed class MoGenHit
    {
        public bool Known;
        public int Cnt;
        public int MoDId;
    }
}
