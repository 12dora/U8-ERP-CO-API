using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的原因码档案部分（ArcReason）：只测纯逻辑（档案定义、EAI 报文、登录前校验、预演模式、权限登记、删除引用），
    // 不连库、不建 COM、不读 U8 目录（字段表用与 ReasonXmlRs.xml 相同的固定表）。
    internal static class ArcReasonSelfTest
    {
        public static void Run()
        {
            CheckKind();
            CheckWiring();
            CheckEnvelope();
            CheckRejects();
            CheckAccepts();
        }

        static void CheckKind()
        {
            ArcKind k = ArcKind.Find(ArcReason.Name);
            Expect("reason kind", k != null && !k.ReadOnly && !k.RoRead);
            Expect("reason eai", k.Root == "reason" && k.RsFile == "ReasonXmlRs.xml" && k.EditProc == "edit");
            Expect("reason table", k.Table == "Reason" && k.Key == "cReasonCode" && k.CodeMax == ArcReason.CodeMax);
            Expect("reason blocked", k.Blocked("code") && !k.Blocked(ArcReason.TypeTag));
            Expect("diffedit default", ArcKind.Find("department").EditProc == "diffedit");
        }

        // 预演一律 validate（EAI 自己提交）；写入不登记功能权限，读取登录即可；删除查引用；meta 的 field_refs。
        static void CheckWiring()
        {
            string[] ops = new string[] { "create", "update", "delete" };
            for (int i = 0; i < ops.Length; i++)
            {
                Expect("reason dry " + ops[i], DryRunModes.Lookup("archives/" + ops[i], ArcReason.Name, "archive_" + ops[i], "") == "validate");
                Expect("reason write rule " + ops[i], PermRegistry.ForKey(ArcGuard.RuleKey(ArcReason.Name, ops[i])) == null);
            }
            PermRule read = PermRegistry.ForKey("archive:" + ArcReason.Name);
            Expect("reason read rule", read != null && read.Auths.Length == 0 && read.Objs.Length == 0);
            Expect("reason refs", ArcRefs.Covers(ArcReason.Name));
            Expect("reason field ref", (string)MetaRefs.FieldRefs()["creasoncode"] == ArcReason.Name);
        }

        // 修改 proc='edit' 发整条记录；删除只带 code；值里的 & < 转义；标签按 RsXml 的大小写发。
        static void CheckEnvelope()
        {
            ArcReq req = Req("update", Fields(ArcReason.MemoTag, "a&b"));
            ArcBag bag = new ArcBag();
            bag.Put("name", "检测<不合格>");
            bag.Put(ArcReason.TypeTag, "1");
            bag.Put(ArcReason.MemoTag, "a&b");
            string xml = ArcXml.Envelope(req, req.Kind.EditProc, bag);
            Expect("reason edit root", xml.IndexOf("roottag='reason'", StringComparison.Ordinal) > 0
                && xml.IndexOf("proc='edit'", StringComparison.Ordinal) > 0);
            Expect("reason edit body", xml.IndexOf("<reason><code>T9</code><name>检测&lt;不合格&gt;</name><Reasontype>1</Reasontype>"
                + "<ReasonMemo>a&amp;b</ReasonMemo></reason>", StringComparison.Ordinal) > 0);
            req = Req("delete", null);
            xml = ArcXml.Envelope(req, "delete", new ArcBag());
            Expect("reason delete body", xml.IndexOf("proc='delete'", StringComparison.Ordinal) > 0
                && xml.IndexOf("<reason><code>T9</code></reason>", StringComparison.Ordinal) > 0);
        }

        static void CheckRejects()
        {
            Rejects("reason create no type", "create", Fields("name", "检测指标不合格"));
            Rejects("reason type range", "create", Fields("name", "x", ArcReason.TypeTag, "256"));
            Rejects("reason type sign", "update", Fields(ArcReason.TypeTag, "-1"));
            Rejects("reason type text", "update", Fields(ArcReason.TypeTag, "1.5"));
            Rejects("reason blank name", "update", Fields("name", "  "));
            Rejects("reason long name", "update", Fields("name", new string('名', ArcReason.NameMax + 1)));
            Rejects("reason long memo", "update", Fields(ArcReason.MemoTag, new string('x', ArcReason.MemoMax + 1)));
        }

        static void CheckAccepts()
        {
            ArcReason.Check(Req("create", Fields("name", "检测指标不合格", ArcReason.TypeTag, "1")));
            ArcReason.Check(Req("update", Fields(ArcReason.MemoTag, "")));
            ArcReason.Check(Req("update", Fields(ArcReason.TypeTag, "15")));
            ArcReason.Check(Req("delete", null));
        }

        static void Rejects(string name, string op, Dictionary<string, string> fields)
        {
            try
            {
                ArcReason.Check(Req(op, fields));
            }
            catch (BridgeException ex)
            {
                Expect(name + " status", ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        // 不经 ArcReq.Parse（它要读 U8 目录的 RsXml）：手工组请求，字段表与 ReasonXmlRs.xml 一致。
        static ArcReq Req(string op, Dictionary<string, string> fields)
        {
            ArcReq req = new ArcReq();
            req.Op = op;
            req.Kind = ArcKind.Find(ArcReason.Name);
            req.Code = "T9";
            req.Map = ArcMap.Fixed(new string[]
            {
                "code", "cReasonCode", "name", "cReasonName", ArcReason.TypeTag, "iReasontype", ArcReason.MemoTag, "cReasonMemo"
            });
            if (fields != null)
            {
                foreach (KeyValuePair<string, string> pair in fields)
                {
                    req.Fields.Put(req.Map.Canon(pair.Key), pair.Value);
                }
            }
            return req;
        }

        static Dictionary<string, string> Fields(params string[] pairs)
        {
            Dictionary<string, string> map = new Dictionary<string, string>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[pairs[i]] = pairs[i + 1];
            }
            return map;
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
