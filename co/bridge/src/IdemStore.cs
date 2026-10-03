using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 一条幂等记录。Response 是当时返回给调用方的 JSON 原文。
    internal sealed class IdemRecord
    {
        public string State;
        public string BodySha;
        public int Status;
        public string Response;
        public long CreatedTicks;
        public string Route;
        public string Acc;
        public string Caller;
        // 写入这条记录的操作员（idempotency/get 只给同一操作员看）。旧记录没有这一项，为空串。
        public string Operator;
    }

    // 幂等记录放在运行目录的 idem 子目录，一个键一个文件（文件名是存储键的 SHA-256），权限继承运行目录。
    // 落盘见 IdemFile：首次 CreateNew + WriteThrough，改写经临时文件 MoveFileEx 原子替换。
    // 进程崩溃或断电后留下的 in_flight 记录、只剩临时文件的键，都按结果未知处理。
    internal static class IdemStore
    {
        public const string InFlight = "in_flight";
        public const string Ok = "ok";
        public const string Failed = "failed";
        public const string Unknown = "outcome_unknown";
        static readonly TimeSpan DoneTtl = TimeSpan.FromHours(24);
        static readonly TimeSpan UnknownTtl = TimeSpan.FromHours(72);
        static readonly TimeSpan PruneEvery = TimeSpan.FromMinutes(30);
        static readonly TimeSpan TmpTtl = TimeSpan.FromHours(1);
        static readonly object FileGate = new object();
        static long _nextPruneTicks;
        static int _pruning;

        public static string Dir
        {
            get { return Paths.UnderRoot(Path.Combine(Paths.Root, "idem")); }
        }

        // 服务启动时在后台清一次过期记录，不拖慢启动。
        public static void Start()
        {
            Interlocked.Exchange(ref _nextPruneTicks, 0);
            PruneSoon();
        }

        // 距上次清理超过 30 分钟就在线程池上再清一次；同一时刻只跑一个。
        public static void PruneSoon()
        {
            long now = DateTime.UtcNow.Ticks;
            if (now < Interlocked.Read(ref _nextPruneTicks))
            {
                return;
            }
            if (Interlocked.CompareExchange(ref _pruning, 1, 0) != 0)
            {
                return;
            }
            Interlocked.Exchange(ref _nextPruneTicks, now + PruneEvery.Ticks);
            try
            {
                ThreadPool.QueueUserWorkItem(PruneJob);
            }
            catch (Exception)
            {
                Interlocked.Exchange(ref _pruning, 0);
            }
        }

        static void PruneJob(object state)
        {
            try
            {
                Prune(DateTime.UtcNow);
            }
            catch (Exception)
            {
            }
            finally
            {
                Interlocked.Exchange(ref _pruning, 0);
            }
        }

        public static TimeSpan TtlOf(string state)
        {
            return state == Ok || state == Failed ? DoneTtl : UnknownTtl;
        }

        public static bool Expired(IdemRecord rec, DateTime nowUtc)
        {
            return nowUtc.Ticks - rec.CreatedTicks > TtlOf(rec.State).Ticks;
        }

        static string FileOf(string id)
        {
            return Path.Combine(Dir, id + ".json");
        }

        // 没有记录返回 null。文件读不出、内容坏了、或只剩临时文件，都按结果未知处理，绝不当成「没做过」。
        public static IdemRecord Read(string id)
        {
            lock (FileGate)
            {
                string file = FileOf(id);
                if (File.Exists(file))
                {
                    string text = File.ReadAllText(file, Encoding.UTF8);
                    return Parse(text, File.GetLastWriteTimeUtc(file).Ticks);
                }
                string tmp = file + ".tmp";
                if (File.Exists(tmp))
                {
                    return Parse("", File.GetLastWriteTimeUtc(tmp).Ticks);
                }
                return null;
            }
        }

        // 首次写（in_flight）：直接建正式文件。已存在则抛 IOException。
        public static void Create(string id, IdemRecord rec)
        {
            lock (FileGate)
            {
                Directory.CreateDirectory(Dir);
                IdemFile.CreateNew(FileOf(id), Encoding.UTF8.GetBytes(Render(rec)));
            }
        }

        public static void Write(string id, IdemRecord rec)
        {
            lock (FileGate)
            {
                Directory.CreateDirectory(Dir);
                IdemFile.Replace(FileOf(id), Encoding.UTF8.GetBytes(Render(rec)));
            }
        }

        // 先删临时文件再删记录：反过来的话，中途失败会留下「只剩临时文件」的键，被当成结果未知。
        public static void Delete(string id)
        {
            lock (FileGate)
            {
                string file = FileOf(id);
                string tmp = file + ".tmp";
                if (File.Exists(tmp))
                {
                    File.Delete(tmp);
                }
                if (File.Exists(file))
                {
                    File.Delete(file);
                }
            }
        }

        // 逐个文件在锁内重读再判断，不会删掉刚被改写成新记录的文件。
        public static void Prune(DateTime nowUtc)
        {
            string dir = Dir;
            if (!Directory.Exists(dir))
            {
                return;
            }
            foreach (string file in Directory.GetFiles(dir, "*.json"))
            {
                PruneOne(Path.GetFileNameWithoutExtension(file), nowUtc);
            }
            foreach (string tmp in Directory.GetFiles(dir, "*.tmp"))
            {
                PruneTmp(tmp, nowUtc);
            }
        }

        static void PruneOne(string id, DateTime nowUtc)
        {
            try
            {
                lock (FileGate)
                {
                    IdemRecord rec = Read(id);
                    if (rec != null && Expired(rec, nowUtc))
                    {
                        File.Delete(FileOf(id));
                    }
                }
            }
            catch (Exception)
            {
            }
        }

        static void PruneTmp(string tmp, DateTime nowUtc)
        {
            try
            {
                lock (FileGate)
                {
                    // 没有对应记录的临时文件本身就是一条「结果未知」，按结果未知的保存期删。
                    string file = tmp.Substring(0, tmp.Length - ".tmp".Length);
                    TimeSpan ttl = File.Exists(file) ? TmpTtl : UnknownTtl;
                    if (nowUtc - File.GetLastWriteTimeUtc(tmp) > ttl)
                    {
                        File.Delete(tmp);
                    }
                }
            }
            catch (Exception)
            {
            }
        }

        static JavaScriptSerializer Serializer()
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            return ser;
        }

        public static string Render(IdemRecord rec)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["v"] = 1;
            map["state"] = rec.State ?? Unknown;
            map["body_sha256"] = rec.BodySha ?? "";
            map["status"] = rec.Status;
            map["response"] = rec.Response ?? "";
            map["created_utc"] = new DateTime(rec.CreatedTicks, DateTimeKind.Utc).ToString("o", CultureInfo.InvariantCulture);
            map["route"] = rec.Route ?? "";
            map["acc"] = rec.Acc ?? "";
            map["caller"] = rec.Caller ?? "";
            map["operator"] = rec.Operator ?? "";
            return Serializer().Serialize(map);
        }

        // fileTicks：文件的修改时间。内容坏了或缺 created_utc 时按它算过期，坏记录也会被清掉。
        public static IdemRecord Parse(string text, long fileTicks)
        {
            IdemRecord rec = new IdemRecord();
            rec.State = Unknown;
            rec.Status = 504;
            rec.CreatedTicks = fileTicks;
            try
            {
                Dictionary<string, object> map = Serializer().DeserializeObject(text) as Dictionary<string, object>;
                if (map == null)
                {
                    return rec;
                }
                rec.State = Pick(map, "state", Unknown);
                rec.BodySha = Pick(map, "body_sha256", "");
                rec.Response = Pick(map, "response", "");
                rec.Route = Pick(map, "route", "");
                rec.Acc = Pick(map, "acc", "");
                rec.Caller = Pick(map, "caller", "");
                rec.Operator = Pick(map, "operator", "");
                object status;
                rec.Status = map.TryGetValue("status", out status) && status is int ? (int)status : 504;
                rec.CreatedTicks = Created(Pick(map, "created_utc", ""), fileTicks);
            }
            catch (Exception)
            {
                rec.State = Unknown;
            }
            return rec;
        }

        static string Pick(Dictionary<string, object> map, string key, string fallback)
        {
            object value;
            if (!map.TryGetValue(key, out value))
            {
                return fallback;
            }
            string text = value as string;
            return text ?? fallback;
        }

        static long Created(string text, long fileTicks)
        {
            DateTime parsed;
            if (DateTime.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out parsed))
            {
                return parsed.ToUniversalTime().Ticks;
            }
            return fileTicks;
        }
    }
}
