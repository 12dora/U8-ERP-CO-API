using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的票据读取部分：列表类型 ar_note / ap_note 的列、筛选与 SQL 占位符，notes/get 的请求校验、
    // 权限规则、读线程池登记和 SQL 形状。只跑纯函数，不连库。
    internal static class NotesReadSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckListSql();
            CheckParse();
            CheckWiring();
            CheckGetSql();
        }

        static void CheckKinds()
        {
            ListKind ar = ListKinds.Find(NotesReadReq.ArType);
            ListKind ap = ListKinds.Find(NotesReadReq.ApType);
            Expect("note kinds", ar != null && ap != null && Kinds.Find("ar_note") == null && Kinds.Find("ap_note") == null);
            Expect("note cols", All(ar[ListKind.Head] == "AP_Note", ar[ListKind.Id] == "Auto_ID", ar.Has(ListKind.Cus),
                !ar.Has(ListKind.Ven), ap.Has(ListKind.Ven), !ap.Has(ListKind.Cus), !ar.HasBodyUfts));
            Expect("note cond", All(ar[ListKind.Cond] == "h.cFlag = N'AR'", ap[ListKind.Cond] == "h.cFlag = N'AP'"));
            string keys = string.Join(",", ListSql.FullKeys(ar));
            Expect("note keys", keys.EndsWith(",ufts,settle_code,amount,remainder,close_id,opening,expire_date,dw_name,currency",
                StringComparison.Ordinal));
            Expect("note flags", All(ar.VerifiedSql() == "0", ar.ClosedSql().Contains("h.iRAmount")));
        }

        static void CheckListSql()
        {
            ListArgs args = ListArgs.Vouchers(Body("{\"type\":\"ar_note\",\"changed_since\":\"123\",\"after\":5,\"limit\":20,"
                + "\"filter\":{\"code\":\"X1\",\"date_from\":\"2026-09-01\",\"date_to\":\"2026-09-30\",\"cus_code\":\"C1\","
                + "\"dep_code\":\"D1\",\"person_code\":\"P1\",\"maker\":\"op001\",\"closed\":false,\"verified\":false}}"));
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, null);
            Expect("note list params", Count(sql, '?') == ps.Count && ps.Count == 10 && (int)ps[0] == 21 && (int)ps[1] == 5);
            Expect("note list sql", sql.Contains(" FROM AP_Note h WHERE h.cFlag = N'AR' AND h.Auto_ID > ?")
                && sql.Contains("h.Ufts > CONVERT(binary(8), CONVERT(bigint, ?))") && sql.Contains("h.cBill")
                && sql.EndsWith(" ORDER BY h.Auto_ID", StringComparison.Ordinal));
            ListArgs keys = ListArgs.Vouchers(Body("{\"type\":\"ap_note\",\"keys_only\":true}"));
            List<object> kp = new List<object>();
            string ksql = ListSql.Vouchers(keys, kp, null);
            Expect("note keys sql", ksql.StartsWith("SELECT TOP (?) h.Auto_ID AS id, h.cVouchID AS code, ", StringComparison.Ordinal)
                && ksql.Contains("h.cFlag = N'AP'") && Count(ksql, '?') == kp.Count);
            ListRefused("note wh", "{\"type\":\"ar_note\",\"filter\":{\"wh_code\":\"01\"}}");
            ListRefused("note ven", "{\"type\":\"ar_note\",\"filter\":{\"ven_code\":\"V1\"}}");
            ListRefused("note red", "{\"type\":\"ap_note\",\"filter\":{\"red\":true}}");
        }

        static void CheckParse()
        {
            NoteAsk byId = NotesReadReq.Parse(Body("{\"type\":\"ar_note\",\"id\":3416}"));
            Expect("note parse id", byId.Flag == "AR" && byId.Id == 3416 && byId.Code == null);
            NoteAsk byCode = NotesReadReq.Parse(Body("{\"type\":\"ap_note\",\"code\":\"PJ0001\"}"));
            Expect("note parse code", byCode.Flag == "AP" && byCode.Id == 0 && byCode.Code == "PJ0001");
            Refused("note both", "{\"type\":\"ar_note\",\"id\":1,\"code\":\"PJ1\"}", "id");
            Refused("note none", "{\"type\":\"ar_note\"}", "id");
            Refused("note type", "{\"type\":\"ar_bill\",\"id\":1}", "type");
            Refused("note no type", "{\"id\":1}", "type");
            Refused("note id zero", "{\"type\":\"ar_note\",\"id\":0}", "id");
            Refused("note id text", "{\"type\":\"ar_note\",\"id\":\"1\"}", "id");
            Refused("note id big", "{\"type\":\"ar_note\",\"id\":2147483648}", "id");
            Expect("note flag of", NotesReadReq.FlagOf("ap_note") == "AP" && NotesReadReq.FlagOf("ar_bill") == null);
        }

        static void CheckWiring()
        {
            WorkItem item = Item(NotesReadReq.Path, "{\"type\":\"ap_note\",\"id\":7}");
            PermRule rule = PermRegistry.Find(item);
            Expect("note rule", rule != null && rule.Key == "voucher:ap_note" && Array.IndexOf(rule.Auths, "AP2231") >= 0);
            Expect("note rule bad type", PermRegistry.Find(Item(NotesReadReq.Path, "{\"type\":\"ap_bill\",\"id\":7}")) == null);
            Expect("note list rule", PermRegistry.Find(Item(Requests.ListPath, "{\"type\":\"ar_note\"}")).Key == "voucher:ar_note");
            Expect("note read", PermRegistry.IsRead(NotesReadReq.Path) && RouteClass.IsSqlRead(item) && !WriteGate.IsWrite(item));
            Expect("note spec", NotesReadReq.Spec[0] == NotesReadReq.Path && Array.IndexOf(NotesReadReq.Spec, "code") > 0);
        }

        static void CheckGetSql()
        {
            List<object> ps = new List<object>();
            string sql = NotesRead.HeadSql(NotesReadReq.Parse(Body("{\"type\":\"ar_note\",\"id\":9}")), ps, null, true);
            Expect("note head id", All(sql.Contains(" FROM AP_Note h WHERE h.cFlag = N'AR' AND h.Auto_ID = ?"), ps.Count == 1,
                (int)ps[0] == 9, Count(sql, '?') == 1));
            List<object> cp = new List<object>();
            string csql = NotesRead.HeadSql(NotesReadReq.Parse(Body("{\"type\":\"ap_note\",\"code\":\"PJ9\"}")), cp, null, false);
            Expect("note head code", All(csql.Contains("h.cFlag = N'AP' AND h.cVouchID = ?"), (string)cp[0] == "PJ9",
                Count(csql, '?') == 1, !csql.Contains("cReceiveAccount")));
            Expect("note sub sql", All(Count(NotesRead.SubSql, '?') == 2, NotesRead.SubSql.Contains("WHERE s.cLink = ?")));
            Expect("note style", All(NotesRead.StyleName(" 9D") == "贴现", NotesRead.StyleName("9E") == "背书",
                NotesRead.StyleName("XX") == "XX", NotesRead.StyleName(null) == ""));
            CheckShape();
        }

        static void CheckShape()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["id"] = "3416";
            row["opening"] = "0";
            row["sub_package"] = "1";
            row["amount"] = "12000";
            Dictionary<string, object> head = NotesRead.Shape(row, NotesRead.HeadKeys);
            Expect("note shape", All((int)head["id"] == 3416, (bool)head["opening"] == false, (bool)head["sub_package"],
                (string)head["amount"] == "12000", head.ContainsKey("ufts"), head["ufts"] == null,
                head.Count == NotesRead.HeadKeys.Length));
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
        }

        static void ListRefused(string name, string json)
        {
            try
            {
                ListArgs.Vouchers(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Refused(string name, string json, string field)
        {
            try
            {
                NotesReadReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static WorkItem Item(string path, string json)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = Body(json);
            return item;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
        }

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
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
