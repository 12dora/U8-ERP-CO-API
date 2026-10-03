using System;
using System.Collections.Generic;

namespace U8Co
{
    // 候选的一行被核销单据：Left 是本次分配过程中剩下的余额；Key 是币种，只与同币种的收付款单行配对。
    internal sealed class AutoCandidate
    {
        public string Key;
        public string Kind;
        public int Id;
        public int Line;
        public string Code;
        public string Date;
        public decimal Balance;
        public decimal Left;
    }

    // 计划里的一笔：某行被核销单据分到的金额，Before 是这笔之前（前面各批分过之后）的余额。
    internal sealed class AutoPiece
    {
        public AutoCandidate Target;
        public decimal Amount;
        public decimal Before;
    }

    // 一批 = 收付款单的一行 + 分给它的若干单据行，对应一次 Save（一个 close 下挂若干 vouch）。
    internal sealed class AutoBatch
    {
        public string Key;
        public string ReceiptKind;
        public int ReceiptId;
        public int Line;
        public string Code;
        public string Date;
        public decimal Remain;
        public decimal Amount;
        public List<AutoPiece> Pieces = new List<AutoPiece>();

        // 交给手工核销的闸门（ArapWriteoffGate.Plan）的请求：发票带行，应收单、应付单整单（行 0）。
        public WriteoffAsk Ask(string flag)
        {
            WriteoffAsk ask = new WriteoffAsk();
            ask.Flag = flag;
            ask.ReceiptKind = ReceiptKind;
            ask.ReceiptId = ReceiptId;
            ask.ReceiptLine = Line;
            ask.Items = new List<WriteoffAskItem>();
            foreach (AutoPiece p in Pieces)
            {
                WriteoffAskItem item = new WriteoffAskItem();
                item.Kind = p.Target.Kind;
                item.Id = p.Target.Id;
                item.Line = ArapWriteoffReq.IsBill(p.Target.Kind) ? 0 : p.Target.Line;
                item.Amount = p.Amount;
                ask.Items.Add(item);
            }
            return ask;
        }
    }

    // 自动核销的配对（账套 iHxRule=0 时 U8 的规则）：同一往来单位，有余额的收付款单行按单据日期、主键、行先后排，
    // 有余额的发票 / 应收应付单行按单据日期、单号、行先后排；依次拿收付款单行去冲单据行，每次取两边余额（和剩余上限）的较小者，
    // 一边用完换下一行，直到任一边用完或到 max_amount。每一行收付款单是一批（一次 Save）。
    // 外币只在同币种之间配对，单据汇率不比（核销按收付款单汇率，汇兑差额留给期末汇兑损益）；max_amount 按原币累计。
    // 上限：一次最多 20 行收付款单、200 行单据；配对是两边的前缀，只取 21 / 201 行候选就能判断是否超限。
    internal static class ArapAutoWriteoffPlan
    {
        public const int MaxReceipts = 20;
        public const int MaxTargets = 200;

        public static List<AutoBatch> Build(object conn, AutoWriteoffAsk ask)
        {
            string local = WriteoffSql.LocalCurrency(conn);
            List<AutoBatch> receipts = Receipts(conn, ask, local);
            List<AutoCandidate> targets = Targets(conn, ask, local);
            List<AutoBatch> plan = Allocate(receipts, targets, ask.MaxAmount);
            int used = 0;
            foreach (AutoCandidate t in targets)
            {
                used += t.Left < t.Balance ? 1 : 0;
            }
            if (plan.Count > MaxReceipts || used > MaxTargets)
            {
                throw ArapWriteoffGate.State("一次最多自动核销 20 行收付款单、200 行单据，请用 receipt、targets 或 date_to 缩小范围");
            }
            return plan;
        }

        static List<AutoBatch> Receipts(object conn, AutoWriteoffAsk ask, string local)
        {
            List<AutoBatch> list = new List<AutoBatch>();
            string kind = ask.Flag == "AP" ? "ap_payment" : "ar_receipt";
            foreach (Dictionary<string, object> row in AutoWriteoffSql.Receipts(conn, ask, local, MaxReceipts + 1))
            {
                AutoBatch b = new AutoBatch();
                b.ReceiptKind = kind;
                b.ReceiptId = CoRows.AsId(CoRows.Col(row, "id"));
                b.Line = CoRows.AsId(CoRows.Col(row, "line"));
                b.Code = CoRows.Col(row, "code");
                b.Date = CoRows.Col(row, "vdate");
                b.Remain = WriteoffSql.Num(CoRows.Col(row, "rem"));
                b.Key = KeyOf(row, local);
                list.Add(b);
            }
            return list;
        }

        static List<AutoCandidate> Targets(object conn, AutoWriteoffAsk ask, string local)
        {
            List<AutoCandidate> list = new List<AutoCandidate>();
            foreach (Dictionary<string, object> row in AutoWriteoffSql.Targets(conn, ask, local, MaxTargets + 1))
            {
                AutoCandidate t = new AutoCandidate();
                t.Kind = CoRows.Col(row, "kind");
                t.Id = CoRows.AsId(CoRows.Col(row, "id"));
                t.Line = CoRows.AsId(CoRows.Col(row, "line"));
                t.Code = CoRows.Col(row, "code");
                t.Date = CoRows.Col(row, "vdate");
                t.Balance = WriteoffSql.Num(CoRows.Col(row, "bal"));
                t.Left = t.Balance;
                t.Key = KeyOf(row, local);
                list.Add(t);
            }
            return list;
        }

        // 配对键：币种，为空按本位币。
        public static string KeyOf(Dictionary<string, object> row, string local)
        {
            string cur = CoRows.Col(row, "cur");
            return cur.Length == 0 ? local : cur;
        }

        // 先进先出的贪心分配，只在同 Key 之间配对（每个 Key 各自一个单据游标）；cap 为 0 表示不限。只返回分到金额的批。
        public static List<AutoBatch> Allocate(List<AutoBatch> receipts, List<AutoCandidate> targets, decimal cap)
        {
            List<AutoBatch> plan = new List<AutoBatch>();
            decimal room = cap > 0 ? cap : decimal.MaxValue;
            Dictionary<string, int> next = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (AutoBatch b in receipts)
            {
                if (room <= 0)
                {
                    break;
                }
                string key = b.Key ?? "";
                int at;
                next.TryGetValue(key, out at);
                room -= Fill(b, targets, ref at, room);
                next[key] = at;
                if (b.Pieces.Count > 0)
                {
                    plan.Add(b);
                }
            }
            return plan;
        }

        // 用一行收付款单从游标 at 起冲同 Key 的单据行，返回分掉的金额；用完的单据行游标后移。
        static decimal Fill(AutoBatch b, List<AutoCandidate> targets, ref int at, decimal room)
        {
            decimal left = b.Remain;
            decimal used = 0m;
            while (left > 0 && used < room && at < targets.Count)
            {
                AutoCandidate t = targets[at];
                if ((t.Key ?? "") != (b.Key ?? "") || t.Left <= 0)
                {
                    at++;
                    continue;
                }
                decimal amount = Math.Min(Math.Min(left, t.Left), room - used);
                AutoPiece p = new AutoPiece();
                p.Target = t;
                p.Amount = amount;
                p.Before = t.Left;
                b.Pieces.Add(p);
                b.Amount += amount;
                left -= amount;
                used += amount;
                t.Left -= amount;
                at += t.Left <= 0 ? 1 : 0;
            }
            return used;
        }
    }
}
