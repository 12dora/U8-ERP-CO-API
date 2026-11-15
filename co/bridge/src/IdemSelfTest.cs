using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的幂等部分：只测纯逻辑（键校验、内容摘要、状态归类、过期），不读写磁盘。
    internal static class IdemSelfTest
    {
        const string Route = "/u8co/v1/vouchers/create";

        public static void Run()
        {
            CheckTake();
            CheckDigest();
            CheckStates();
            CheckTtl();
            CheckRecord();
        }

        static Dictionary<string, object> Body(string passwordEnc, bool swapped)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            if (swapped)
            {
                head["cMemo"] = "备注\"甲\"";
                head["cCusCode"] = "C001";
            }
            else
            {
                head["cCusCode"] = "C001";
                head["cMemo"] = "备注\"甲\"";
            }
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["cInvCode"] = "A";
            line["iQuantity"] = 1.5m;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["acc"] = "999";
            body["year"] = "2024";
            body["operator"] = "op001";
            body["password_enc"] = passwordEnc;
            body["date"] = "2026-09-28";
            body["type"] = "sale_order";
            body["head"] = head;
            body["lines"] = new object[] { line };
            return body;
        }

        static void CheckTake()
        {
            Dictionary<string, object> body = Body("x", false);
            body["idempotency_key"] = "order-2026-0001";
            body["caller"] = "tool:client-a";
            IdemAsk ask = IdemReq.Take(body, Route);
            if (ask == null || ask.Caller != "tool:client-a" || body.ContainsKey("idempotency_key") || body.ContainsKey("caller"))
            {
                throw new InvalidOperationException("idem take");
            }
            Dictionary<string, object> plain = Body("x", false);
            plain["idempotency_key"] = "k1";
            if (IdemReq.Take(plain, Route).Caller != IdemReq.DirectCaller)
            {
                throw new InvalidOperationException("idem direct caller");
            }
            Dictionary<string, object> other = Body("x", false);
            other["idempotency_key"] = "k1";
            if (IdemReq.Take(other, "/u8co/v1/vouchers/load") != null || !other.ContainsKey("idempotency_key"))
            {
                throw new InvalidOperationException("idem unsupported route");
            }
            CheckAllWrites();
            ExpectBad("idem key space", "a b");
            ExpectBad("idem key empty", "");
            ExpectBad("idem key long", new string('k', 129));
            ExpectBad("idem key type", 5);
            IdemReq.CheckKey(new string('k', 128));
        }

        // 全部写路由（含修改、审核、旧版审核、自动核销）都收幂等键；自动核销试算带键 400，真核销照收。
        static void CheckAllWrites()
        {
            string[] writes = new string[]
            {
                "/u8co/v1/vouchers/update", "/u8co/v1/vouchers/verify", "/u8co/v1/sale-orders/verify",
                "/u8co/v1/dispatches/verify", Requests.GlRoot + "post", Requests.WriteoffAutoPath
            };
            for (int i = 0; i < writes.Length; i++)
            {
                Dictionary<string, object> body = Body("x", false);
                body["idempotency_key"] = "k1";
                if (IdemReq.Take(body, writes[i]) == null || body.ContainsKey("idempotency_key"))
                {
                    throw new InvalidOperationException("idem write route " + writes[i]);
                }
            }
            Dictionary<string, object> real = Body("x", false);
            real["idempotency_key"] = "k1";
            real["dry_run"] = false;
            if (IdemReq.Take(real, Requests.WriteoffAutoPath) == null)
            {
                throw new InvalidOperationException("idem auto real");
            }
            Dictionary<string, object> plan = Body("x", false);
            plan["idempotency_key"] = "k1";
            plan["dry_run"] = true;
            try
            {
                IdemReq.Take(plan, Requests.WriteoffAutoPath);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Field == IdemReq.KeyField && plan.ContainsKey("idempotency_key"))
                {
                    return;
                }
            }
            throw new InvalidOperationException("idem auto plan refused");
        }

        static void ExpectBad(string name, object key)
        {
            try
            {
                IdemReq.CheckKey(key);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400)
                {
                    return;
                }
            }
            throw new InvalidOperationException(name);
        }

        // 口令密文不同、键顺序不同，摘要相同；业务内容一变，摘要就变。存储键区分调用方、账套和路由。
        static void CheckDigest()
        {
            string a = IdemReq.BodySha(Body("enc-1", false));
            string b = IdemReq.BodySha(Body("enc-2", true));
            if (a != b || a.Length != 64)
            {
                throw new InvalidOperationException("idem digest stable");
            }
            Dictionary<string, object> nextDay = Body("enc-1", false);
            nextDay["date"] = "2026-09-29";
            if (IdemReq.BodySha(nextDay) != a)
            {
                throw new InvalidOperationException("idem digest date");
            }
            Dictionary<string, object> changed = Body("enc-1", false);
            changed["type"] = "purchase_order";
            if (IdemReq.BodySha(changed) == a)
            {
                throw new InvalidOperationException("idem digest change");
            }
            IdemAsk ask = new IdemAsk();
            ask.Key = "k";
            ask.Caller = "tool:a";
            string id = ask.StoreId("999", Route);
            if (id == ask.StoreId("998", Route) || id == ask.StoreId("999", "/u8co/v1/vouchers/generate"))
            {
                throw new InvalidOperationException("idem store id");
            }
            ask.Caller = "tool:b";
            if (id == ask.StoreId("999", Route))
            {
                throw new InvalidOperationException("idem store id caller");
            }
        }

        // 只存 ok 和结果未知；4xx 和保证没执行的错误码都不占用键；其余 5xx 一律结果未知。
        static void CheckStates()
        {
            Expect("200", IdemStore.Ok, IdemFlow.StateOf(200, "ok"));
            Expect("409", null, IdemFlow.StateOf(409, "u8_rejected"));
            Expect("400", null, IdemFlow.StateOf(400, "bad_request"));
            Expect("422", null, IdemFlow.StateOf(422, "login_failed"));
            Expect("429", null, IdemFlow.StateOf(429, "busy"));
            Expect("503 busy_timeout", null, IdemFlow.StateOf(503, "busy_timeout"));
            Expect("503 stopping", null, IdemFlow.StateOf(503, "stopping"));
            Expect("503 license", null, IdemFlow.StateOf(503, "u8_license_full"));
            Expect("503 com", IdemStore.Unknown, IdemFlow.StateOf(503, "com_unavailable"));
            Expect("500", IdemStore.Unknown, IdemFlow.StateOf(500, "internal"));
            Expect("504", IdemStore.Unknown, IdemFlow.StateOf(504, "outcome_unknown"));
        }

        static void CheckTtl()
        {
            DateTime now = new DateTime(2026, 9, 28, 0, 0, 0, DateTimeKind.Utc);
            IdemRecord rec = new IdemRecord();
            rec.State = IdemStore.Ok;
            rec.CreatedTicks = now.AddHours(-25).Ticks;
            if (!IdemStore.Expired(rec, now))
            {
                throw new InvalidOperationException("idem ttl ok");
            }
            rec.State = IdemStore.Unknown;
            if (IdemStore.Expired(rec, now))
            {
                throw new InvalidOperationException("idem ttl unknown");
            }
            rec.CreatedTicks = now.AddHours(-73).Ticks;
            if (!IdemStore.Expired(rec, now))
            {
                throw new InvalidOperationException("idem ttl unknown expired");
            }
        }

        // 记录序列化往返；坏内容按结果未知处理。
        static void CheckRecord()
        {
            IdemRecord rec = new IdemRecord();
            rec.State = IdemStore.Ok;
            rec.BodySha = new string('a', 64);
            rec.Status = 200;
            rec.Response = "{\"ok\":true,\"id\":1000000001}";
            rec.CreatedTicks = new DateTime(2026, 9, 28, 1, 2, 3, DateTimeKind.Utc).Ticks;
            IdemRecord back = IdemStore.Parse(IdemStore.Render(rec), 0);
            bool same = back.State == rec.State && back.BodySha == rec.BodySha && back.Status == 200
                && back.Response == rec.Response && back.CreatedTicks == rec.CreatedTicks;
            if (!same)
            {
                throw new InvalidOperationException("idem record");
            }
            IdemRecord bad = IdemStore.Parse("{not json", 42);
            Expect("corrupt", IdemStore.Unknown, bad.State);
            if (bad.CreatedTicks != 42 || IdemStore.Parse("", 7).CreatedTicks != 7)
            {
                throw new InvalidOperationException("idem corrupt uses file time");
            }
        }

        static void Expect(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException("idem state " + name);
            }
        }
    }
}
