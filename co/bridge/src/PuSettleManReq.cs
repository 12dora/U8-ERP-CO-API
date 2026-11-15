using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购手工结算（vouchers/create，type=purchase_settle）的请求规则，登录前在 Requests.FillCreate（本文件末尾）里调用。
    // 写法与 U8 采购「手工结算」界面执行的 SQL 一致（实测核对），没有官方组件。第一级写入；
    // 桥不做的结算（委外、外币、费用分摊、同一发票行既对冲又配对等）由 PuSettleManRules / PuSettleManGate 拒绝。
    // 表头只收 settle_date（可省，给了就替换本次的 U8 登录日期，同生单）；lines 1 到 400 行（PU.iSettleLoadRowCount），
    // 每行 in_line_id（采购入库行 rdrecords01.AutoID，0 或省略表示没有）、invoice_line_id（采购发票行 PurBillVouchs.ID，同上）、
    // quantity（带符号，红字为负）、amount（可省，结算金额 = 无税金额；缺省按发票行无税金额按数量比例）。
    // 两个 id 都有是入库与发票配对；只有入库行是红蓝入库对冲；只有发票行是红蓝发票对冲（两者的数量在同一存货上合计为 0，由闸门查）。
    internal static class PuSettleManReq
    {
        internal const string CreateRule = "write:purchase_settle:create";
        internal const string InKey = "in_line_id";
        internal const string BillKey = "invoice_line_id";
        internal const string QtyKey = "quantity";
        internal const string AmountKey = "amount";
        const decimal NumMax = 1000000000000m;
        const string Path = "/u8co/v1/vouchers/create";

        internal static bool Handles(WorkItem item, string path)
        {
            return item != null && path == Path && PuSettleReq.Handles(item.Type);
        }

        // 登录前：表头 settle_date、lines 逐行校验；settle_date 替换本次的 U8 登录日期。
        internal static void Fill(Dictionary<string, object> body, WorkItem item)
        {
            object head = body == null ? null : Requests.Field(body, "head");
            if (head != null && !(head is Dictionary<string, object>))
            {
                throw BridgeException.BadField("head", "head 必须是 JSON 对象");
            }
            item.Head = head == null ? new Dictionary<string, object>() : (Dictionary<string, object>)head;
            item.Lines = RawLines(body == null ? null : Requests.Field(body, "lines"));
            Parse(item.Lines);
            string date = PuSettleReq.SettleDate(item.Head);
            if (date.Length > 0)
            {
                PuSettleReq.CheckDay(date, DateTime.Today);
                item.Date = date;
            }
        }

        static object[] RawLines(object raw)
        {
            ArrayList list = raw as ArrayList;
            if (list == null && raw is object[])
            {
                list = new ArrayList((object[])raw);
            }
            if (list == null)
            {
                throw BridgeException.BadField("lines", "lines 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > PuSettleReq.LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 400 行");
            }
            return list.ToArray();
        }

        // 入队后（PuSettleMan）再解析一次：登录前已校验过，这里不会再出错。
        internal static List<ManLine> Parse(object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > PuSettleReq.LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 400 行");
            }
            List<ManLine> list = new List<ManLine>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < lines.Length; i++)
            {
                string at = FieldPath.Item("lines", i);
                ManLine line = One(lines[i] as Dictionary<string, object>, at);
                line.Index = i;
                if (!seen.Add(line.InId.ToString(CultureInfo.InvariantCulture) + ":" + line.BsId.ToString(CultureInfo.InvariantCulture)))
                {
                    throw BridgeException.BadField(at, "入库行与发票行的组合重复");
                }
                list.Add(line);
            }
            return list;
        }

        static ManLine One(Dictionary<string, object> row, string at)
        {
            if (row == null)
            {
                throw BridgeException.BadField(at, "lines 的每一行必须是对象");
            }
            ManLine line = new ManLine();
            foreach (KeyValuePair<string, object> pair in row)
            {
                Take(line, pair.Key ?? "", pair.Value, FieldPath.Join(at, pair.Key));
            }
            Whole(line, at);
            return line;
        }

        // 一行的组合规则：至少一个主键、必有数量、对冲入库行不收金额、金额与数量同号。
        static void Whole(ManLine line, string at)
        {
            if (line.InId == 0 && line.BsId == 0)
            {
                throw BridgeException.BadField(at, "in_line_id、invoice_line_id 至少给一个");
            }
            if (!line.HasQty)
            {
                throw BridgeException.BadField(FieldPath.Join(at, QtyKey), "缺少 quantity");
            }
            if (line.HasAmount && line.BsId == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, AmountKey), "红蓝入库对冲行（没有发票行）不能指定 amount，按暂估金额结算");
            }
            if (line.HasAmount && line.Amount != 0m && Math.Sign(line.Amount) != Math.Sign(line.Qty))
            {
                throw BridgeException.BadField(FieldPath.Join(at, AmountKey), "amount 的正负必须与 quantity 一致");
            }
        }

        static void Take(ManLine line, string key, object value, string field)
        {
            if (key == InKey)
            {
                line.InId = Id(value, field);
                return;
            }
            if (key == BillKey)
            {
                line.BsId = Id(value, field);
                return;
            }
            if (key == QtyKey)
            {
                line.Qty = Num(value, field, false);
                line.HasQty = true;
                return;
            }
            if (key == AmountKey)
            {
                line.Amount = Num(value, field, true);
                line.HasAmount = true;
                return;
            }
            throw BridgeException.BadField(field, "不能设置字段 " + key);
        }

        // 0 到 2147483647 的整数；布尔、字符串、浮点都不收。
        internal static int Id(object value, string field)
        {
            long number;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw BridgeException.BadField(field, "必须是整数");
            }
            if (number < 0 || number > int.MaxValue)
            {
                throw BridgeException.BadField(field, "必须是 0 到 2147483647 的整数");
            }
            return (int)number;
        }

        // 数量不能为 0；金额可以为 0。绝对值不超过 1000000000000。
        internal static decimal Num(object value, string field, bool zeroOk)
        {
            decimal n;
            if (!TryNum(value, out n) || Math.Abs(n) > NumMax || (n == 0m && !zeroOk))
            {
                throw BridgeException.BadField(field, zeroOk ? "必须是绝对值不超过 1000000000000 的数"
                    : "必须是不为 0、绝对值不超过 1000000000000 的数");
            }
            return n;
        }

        static bool TryNum(object value, out decimal n)
        {
            n = 0m;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    n = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double)
                {
                    double d = (double)value;
                    if (double.IsNaN(d) || double.IsInfinity(d))
                    {
                        return false;
                    }
                    n = (decimal)d;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        // MetaFields 的字段全集（表体）。
        internal static string[] MetaLineNames()
        {
            return new string[] { InKey, BillKey, QtyKey, AmountKey };
        }

        // meta 的 writable.create：表头只收 settle_date（可省），表体 1 到 400 行，必填 quantity。
        internal static Dictionary<string, object> Meta()
        {
            Dictionary<string, object> spec = MetaWritable.Spec(PuSettleReq.HeadKey, LineKey, 1, PuSettleReq.LinesMax,
                new string[0]);
            spec["required"] = MetaWritable.Required(new string[0], new string[] { QtyKey });
            return spec;
        }

        static bool LineKey(string low)
        {
            return low == InKey || low == BillKey || low == QtyKey || low == AmountKey;
        }
    }

    internal static partial class Requests
    {
        // vouchers/create 的表头、表体（从 Requests.ApplyPayload 移来：文件行数上限）。采购手工结算走 PuSettleManReq.Fill
        // （1 到 400 行、表头只收 settle_date），其余类型照旧：head 必须是对象、lines 1 到 200 行。
        static void FillCreate(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (PuSettleManReq.Handles(item, path))
            {
                PuSettleManReq.Fill(body, item);
                return;
            }
            item.Head = Json.RequireObject(body, "head", item.Type);
            item.Lines = Json.RequireLines(body, item.Type);
        }
    }

    // 手工结算的一行请求。InId / BsId 为 0 表示没有入库行 / 发票行。
    internal sealed class ManLine
    {
        public int Index;
        public int InId;
        public int BsId;
        public decimal Qty;
        public bool HasQty;
        public decimal Amount;
        public bool HasAmount;
    }
}
