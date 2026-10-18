using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户专管业务员部分：请求解析、分管部门跟随、权限与写入分类、锁键、预演模式。不连库。
    internal static class ArcPersonSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckFollow();
            CheckDecide();
            CheckBody();
            CheckRoute();
            CheckLocks();
        }

        static void CheckParse()
        {
            ArcPersonAsk ask = ArcPerson.Parse(Body("C900001", "EX040001", null, false));
            Expect("parse code", ask.Code == "C900001" && ask.Person == "EX040001" && !ask.HasExpect);
            ask = ArcPerson.Parse(Body("C900001", "EX040001", "", true));
            Expect("parse expect empty", ask.HasExpect && ask.Expect == "");
            ask = ArcPerson.Parse(Body("C900001", "EX040001", "op001", true));
            Expect("parse expect", ask.HasExpect && ask.Expect == "op001");
            Dictionary<string, object> nil = Body("C900001", "EX040001", null, false);
            nil["expect"] = null;
            ask = ArcPerson.Parse(nil);
            Expect("parse expect null", !ask.HasExpect);
            Rejected("parse person missing", Body("C900001", null, null, false));
            Rejected("parse person long", Body("C900001", new string('A', 21), null, false));
        }

        static void CheckFollow()
        {
            Expect("follow empty", ArcPerson.FollowDept("", "D1", "D2"));
            Expect("follow same", ArcPerson.FollowDept("D1", "D1", "D2"));
            Expect("follow keep", !ArcPerson.FollowDept("D1", "D9", "D2"));
            Expect("follow no new", !ArcPerson.FollowDept("", "D1", ""));
            Expect("follow null new", !ArcPerson.FollowDept("D1", "D1", null));
            Expect("blank null", ArcPerson.BlankNull("") == null && ArcPerson.BlankNull("X") == "X");
            Expect("note missing", ArcPersonAuth.NoteMissing("D9") == "分管部门不存在：D9");
            Expect("note leaf", ArcPersonAuth.NoteNotLeaf("D9") == "分管部门不是末级：D9");
            Expect("note ended", ArcPersonAuth.NoteEnded("D9") == "分管部门已停用：D9");
        }

        static void CheckDecide()
        {
            ArcPersonSnap snap = NewSnap("", "D1", "D2");
            ArcPerson.DecideFollow(snap, "");
            Expect("decide follow", snap.Follow && snap.DeptNote == null);
            snap = NewSnap("D1", "D1", "D2");
            ArcPerson.DecideFollow(snap, ArcPersonAuth.NoteNotLeaf("D2"));
            Expect("decide skip", !snap.Follow && snap.DeptNote == "分管部门不是末级：D2");
            snap = NewSnap("D1", "D9", "D2");
            ArcPerson.DecideFollow(snap, "");
            Expect("decide keep", !snap.Follow && snap.DeptNote == null);
            snap = NewSnap("D2", "D2", "D2");
            ArcPerson.DecideFollow(snap, "");
            Expect("decide same", !snap.Follow && snap.DeptNote == null);
        }

        static void CheckBody()
        {
            ArcPersonAsk ask = ArcPerson.Parse(Body("C900001", "EX040001", null, false));
            ArcPersonSnap snap = NewSnap("D1", "D1", "D2");
            snap.Person = "EX040002";
            snap.DeptNote = ArcPersonAuth.NoteNotLeaf("D2");
            Dictionary<string, object> body = ArcPerson.OkBody(ask, snap, true);
            Expect("body note", (string)body["dept_note"] == "分管部门不是末级：D2");
            Expect("body keep", (string)body["dept_after"] == "D1");
            snap.Follow = true;
            snap.DeptNote = null;
            body = ArcPerson.OkBody(ask, snap, true);
            Expect("body moved", (string)body["dept_after"] == "D2");
            Expect("body no note", body["dept_note"] == null);
            body = ArcPerson.DryBody(ask, snap);
            Expect("dry note key", body.ContainsKey("dept_note"));
        }

        static ArcPersonSnap NewSnap(string dept, string oldDept, string newDept)
        {
            ArcPersonSnap snap = new ArcPersonSnap();
            snap.Dept = dept;
            snap.OldDept = oldDept;
            snap.NewDept = newDept;
            return snap;
        }

        static void CheckRoute()
        {
            Expect("rule", PermRegistry.ForKey(ArcPerson.RuleKey) != null);
            Expect("write gate", WriteGate.IsWrite(ArcPerson.Path));
            WriteRequestInfo info = WriteClass.Of(ArcPerson.Path, Body("C900001", "EX040001", null, false));
            Expect("write class", info != null && info.Type == "archives" && info.Op == "customer_person");
            Expect("dry mode", DryRunModes.Lookup("archives/customer_person", "", "archive_customer_person", "")
                == DryRunModes.Validate);
            Expect("op name", WriteClass.IsOp("customer_person"));
        }

        static void CheckLocks()
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Config.SerializeWrites = false;
            item.Path = ArcPerson.Path;
            item.Body = Body("C900001", "EX040001", null, false);
            string[] keys = DocLocks.KeysOf(item);
            Expect("lock customer", Array.IndexOf(keys, "arc:customer:C900001") >= 0);
            Expect("lock write", Array.IndexOf(keys, "arc:write") >= 0);
        }

        static Dictionary<string, object> Body(string code, string person, string expect, bool hasExpect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (code != null)
            {
                body["code"] = code;
            }
            if (person != null)
            {
                body["person"] = person;
            }
            if (hasExpect)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static void Rejected(string name, Dictionary<string, object> body)
        {
            try
            {
                ArcPerson.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
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
