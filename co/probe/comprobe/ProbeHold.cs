using System;
using System.Collections.Generic;

namespace U8Co
{
    // hold：同一子系统连续登录 count 次并全部保持，记下成功几次、第几次失败及原因，最后逐个登出。
    // 用来测许可服务的剩余点数：成功次数就是当时还能分配的点数（上限 count）。
    internal static class ProbeHold
    {
        public static void Run(ProbeRun run, Dictionary<string, object> step)
        {
            ProbeLogin.RefuseOther(step);
            ProbeLogin.RequireAllowed();
            string sub = ProbeLogin.NeedToken(step, "sub", "需要子系统");
            int count = Nums.AsInt(step["count"]);
            List<object> held = new List<object>();
            string error = "";
            try
            {
                for (int i = 0; i < count; i++)
                {
                    try
                    {
                        held.Add(ProbeLogin.Open(run, sub, ProbeLogin.YearOf(step),
                            ProbeLogin.NeedToken(step, "operator", "需要操作员"),
                            ProbeLogin.DateOf(step), ProbeLogin.ServerOf(step)));
                    }
                    catch (Exception ex)
                    {
                        error = ex.Message;
                        break;
                    }
                }
            }
            finally
            {
                for (int i = held.Count - 1; i >= 0; i--)
                {
                    ProbeLogin.Shut(held[i]);
                    ComUtil.Final(held[i]);
                }
            }
            Line line = ProbeOut.Begin(run, step, true);
            line.Str("sub", sub);
            line.Num("held", held.Count);
            line.Num("asked", count);
            line.Str("stop", error);
            ProbeOut.Emit(line);
        }
    }
}
