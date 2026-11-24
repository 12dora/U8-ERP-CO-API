using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的采购入库参照来料不良品处理单部分（不连库）：登录前的表头、表体校验，来源规则共用的 SQL 与来源名。
    internal static class StockGenRejSelfTest
    {
        public static void Run()
        {
            CheckSource();
            CheckAccepted();
            CheckRefused();
        }

        static void CheckSource()
        {
            Expect("rej in title", "来料不良品处理单", RejSrc.Incoming.Title);
            Expect("rej pro title", "产品不良品处理单", RejSrc.Product.Title);
            Expect("rej in source", "qm_incoming_reject", RejSrc.Incoming.Source);
            Expect("rej in head sql", "1", Count(RejSrc.Incoming.HeadSql));
            Expect("rej in used sql", "1", Count(RejSrc.Incoming.UsedSql));
            Expect("rej in rd sql", "1", Count(RejSrc.Incoming.RdSql));
            Expect("rej in table", "True", (RejSrc.Incoming.UsedSql.IndexOf("rdrecords01", StringComparison.Ordinal) > 0).ToString());
            Expect("rej pro table", "True", (RejSrc.Product.UsedSql.IndexOf("rdrecords10", StringComparison.Ordinal) > 0).ToString());
            Expect("rej in arrival", "True", (RejSrc.Incoming.HeadSql.IndexOf("ArrLine", StringComparison.Ordinal) > 0).ToString());
            Expect("rej pro mo", "True", (RejSrc.Product.HeadSql.IndexOf("MoDId", StringComparison.Ordinal) > 0).ToString());
        }

        static void CheckAccepted()
        {
            StockGen.CheckRej(Head("01"), new object[] { Line(11, 3m), Line(12, 5m) });
            Dictionary<string, object> head = Head("01");
            head["ddate"] = "2026-10-02";
            head["cmemo"] = "文本A";
            head["cdefine1"] = "DEMO-001";
            object[] many = new object[200];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = Line(i + 1, 1m);
            }
            StockGen.CheckRej(head, many);
        }

        static void CheckRefused()
        {
            Dictionary<string, object> noWh = new Dictionary<string, object>();
            noWh["cmemo"] = "文本A";
            Refused("rej no head", null, new object[] { Line(1, 1m) });
            Refused("rej no warehouse", noWh, new object[] { Line(1, 1m) });
            Refused("rej bad date", With(Head("01"), "ddate", "2026/10/02"), new object[] { Line(1, 1m) });
            Refused("rej head code", With(Head("01"), "ccode", "RK1"), new object[] { Line(1, 1m) });
            Refused("rej no lines", Head("01"), new object[0]);
            Refused("rej null lines", Head("01"), null);
            Refused("rej 201 lines", Head("01"), Many(201));
            Refused("rej dup line", Head("01"), new object[] { Line(1, 1m), Line(1, 2m) });
            Refused("rej zero qty", Head("01"), new object[] { Line(1, 0m) });
            Refused("rej negative qty", Head("01"), new object[] { Line(1, -1m) });
            Refused("rej line wh", Head("01"), new object[] { With(Line(1, 1m), "cwhcode", "02") });
            Refused("rej line po", Head("01"), new object[] { With(Line(1, 1m), "iposid", 5) });
        }

        static object[] Many(int count)
        {
            object[] lines = new object[count];
            for (int i = 0; i < count; i++)
            {
                lines[i] = Line(i + 1, 1m);
            }
            return lines;
        }

        static Dictionary<string, object> Head(string wh)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cwhcode"] = wh;
            return head;
        }

        static Dictionary<string, object> Line(int id, decimal qty)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["source_line_id"] = id;
            line["quantity"] = qty;
            return line;
        }

        static Dictionary<string, object> With(Dictionary<string, object> map, string key, object value)
        {
            map[key] = value;
            return map;
        }

        static void Refused(string name, Dictionary<string, object> head, object[] lines)
        {
            try
            {
                StockGen.CheckRej(head, lines);
            }
            catch (BridgeException ex)
            {
                Expect(name + " code", "400", ex.Status.ToString());
                return;
            }
            throw new InvalidOperationException("rej in selftest: " + name + " 应被拒绝");
        }

        static int CountOf(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                n += sql[i] == '?' ? 1 : 0;
            }
            return n;
        }

        static string Count(string sql)
        {
            return CountOf(sql).ToString();
        }

        static void Expect(string name, string want, string got)
        {
            if (want != got)
            {
                throw new InvalidOperationException("rej in selftest: " + name + " 期望 " + want + " 实际 " + got);
            }
        }
    }
}
