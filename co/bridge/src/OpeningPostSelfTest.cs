using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的期初记账部分（openings/post；存货核算见 OpeningIaSelfTest）：请求校验（module、action）、闸门、登记（写闸门、幂等、预演、锁键、权限）、
    // 测试账套名单。只跑纯函数，不连库、不建 COM。
    internal static class OpeningPostSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckGate();
            CheckWiring();
            OpeningIaSelfTest.Run();
            TestAccountGateSelfTest.Check("opening", TestAccountGate.OpeningPostText);
        }

        static void CheckParse()
        {
            OpeningAsk ask = OpeningPostReq.Parse(Body("pu", "post"));
            Expect("opening parse post", ask.Module == "pu" && ask.Post && ask.ActionText() == "post");
            ask = OpeningPostReq.Parse(Body("pu", "unpost"));
            Expect("opening parse unpost", !ask.Post && OpeningPostReq.AuditAction(ask) == OpeningPostReq.UndoAction);
            Expect("opening sub", OpeningPostReq.SubOf(ask) == "PU" && OpeningPostReq.RuleKey(ask) == OpeningPostReq.PuRule);
            ask = OpeningPostReq.Parse(Body("ia", "post"));
            Expect("opening parse ia", ask.Module == "ia" && ask.Post && OpeningPostReq.SubOf(ask) == "IA"
                && OpeningPostReq.RuleKey(ask) == OpeningPostReq.IaRule && OpeningPostReq.AuditAction(ask) == OpeningPostReq.Action);
            ask = OpeningPostReq.Parse(Body("ia", "unpost"));
            Expect("opening parse ia unpost", !ask.Post && OpeningPostReq.AuditAction(ask) == OpeningPostReq.UndoAction);
            Bad("opening module sa", Body("sa", "post"), "module", true);
            Bad("opening module st", Body("st", "post"), "module", true);
            Bad("opening module ia case", Body("IA", "post"), "module", true);
            Bad("opening module case", Body("PU", "post"), "module", true);
            Bad("opening module missing", Body(null, "post"), "module", true);
            Bad("opening action", Body("pu", "close"), "action", false);
            Bad("opening action missing", Body("pu", null), "action", true);
            Dictionary<string, object> number = Body("pu", "post");
            number["module"] = 1;
            Bad("opening module number", number, "module", true);
        }

        static void CheckGate()
        {
            Expect("opening gate post ok", OpeningPost.Refusal(true, Flags(1, 0, 0, 0), 2024) == null);
            Expect("opening gate post done", OpeningPost.Refusal(true, Flags(1, 1, 0, 0), 2024) == "采购期初已记账");
            Expect("opening gate post closed", OpeningPost.Refusal(true, Flags(1, 0, 3, 1), 2024) == "采购已有月份结账，不能期初记账");
            Expect("opening gate post pre-start", OpeningPost.Refusal(true, Flags(4, 0, 2, 1), 2024) == null);
            Expect("opening gate unpost not", OpeningPost.Refusal(false, Flags(1, 0, 0, 0), 2024) == "采购期初未记账");
            Expect("opening gate unpost ok", OpeningPost.Refusal(false, Flags(1, 1, 0, 0), 2024) == null);
            // 启用月份 3：第 1、2 期是记账时一起标上的，不算结账；第 3 期标了才算。
            Expect("opening gate unpost pre-start", OpeningPost.Refusal(false, Flags(3, 1, 2, 1), 2024) == null);
            Expect("opening gate unpost closed", OpeningPost.Refusal(false, Flags(3, 1, 3, 1), 2024)
                == "采购已有月份结账，不能取消期初记账");
            List<int[]> none = new List<int[]>();
            none.Add(new int[] { 1, 0 });
            Expect("opening gate no zero", OpeningPost.Refusal(true, OpeningPost.Flags(none, 1), 2024) != null);
        }

        static void CheckWiring()
        {
            string path = OpeningPostReq.Path;
            Expect("opening write", WriteGate.IsWrite(path) && IdemReq.Supports(path) && DryRunReq.Accepts(path));
            Expect("opening dry mode", DryRunModes.Lookup(path, "", OpeningPostReq.Action, "") == DryRunModes.Rollback
                && (string)MetaDryRun.Routes()["openings/post"] == DryRunModes.Rollback);
            Expect("opening perm", PermRegistry.ForKey(OpeningPostReq.PuRule) != null && !PermRegistry.IsRead(path));
            Expect("opening perm ia", PermRegistry.ForKey(OpeningPostReq.IaRule) != null);
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = path;
            item.Body = Body("pu", "post");
            string[] keys = DocLocks.KeysOf(item);
            Expect("opening locks", Array.IndexOf(keys, "opening:pu") >= 0 && Array.IndexOf(keys, WriteGate.Key) >= 0);
            Expect("opening not sql read", !RouteClass.IsSqlRead(item));
            item.Body = Body("ia", "unpost");
            keys = DocLocks.KeysOf(item);
            Expect("opening locks ia", Array.IndexOf(keys, "opening:ia") >= 0 && Array.IndexOf(keys, WriteGate.Key) >= 0
                && Array.IndexOf(keys, "opening:pu") < 0);
        }

        // 启用月份 month，第 0 期标志 zero；另有一个期间 period 的标志 flag（period 为 0 时不加）。
        static OpeningFlags Flags(int month, int zero, int period, int flag)
        {
            List<int[]> rows = new List<int[]>();
            rows.Add(new int[] { 0, zero });
            for (int p = 1; p < month; p++)
            {
                rows.Add(new int[] { p, zero });
            }
            if (period > 0)
            {
                rows.Add(new int[] { period, flag });
            }
            return OpeningPost.Flags(rows, month);
        }

        static Dictionary<string, object> Body(string module, string action)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (module != null)
            {
                body["module"] = module;
            }
            if (action != null)
            {
                body["action"] = action;
            }
            return body;
        }

        static void Bad(string name, Dictionary<string, object> body, string field, bool hint)
        {
            try
            {
                OpeningPostReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field
                    && (!hint || !string.IsNullOrEmpty(ex.Hint)));
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
