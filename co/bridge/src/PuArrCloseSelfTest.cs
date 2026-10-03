using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的到货单关闭 / 打开部分（PuArrClose）：闸门、提交前核对、响应体、类型表、预演模式、签名表。只跑纯函数，不连库。
    internal static class PuArrCloseSelfTest
    {
        public static void Run()
        {
            CheckGateHead();
            CheckGateWhole();
            CheckGateLines();
            CheckReached();
            CheckBody();
            CheckWiring();
        }

        static void CheckGateHead()
        {
            Refused("arr red", "close", Head("1", "演示", ""), Lines(31, ""), null, "400 仅支持蓝字到货单");
            Refused("arr blank bill", "close", Head("", "演示", ""), Lines(31, ""), null, "400 仅支持蓝字到货单");
            Refused("arr unverified", "close", Head("0", "", ""), Lines(31, ""), null, "409 单据未审核");
            // 打开不要求已审核（与采购订单相同）。
            PuArrClose.Gate("open", Head("0", "", "演示"), Lines(31, "演示"), null);
        }

        static void CheckGateWhole()
        {
            PuArrClose.Gate("close", Head("0", "演示", ""), Lines(31, "", 32, ""), null);
            // 只有部分行已关闭时整单关闭照常放行（与采购订单相同）。
            PuArrClose.Gate("close", Head("0", "演示", ""), Lines(31, "演示", 32, ""), null);
            Refused("arr closed again", "close", Head("0", "演示", "演示"), Lines(31, "演示"), null, "409 单据已关闭");
            Refused("arr open open", "open", Head("0", "演示", ""), Lines(31, "", 32, ""), null, "409 单据未关闭");
            PuArrClose.Gate("open", Head("0", "演示", ""), Lines(31, "演示", 32, ""), null);
            PuArrClose.Gate("open", Head("0", "演示", "演示"), Lines(31, "演示"), null);
        }

        static void CheckGateLines()
        {
            Dictionary<int, string> lines = Lines(31, "", 32, "演示");
            Dictionary<string, object> head = Head("0", "演示", "");
            PuArrClose.Gate("close", head, lines, new int[] { 31 });
            PuArrClose.Gate("open", head, lines, new int[] { 32 });
            Refused("arr empty lines", "close", head, lines, new int[0], "400 没有要处理的明细");
            Refused("arr many lines", "close", head, lines, new int[PuArrClose.LinesMax + 1], "400 没有要处理的明细");
            Refused("arr foreign line", "close", head, lines, new int[] { 99 }, "400 明细行不存在");
            Refused("arr zero line", "close", head, lines, new int[] { 0 }, "400 明细行不存在");
            Refused("arr dup line", "close", head, lines, new int[] { 31, 31 }, "400 明细行重复");
            Refused("arr line closed", "close", head, lines, new int[] { 32 }, "409 单据已关闭");
            Refused("arr line open", "open", head, lines, new int[] { 31 }, "409 单据未关闭");
        }

        static void CheckReached()
        {
            Expect("arr reached close", PuArrClose.Reached("close", null, Lines(31, "演示", 32, "演示")));
            Expect("arr partial close", !PuArrClose.Reached("close", null, Lines(31, "演示", 32, "")));
            Expect("arr reached open", PuArrClose.Reached("open", null, Lines(31, "", 32, "")));
            Expect("arr left closed", !PuArrClose.Reached("open", null, Lines(31, "", 32, "演示")));
            Expect("arr line reached", PuArrClose.Reached("close", new int[] { 31 }, Lines(31, "演示", 32, "")));
            Expect("arr line missed", !PuArrClose.Reached("close", new int[] { 32 }, Lines(31, "演示", 32, "")));
            Expect("arr no lines", !PuArrClose.Reached("close", null, new Dictionary<int, string>()));
            CheckUntouched();
        }

        // 按行处理时没选的行必须保持原状（U8 顺带关掉 / 打开别的行就回滚）。
        static void CheckUntouched()
        {
            int[] one = new int[] { 31 };
            Dictionary<int, string> before = Lines(31, "", 32, "", 33, "演示");
            Expect("arr kept", PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "", 33, "演示")));
            Expect("arr other closed", !PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "演示", 33, "演示")));
            Expect("arr other opened", !PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "", 33, "")));
            Expect("arr line gone", !PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "")));
            Expect("arr line swapped", !PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "", 34, "演示")));
            Expect("arr closer renamed", PuArrClose.Untouched(one, before, Lines(31, "演示", 32, "", 33, "张三")));
            Expect("arr whole skips", PuArrClose.Untouched(null, before, Lines(31, "演示", 32, "演示", 33, "演示")));
        }

        static void CheckBody()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["closer"] = "演示";
            head["closed_date"] = new DateTime(2026, 9, 28);
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>>();
            lines.Add(Row(31, "演示"));
            lines.Add(Row(32, ""));
            Dictionary<string, object> body = PuArrClose.Body("arrival", 9, "close", head, lines);
            List<Dictionary<string, object>> list = (List<Dictionary<string, object>>)body["lines"];
            Expect("arr body ok", (bool)body["ok"] && (string)body["type"] == "arrival" && (int)body["id"] == 9);
            Expect("arr body head", (string)body["action"] == "close" && (bool)body["closed"] && (string)body["closed_by"] == "演示");
            Expect("arr body date", (string)body["closed_at"] == "2026-09-28" && list.Count == 2);
            Expect("arr body lines", (int)list[0]["line_id"] == 31 && (bool)list[0]["closed"] && !(bool)list[1]["closed"]);
            CheckBodyOpen(head, lines);
        }

        static void CheckBodyOpen(Dictionary<string, object> head, List<Dictionary<string, object>> lines)
        {
            head["closer"] = "";
            Dictionary<string, object> body = PuArrClose.Body("arrival", 9, "open", head, lines);
            Expect("arr body open", !(bool)body["closed"] && (string)body["closed_by"] == "" && (string)body["closed_at"] == "");
            Expect("arr lines sql", PuArrClose.LinesSql.Contains("cbcloser") && PuArrClose.LinesSql.Contains("Autoid"));
            Expect("arr head sql", PuArrClose.HeadSql.Contains("ccloser") && PuArrClose.HeadSql.Contains("dclosedate"));
        }

        static void CheckWiring()
        {
            VoucherKind kind = Kinds.Find("arrival");
            Expect("arr closable", kind != null && kind.Closable && !Kinds.Find("purchase_return").Closable);
            Expect("arr dry run", DryRunModes.Lookup("/u8co/v1/vouchers/close", "arrival", "close", "") == DryRunModes.Rollback
                && DryRunModes.Lookup("/u8co/v1/vouchers/close", "purchase_return", "close", "") == DryRunModes.Refuse);
            int found = 0;
            foreach (SigNeed need in SigTable.Build())
            {
                if (need.ProgId == "VoucherCO_PU.clsVoucherCO_PU" && (need.Member == "CloseArrItems" || need.Member == "OpenArrItems"))
                {
                    Expect("arr sig " + need.Member, need.ArgCount == 4 && need.ByRef != null && need.ByRef.Length == 1
                        && need.ByRef[0] == 2);
                    found++;
                }
            }
            Expect("arr sig rows", found == 2);
        }

        static Dictionary<string, object> Head(string bill, string verifier, string closer)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["bill_type"] = bill;
            row["verifier"] = verifier;
            row["closer"] = closer;
            return row;
        }

        static Dictionary<int, string> Lines(params object[] pairs)
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(int)pairs[i]] = (string)pairs[i + 1];
            }
            return map;
        }

        static Dictionary<string, object> Row(int id, string closer)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["line_id"] = id;
            row["closer"] = closer;
            return row;
        }

        static void Refused(string name, string action, Dictionary<string, object> head, Dictionary<int, string> lines,
            int[] ids, string want)
        {
            try
            {
                PuArrClose.Gate(action, head, lines, ids);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status + " " + ex.Message == want);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
