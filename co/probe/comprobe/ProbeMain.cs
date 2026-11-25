using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    internal static class ProbeMain
    {
        [STAThread]
        static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            ProbeHost.Install();
            try
            {
                RefuseArgs(args);
                if (IntPtr.Size != 4)
                {
                    throw new BridgeException(500, "internal", "必须使用 32 位进程");
                }
                ProbeConf.Load();
                return Run();
            }
            catch (Exception ex)
            {
                ProbeOut.Fail(null, -1, "", ex);
                return 1;
            }
        }

        static void RefuseArgs(string[] args)
        {
            if (args != null && args.Length > 0)
            {
                throw new BridgeException(400, "bad_request", "不接受命令行参数");
            }
        }

        static int Run()
        {
            string secret;
            string script = ProbeStdin.ReadScript(out secret);
            if (secret == null || secret.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "口令为空");
            }
            int budget;
            List<Dictionary<string, object>> steps = ProbeParse.Read(script, out budget);
            using (ProbeRun run = new ProbeRun(secret))
            {
                return Each(run, steps, budget);
            }
        }

        // 退出码：0 全部成功；1 某一步失败并停下；2 跑完了，但有带 tolerate 的步骤失败过。
        const int ExitTolerated = 2;

        static int Each(ProbeRun run, List<Dictionary<string, object>> steps, int budget)
        {
            int started = Environment.TickCount;
            bool tolerated = false;
            for (int i = 0; i < steps.Count; i++)
            {
                run.Index = i;
                try
                {
                    CheckBudget(steps[i], budget, started);
                    Dispatch(run, steps[i]);
                }
                catch (Exception ex)
                {
                    ProbeOut.Fail(run, i, StepIo.Op(steps[i]), ex);
                    if (!Tolerated(steps[i]) || IsWindowClosed(ex))
                    {
                        return 1;
                    }
                    tolerated = true;
                }
            }
            return tolerated ? ExitTolerated : 0;
        }

        // 步骤带 "tolerate": true 且是 call 时失败后继续，后面的 lastsql、回滚照常执行；退出码仍非 0。
        static bool Tolerated(Dictionary<string, object> step)
        {
            object value;
            return StepIo.Op(step) == "call" && step.TryGetValue("tolerate", out value) && value is bool && (bool)value;
        }

        // 写步骤的时间预算用完（看门狗将到点）从不容忍：立即停下。
        static bool IsWindowClosed(Exception ex)
        {
            BridgeException bex = ex as BridgeException;
            return bex != null && bex.Code == "window_closed";
        }

        // 与 comprobe.py 的 _WRITE 对应（trans.rollback 只会撤销，不拦）。
        static readonly string[] WriteOps = new string[] { "call", "trans.begin", "trans.commit", "dom.set" };

        // 看门狗快到点或会话窗口将关时，不再开始新的写步骤：写到一半被还原，写入会留在测试账套里。
        static void CheckBudget(Dictionary<string, object> step, int budget, int started)
        {
            if (budget <= 0 || Array.IndexOf(WriteOps, StepIo.Op(step)) < 0)
            {
                return;
            }
            long elapsed = (long)unchecked((uint)(Environment.TickCount - started)) / 1000;
            if (elapsed >= budget)
            {
                throw new BridgeException(409, "window_closed", "已超出写步骤的时间预算（看门狗将到点），拒绝继续写");
            }
        }

        static void Dispatch(ProbeRun run, Dictionary<string, object> step)
        {
            string op = StepIo.Op(step);
            if (op == "login")
            {
                ProbeLogin.Login(run, step);
                return;
            }
            if (op == "hold")
            {
                ProbeHold.Run(run, step);
                return;
            }
            if (op == "sql")
            {
                ProbeSql.Query(run, step);
                return;
            }
            if (op == "lastsql")
            {
                ProbeSql.Last(run, step);
                return;
            }
            if (IsTrans(op))
            {
                ProbeSql.Trans(run, step);
                return;
            }
            if (op.StartsWith("dom.", StringComparison.Ordinal))
            {
                ProbeDom.Exec(run, step);
                return;
            }
            ProbeCall.Exec(run, step);
        }

        static bool IsTrans(string op)
        {
            return op == "trans.begin" || op == "trans.commit" || op == "trans.rollback";
        }
    }
}
