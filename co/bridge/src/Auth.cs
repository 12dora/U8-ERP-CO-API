using System;
using System.Collections.Generic;
using System.Net;
using System.Text;

namespace U8Co
{
    internal sealed class AuthAttempt
    {
        public string Method;
        public string Path;
        public string Ts;
        public string Nonce;
        public string Sig;
        public byte[] Body;
        public string Ip;
        public long Now;
    }

    internal static class Auth
    {
        const int WindowSeconds = 120;
        const int NonceTtlSeconds = 600;
        static readonly object Gate = new object();
        static readonly Dictionary<string, long> Nonces = new Dictionary<string, long>();

        public static string Sign(byte[] macKey, string method, string path, string ts, string nonce, byte[] body)
        {
            string canon = method + "\n" + path + "\n" + ts + "\n" + nonce + "\n" + Crypto.Hex(Crypto.Sha256(body));
            return Crypto.Hex(Crypto.Hmac(macKey, Encoding.ASCII.GetBytes(canon)));
        }

        public static bool Allow(BridgeConfig cfg, AuthAttempt attempt)
        {
            if (!IpAllowed(cfg, attempt.Ip))
            {
                return false;
            }
            // 先算出期望签名再比较，失败请求也走同样的比较，不靠提前返回泄露密钥。
            string expected = Expected(cfg, attempt);
            bool sigOk = FixedHex(expected, attempt.Sig);
            if (!sigOk || !TimeOk(attempt.Ts, attempt.Now) || !NonceFormat(attempt.Nonce))
            {
                return false;
            }
            return Remember(attempt.Nonce, attempt.Now);
        }

        static string Expected(BridgeConfig cfg, AuthAttempt attempt)
        {
            return Sign(cfg.MacKey, Text(attempt.Method), Text(attempt.Path), Text(attempt.Ts), Text(attempt.Nonce), BodyOf(attempt.Body));
        }

        static string Text(string value)
        {
            return value ?? "";
        }

        static byte[] BodyOf(byte[] body)
        {
            if (body == null)
            {
                return new byte[0];
            }
            return body;
        }

        public static string ClientIp(HttpListenerRequest req)
        {
            try
            {
                if (req.RemoteEndPoint == null || req.RemoteEndPoint.Address == null)
                {
                    return "";
                }
                // 只认 TCP 对端。X-Forwarded-For 可以被调用方伪造。
                string ip = req.RemoteEndPoint.Address.ToString();
                const string mapped = "::ffff:";
                if (ip.StartsWith(mapped, StringComparison.OrdinalIgnoreCase))
                {
                    return ip.Substring(mapped.Length);
                }
                return ip;
            }
            catch (Exception)
            {
                return "";
            }
        }

        public static long UnixNow()
        {
            TimeSpan span = DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
            return (long)span.TotalSeconds;
        }

        public static bool IpAllowed(BridgeConfig cfg, string ip)
        {
            if (ip == null || cfg.AllowedClients == null)
            {
                return false;
            }
            for (int i = 0; i < cfg.AllowedClients.Length; i++)
            {
                if (string.Equals(cfg.AllowedClients[i], ip, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        static bool TimeOk(string ts, long now)
        {
            if (ts == null || ts.Length == 0 || ts.Length > 12)
            {
                return false;
            }
            for (int i = 0; i < ts.Length; i++)
            {
                if (ts[i] < '0' || ts[i] > '9')
                {
                    return false;
                }
            }
            long stamp;
            if (!long.TryParse(ts, out stamp))
            {
                return false;
            }
            long delta = now - stamp;
            if (delta < 0)
            {
                delta = -delta;
            }
            return delta <= WindowSeconds;
        }

        static bool NonceFormat(string nonce)
        {
            if (nonce == null || nonce.Length != 32)
            {
                return false;
            }
            return Crypto.ParseHexLower(nonce) != null;
        }

        static bool Remember(string nonce, long now)
        {
            lock (Gate)
            {
                Prune(now);
                if (Nonces.ContainsKey(nonce))
                {
                    return false;
                }
                Nonces[nonce] = now;
                return true;
            }
        }

        static void Prune(long now)
        {
            List<string> dead = new List<string>();
            foreach (KeyValuePair<string, long> pair in Nonces)
            {
                if (now - pair.Value > NonceTtlSeconds)
                {
                    dead.Add(pair.Key);
                }
            }
            for (int i = 0; i < dead.Count; i++)
            {
                Nonces.Remove(dead[i]);
            }
        }

        // 长度固定为 64 个十六进制字符时逐字符异或，避免按第一个不同字节提前返回。
        static bool FixedHex(string expected, string given)
        {
            if (expected == null || given == null || expected.Length != given.Length)
            {
                return false;
            }
            int diff = 0;
            for (int i = 0; i < expected.Length; i++)
            {
                diff |= expected[i] ^ given[i];
            }
            return diff == 0;
        }
    }
}
