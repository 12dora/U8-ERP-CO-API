using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 合并检验的一条来源：报检单表体 AUTOID 和本次数量。数量没送（Whole）时取该行剩余，合格 = 数量 − 让步 − 不良。
    internal sealed class QmMergeLine
    {
        public int Id;
        public bool Whole = true;
        public decimal Qty;
        public bool RegGiven;
        public decimal Reg;
        public decimal Con;
        public decimal Dis;
    }

    // 合并检验（产品检验单 QM04 参照多条产品报检单行）的请求：vouchers/generate type=qm_product_check，
    // source_ids = 2 到 20 条产品报检单表体 AUTOID（可来自不同报检单，不重复），代替 id；表头同单行生单（QmReq）。
    // lines 可省；给出时与 source_ids 一一对应，每行 {quantity?, fregquantity?, fconquantiy?, fdisquantity?}，
    // 可带 source_line_id（必须等于对应的 source_ids）。登录前解析，source_ids 记在 WorkItem.LineIds，HasId 为假。
    internal static class QmMergeReq
    {
        internal const string Field = "source_ids";
        internal const int Min = 2;
        internal const int Max = 20;
        internal const string Kind = "qm_product_check";
        const string Path = "/u8co/v1/vouchers/generate";
        static readonly string[] LineKeys = new string[]
        {
            "source_line_id", "quantity", "fregquantity", "fconquantiy", "fdisquantity"
        };

        internal static bool Asked(Dictionary<string, object> body, string path)
        {
            return path == Path && body != null && body.ContainsKey(Field);
        }

        // 本请求是合并检验生单（登录前 Apply 过）。
        internal static bool Is(WorkItem item)
        {
            return item != null && item.Path == Path && item.LineIds != null && item.Type != null && item.Type.Name == Kind;
        }

        // 登录前（Requests.ApplyId）：只给产品检验单，不能同时给 id。
        internal static void Apply(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Type == null || item.Type.Name != Kind)
            {
                throw BridgeException.BadField(Field, "只有产品检验单（合并检验）可以给 source_ids");
            }
            if (body.ContainsKey("id"))
            {
                throw BridgeException.BadField(Field, "source_ids 不能与 id 同时给出");
            }
            item.LineIds = ParseIds(body[Field]);
            item.HasId = false;
        }

        // 登录前（Requests.FillGenerate）：lines 可省；给出时条数等于 source_ids，原样交给 Parse 校验。
        // merge_rules（QmMergeRules）只能与 source_ids 一起给。
        internal static object[] Lines(Dictionary<string, object> body, WorkItem item)
        {
            if (!Is(item))
            {
                throw BridgeException.BadField(QmMergeRules.Field, "merge_rules 只能与 source_ids 一起给出（产品检验单合并检验）");
            }
            int count = item.LineIds.Length;
            object raw;
            if (body == null || !body.TryGetValue("lines", out raw))
            {
                return new object[0];
            }
            IList list = raw as IList;
            if (list == null || list.Count != count)
            {
                throw BridgeException.BadField("lines", "合并检验的 lines 可省略；给出时必须与 source_ids 一一对应");
            }
            object[] lines = new object[list.Count];
            list.CopyTo(lines, 0);
            return lines;
        }

        // 每张合并检验都锁同类生单键：所有产品检验单生单、删除互相串行；另锁产品报检单的生单键，与报检单删除、新增串行
        // （来源报检单要查库才知道，锁不到 "qm_product_inspect:<id>"）。
        internal static string[] LockKeys()
        {
            return new string[] { "new:" + Kind, "new:qm_product_inspect" };
        }

        internal static int[] ParseIds(object raw)
        {
            IList list = raw as IList;
            if (list == null)
            {
                throw BridgeException.BadField(Field, "source_ids 必须是 JSON 数组");
            }
            if (list.Count < Min || list.Count > Max)
            {
                throw BridgeException.BadField(Field, "合并检验须选择 2 到 20 条报检单表体行");
            }
            int[] ids = new int[list.Count];
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                ids[i] = Id(list[i], FieldPath.Item(Field, i));
                if (!seen.Add(ids[i]))
                {
                    throw BridgeException.BadField(FieldPath.Item(Field, i), "来源报检单行重复");
                }
            }
            return ids;
        }

        static int Id(object value, string at)
        {
            long n = value is int ? (int)value : (value is long ? (long)value : 0L);
            if (!(value is int) && !(value is long))
            {
                throw BridgeException.BadField(at, "source_ids 的每一项必须是整数");
            }
            if (n < 1 || n > int.MaxValue)
            {
                throw BridgeException.BadField(at, "source_ids 的每一项必须是正整数");
            }
            return (int)n;
        }

        // 登录前、处理函数里各校验一次：表头同单行检验单生单，lines 按 source_ids 对齐。
        public static QmAsk Parse(QmSpec spec, Dictionary<string, object> head, int[] ids, object[] lines,
            List<QmMergeLine> merged)
        {
            QmAsk ask = QmReq.ParseHead(spec, head);
            if (ids == null || ids.Length < Min || ids.Length > Max)
            {
                throw BridgeException.BadField(Field, "合并检验须选择 2 到 20 条报检单表体行");
            }
            object[] rows = lines ?? new object[0];
            if (rows.Length != 0 && rows.Length != ids.Length)
            {
                throw BridgeException.BadField("lines", "合并检验的 lines 可省略；给出时必须与 source_ids 一一对应");
            }
            for (int i = 0; i < ids.Length; i++)
            {
                try
                {
                    merged.Add(rows.Length == 0 ? Whole(ids[i]) : One(ids[i], rows[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return ask;
        }

        static QmMergeLine Whole(int id)
        {
            QmMergeLine line = new QmMergeLine();
            line.Id = id;
            return line;
        }

        static QmMergeLine One(int id, object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (Array.IndexOf(LineKeys, kv.Key == null ? "" : kv.Key.ToLowerInvariant()) < 0)
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
            }
            if (MfgReq.Raw(row, "source_line_id") != null && MfgReq.LineId(row) != id)
            {
                throw BridgeException.BadField("source_line_id", "source_line_id 必须等于对应的 source_ids");
            }
            QmMergeLine line = Whole(id);
            if (MfgReq.Raw(row, "quantity") != null)
            {
                line.Whole = false;
                line.Qty = MfgReq.LineQty(row);
            }
            object reg = MfgReq.Raw(row, "fregquantity");
            line.RegGiven = reg != null;
            line.Reg = reg == null ? 0m : QmReq.Qty(reg, "fregquantity", true);
            line.Con = Opt(MfgReq.Raw(row, "fconquantiy"), "fconquantiy");
            line.Dis = Opt(MfgReq.Raw(row, "fdisquantity"), "fdisquantity");
            if (!line.Whole)
            {
                Split(line, line.Qty);
            }
            return line;
        }

        static decimal Opt(object value, string name)
        {
            return value == null ? 0m : QmReq.Qty(value, name, true);
        }

        // 定下数量后算合格数量（三者之和必须等于数量），返回本行的 QmLineAsk。
        internal static QmLineAsk Split(QmMergeLine line, decimal qty)
        {
            QmLineAsk ask = new QmLineAsk();
            ask.SourceLineId = line.Id;
            ask.Qty = qty;
            ask.Con = line.Con;
            ask.Dis = line.Dis;
            ask.Reg = QmMath.Qualified(qty, line.RegGiven, line.Reg, line.Con, line.Dis);
            return ask;
        }
    }
}
