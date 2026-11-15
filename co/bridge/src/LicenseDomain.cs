using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;

namespace U8Co
{
    // 读许可总数时 U8 Framework 的程序集、静态状态和它们装的解析器都不进服务的默认 AppDomain：
    // 每次读取新建一个 AppDomain（ApplicationBase = 桥的 bin 目录），在里面由 LicenseWorker 加载 U8 程序集、调用、解析，
    // 只把解析后的 Dictionary<string, int> 带回来，读完卸载。整个读取放在后台线程上，
    // 结果（或错误）一定下来就发信号，采样线程最多等 JoinMs 的是这个信号，卸载随后在同一后台线程上做完，慢也不丢结果。
    // 超时记 LastError、丢下这个线程（后台线程，不挡退出），6 小时内不再读（LicenseSampler 的 _nextTotals），
    // 上一次的线程（读取或卸载）还没结束时也不再开新的。只由采样线程调用。
    internal static class LicenseDomain
    {
        const int JoinMs = 20000;
        static Thread _stuck;

        public static Dictionary<string, int> Run(string home, string appServer, string serial)
        {
            if (_stuck != null && _stuck.IsAlive)
            {
                LicenseState.NoteError("读许可总数：上一次读取或卸载仍未结束");
                return null;
            }
            _stuck = null;
            LicenseRead job = new LicenseRead();
            job.Home = home;
            job.AppServer = appServer;
            job.Serial = serial;
            Thread thread = new Thread(job.Go);
            thread.IsBackground = true;
            thread.Name = "u8co-license-totals";
            // U8 组件要求在 STA 线程上调用。
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            // 读完后线程还要卸载域：不论结果如何都记下它，下次读之前看它是否已结束。
            _stuck = thread;
            if (!job.Done.WaitOne(JoinMs))
            {
                LicenseState.NoteError("读许可总数超时（" + (JoinMs / 1000) + " 秒），6 小时内不再读");
                return null;
            }
            if (job.Error != null)
            {
                LicenseState.NoteError("读许可总数失败：" + job.Error);
                return null;
            }
            return job.Map;
        }
    }

    // 一次读取：建域、跨域调用 LicenseWorker、发 Done、卸载。结果和错误在 Done 之后由采样线程读。
    // Done 不释放：超时时后台线程之后还会 Set 它；交给终结器（6 小时一次）。
    internal sealed class LicenseRead
    {
        public string Home;
        public string AppServer;
        public string Serial;
        public Dictionary<string, int> Map;
        public string Error;
        public readonly ManualResetEvent Done = new ManualResetEvent(false);

        public void Go()
        {
            AppDomain domain = null;
            try
            {
                Assembly self = typeof(LicenseWorker).Assembly;
                AppDomainSetup setup = new AppDomainSetup();
                setup.ApplicationBase = Path.GetDirectoryName(self.Location);
                setup.ConfigurationFile = AppDomain.CurrentDomain.SetupInformation.ConfigurationFile;
                domain = AppDomain.CreateDomain("u8co-license", null, setup);
                LicenseWorker worker = (LicenseWorker)domain.CreateInstanceAndUnwrap(self.FullName, typeof(LicenseWorker).FullName);
                LicenseReply reply = worker.Read(Home, AppServer, Serial);
                Map = reply == null ? null : reply.Map;
                Error = reply == null ? "没有结果" : reply.Error;
            }
            catch (Exception ex)
            {
                Error = LicenseSampler.Describe(ex);
            }
            finally
            {
                Done.Set();
                Unload(domain);
            }
        }

        static void Unload(AppDomain domain)
        {
            if (domain == null)
            {
                return;
            }
            try
            {
                AppDomain.Unload(domain);
            }
            catch (Exception)
            {
                // 卸不掉（域里还有线程不肯退）也不影响采样；这一份留到进程退出。
            }
        }
    }

    // 跨域带回的结果：只有子系统号和整数，或一行不含许可持有人信息的错误。
    // 与 LicenseWorker 一样是 public：跨域代理只走公开成员。
    [Serializable]
    public sealed class LicenseReply
    {
        public Dictionary<string, int> Map;
        public string Error;
    }

    // 在读许可总数的 AppDomain 里运行。解析器只在本域有效：只在 u8Home 的 Framework、Interop 里按短名找，
    // 卫星资源和 .XmlSerializers 不找，按线程防重入（避免解析器递归到栈溢出）。
    public sealed class LicenseWorker : MarshalByRefObject
    {
        static string _home;
        static int _installed;

        [ThreadStatic]
        static int _resolving;

        public LicenseReply Read(string home, string appServer, string serial)
        {
            LicenseReply reply = new LicenseReply();
            try
            {
                _home = home;
                if (Interlocked.Exchange(ref _installed, 1) == 0)
                {
                    AppDomain.CurrentDomain.AssemblyResolve += Resolve;
                }
                reply.Map = LicenseHooks.ReadTotals(home, appServer, serial);
            }
            catch (Exception ex)
            {
                reply.Error = LicenseSampler.Describe(ex);
            }
            return reply;
        }

        // 域用完就卸载，代理不需要租约续期。
        public override object InitializeLifetimeService()
        {
            return null;
        }

        static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            if (_resolving != 0 || _home == null)
            {
                return null;
            }
            _resolving = 1;
            try
            {
                string name = U8Resolve.ShortName(args == null ? null : args.Name);
                if (U8Resolve.Optional(name) || !U8Resolve.SafeName(name))
                {
                    return null;
                }
                return U8Resolve.FindUnder(_home, U8Resolve.LeanDirs, name, false);
            }
            catch (Exception)
            {
                return null;
            }
            finally
            {
                _resolving = 0;
            }
        }
    }
}
