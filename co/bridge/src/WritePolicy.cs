using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 写入策略（config.json 的 writePolicyFile）。不配置时 State 为 off，行为与以前完全相同。
    // 配置了文件就必须存在且有效：启动时缺失、无效或从未加载成功，所有写入一律 503 write_policy_unavailable（不放行）。
    // 后台线程每 reloadSeconds 秒读一次文件、按内容的 SHA-256 判断是否变了（不看修改时间和长度：保留时间戳的
    // 等长替换也要生效）；新内容无效时保留上一份有效快照，
    // 记审计事件 write_policy_invalid，健康检查 write_policy.state 为 invalid。文件被删除时不再放行写入（missing）。
    // 快照不可变，整份替换；请求只读一次 Current。
    internal static class WritePolicy
    {
        public const string ConfigKey = "writePolicyFile";
        const string Off = "off";
        const string Ok = "ok";
        const string Invalid = "invalid";
        const string Missing = "missing";
        const string MissingMark = "-";
        static readonly object Gate = new object();
        static volatile WritePolicySnapshot _current;
        static volatile string _state = Off;
        static string _file;
        // 上次看到的文件内容签名（SHA-256 十六进制）；相同则不重新解析。
        static string _seen;
        static Thread _thread;

        public static WritePolicySnapshot Current
        {
            get { return _current; }
        }

        // off：未配置；ok：当前文件有效；invalid：当前文件无效（Current 可能是上一份有效快照，也可能为 null）；missing：文件不存在。
        public static string State
        {
            get { return _state; }
        }

        // config.json 的 writePolicyFile：缺少为 null（不启用）；相对路径按运行目录解析，结果必须位于运行目录之下。
        public static string ResolvePath(Dictionary<string, object> map)
        {
            object raw;
            if (map == null || !map.TryGetValue(ConfigKey, out raw))
            {
                return null;
            }
            string text = raw as string;
            if (text == null || text.Trim().Length == 0)
            {
                throw new InvalidOperationException(ConfigKey + " 必须是非空字符串；不需要时删掉这个键");
            }
            text = text.Trim();
            string full = Path.IsPathRooted(text) ? text : Path.Combine(Paths.Root, text);
            try
            {
                return Paths.UnderRoot(full);
            }
            catch (InvalidOperationException ex)
            {
                throw new InvalidOperationException(ConfigKey + "：" + ex.Message);
            }
        }

        // AppHost 启动时调用一次：同步加载一次，再起后台重载线程。未配置时什么都不做。
        public static void Start(BridgeConfig cfg)
        {
            string file = cfg == null ? null : cfg.WritePolicyFile;
            if (file == null)
            {
                return;
            }
            lock (Gate)
            {
                Init(file);
                if (_thread != null)
                {
                    return;
                }
                _thread = new Thread(Loop);
                _thread.IsBackground = true;
                _thread.Name = "u8co-write-policy";
                _thread.Start();
            }
        }

        // 换到 file 并同步加载一次（不起线程）。自检也直接调用。
        internal static void Init(string file)
        {
            lock (Gate)
            {
                _file = file;
                _current = null;
                _state = Missing;
                _seen = null;
                Poll();
            }
        }

        // 自检用：回到未配置（off）。
        internal static void ResetForTest()
        {
            lock (Gate)
            {
                _file = null;
                _current = null;
                _state = Off;
                _seen = null;
            }
        }

        // 自检用：立即按当前文件重载一次（与后台线程的一轮相同）。
        internal static void PollForTest()
        {
            lock (Gate)
            {
                Poll();
            }
        }

        static void Loop()
        {
            while (true)
            {
                WritePolicySnapshot snap = _current;
                int seconds = snap == null ? 2 : snap.ReloadSeconds;
                Thread.Sleep(TimeSpan.FromSeconds(Math.Max(1, seconds)));
                try
                {
                    lock (Gate)
                    {
                        Poll();
                    }
                }
                catch (Exception)
                {
                    // 重载失败不影响服务：状态保持不变，下一轮再看。
                }
            }
        }

        // 只在 Gate 内调用。
        static void Poll()
        {
            if (_file == null)
            {
                return;
            }
            FileInfo info = new FileInfo(_file);
            if (!info.Exists)
            {
                if (_seen != MissingMark)
                {
                    _current = null;
                    _state = Missing;
                    _seen = MissingMark;
                    Event("write_policy_missing", null);
                }
                return;
            }
            byte[] bytes;
            try
            {
                bytes = ReadBytes(_file);
            }
            catch (IOException)
            {
                // 正在被改写：下一轮再读。
                return;
            }
            catch (UnauthorizedAccessException)
            {
                // 读不到（权限）：状态不变，下一轮再读。
                return;
            }
            string seen = Signature(bytes);
            if (seen == _seen)
            {
                return;
            }
            _seen = seen;
            Apply(Decode(bytes), DateTime.UtcNow);
        }

        internal static string Signature(byte[] bytes)
        {
            using (SHA256 sha = SHA256.Create())
            {
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "");
            }
        }

        static byte[] ReadBytes(string path)
        {
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (MemoryStream copy = new MemoryStream())
            {
                fs.CopyTo(copy);
                return copy.ToArray();
            }
        }

        // 与 ReadShared 相同：按 UTF-8 读，有 BOM 按 BOM。
        static string Decode(byte[] bytes)
        {
            using (StreamReader reader = new StreamReader(new MemoryStream(bytes), Encoding.UTF8, true))
            {
                return reader.ReadToEnd();
            }
        }

        // 解析成功则整份替换；失败保留上一份有效快照。只在 Gate 内或自检里调用。
        internal static void Apply(string text, DateTime nowUtc)
        {
            try
            {
                WritePolicySnapshot snap = WritePolicySnapshot.Parse(text, nowUtc);
                _current = snap;
                _state = Ok;
                Event("write_policy_loaded", null);
            }
            catch (Exception ex)
            {
                _state = Invalid;
                Event("write_policy_invalid", ex.Message);
            }
        }

        static string ReadShared(string path)
        {
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (StreamReader reader = new StreamReader(fs, Encoding.UTF8, true))
            {
                return reader.ReadToEnd();
            }
        }

        static void Event(string name, string error)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            WritePolicySnapshot snap = _current;
            fields["state"] = _state;
            fields["version"] = snap == null ? (object)null : snap.Version;
            if (error != null)
            {
                fields["error"] = error.Length > 300 ? error.Substring(0, 300) : error;
            }
            AuditEvent.Write(name, fields);
        }

        // 健康检查的 write_policy 对象：{state, version, loaded_at, freeze:{global, accounts}, window_open}。
        public static object HealthJson()
        {
            return HealthOf(_state, _current, DateTime.Now);
        }

        internal static Dictionary<string, object> HealthOf(string state, WritePolicySnapshot snap, DateTime nowLocal)
        {
            Dictionary<string, object> obj = new Dictionary<string, object>();
            obj["state"] = state;
            if (state == Off)
            {
                return obj;
            }
            Dictionary<string, object> freeze = new Dictionary<string, object>();
            freeze["global"] = snap != null && snap.FreezeGlobal;
            freeze["accounts"] = snap == null ? new string[0] : snap.FreezeAccounts;
            obj["version"] = snap == null ? (object)null : snap.Version;
            obj["loaded_at"] = snap == null ? null
                : snap.LoadedAtUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            obj["freeze"] = freeze;
            obj["window_open"] = snap != null && snap.WindowOpen(nowLocal);
            return obj;
        }

        // 追加到健康检查 JSON 末尾（以逗号开头）。
        public static string HealthFragment()
        {
            try
            {
                return ",\"write_policy\":" + new JavaScriptSerializer().Serialize(HealthJson());
            }
            catch (Exception)
            {
                return ",\"write_policy\":{\"state\":\"unknown\"}";
            }
        }

        // --check-config 用：文件里的许可保护设置；未配置、不存在或无效时为 null。
        internal static PolicyLicense LicenseOf(BridgeConfig cfg)
        {
            if (cfg == null || cfg.WritePolicyFile == null || !File.Exists(cfg.WritePolicyFile))
            {
                return null;
            }
            try
            {
                return WritePolicySnapshot.Parse(ReadShared(cfg.WritePolicyFile), DateTime.UtcNow).License;
            }
            catch (Exception)
            {
                return null;
            }
        }

        // --check-config 的说明行：未配置、文件不存在、无效（带原因），或有效时的版本、冻结与账套。
        public static string Describe(BridgeConfig cfg)
        {
            string label = ConfigKey + "（写入策略）: ";
            if (cfg.WritePolicyFile == null)
            {
                return label + "未配置（不限制）";
            }
            if (!File.Exists(cfg.WritePolicyFile))
            {
                return label + cfg.WritePolicyFile + "，文件不存在：所有写入都会被拒绝";
            }
            try
            {
                WritePolicySnapshot snap = WritePolicySnapshot.Parse(ReadShared(cfg.WritePolicyFile), DateTime.UtcNow);
                return label + cfg.WritePolicyFile + "，有效（version " + snap.Version
                    + "，冻结 " + (snap.FreezeGlobal ? "全部" : (snap.FreezeAccounts.Length == 0 ? "无" : string.Join(",", snap.FreezeAccounts)))
                    + "，此刻" + (snap.WindowOpen(DateTime.Now) ? "可写" : "不在可写时段") + "）";
            }
            catch (Exception ex)
            {
                return label + cfg.WritePolicyFile + "，无效：" + ex.Message + "；所有写入都会被拒绝";
            }
        }
    }
}
