using System;
using System.Security.Cryptography;
using System.Text;

namespace U8Co
{
    internal static class Crypto
    {
        public static byte[] MacKey(byte[] secret)
        {
            return Hmac(secret, Encoding.ASCII.GetBytes("u8co/v1/mac"));
        }

        public static byte[] EncKey(byte[] secret)
        {
            return Hmac(secret, Encoding.ASCII.GetBytes("u8co/v1/enc"));
        }

        public static byte[] Hmac(byte[] key, byte[] data)
        {
            using (HMACSHA256 mac = new HMACSHA256(key))
            {
                return mac.ComputeHash(data);
            }
        }

        public static byte[] Sha256(byte[] data)
        {
            using (SHA256 sha = SHA256.Create())
            {
                return sha.ComputeHash(data);
            }
        }

        public static string Decrypt(byte[] key, string passwordEnc)
        {
            byte[] blob = Convert.FromBase64String(passwordEnc);
            // IV 16 字节后面至少一块密文。长度不对就不要送进 AES。
            if (blob.Length < 32 || (blob.Length % 16) != 0)
            {
                throw new FormatException("cipher length");
            }
            byte[] iv = new byte[16];
            byte[] cipher = new byte[blob.Length - 16];
            Buffer.BlockCopy(blob, 0, iv, 0, 16);
            Buffer.BlockCopy(blob, 16, cipher, 0, cipher.Length);
            using (AesCryptoServiceProvider aes = new AesCryptoServiceProvider())
            {
                aes.KeySize = 256;
                aes.Mode = CipherMode.CBC;
                aes.Padding = PaddingMode.PKCS7;
                aes.Key = key;
                aes.IV = iv;
                using (ICryptoTransform dec = aes.CreateDecryptor())
                {
                    byte[] plain = dec.TransformFinalBlock(cipher, 0, cipher.Length);
                    return Encoding.UTF8.GetString(plain);
                }
            }
        }

        public static string Hex(byte[] data)
        {
            StringBuilder buf = new StringBuilder(data.Length * 2);
            for (int i = 0; i < data.Length; i++)
            {
                buf.Append(data[i].ToString("x2"));
            }
            return buf.ToString();
        }

        // 只接受小写十六进制，和契约里的签名、密钥格式一致。
        public static byte[] ParseHexLower(string hex)
        {
            if (hex == null || (hex.Length % 2) != 0)
            {
                return null;
            }
            byte[] raw = new byte[hex.Length / 2];
            for (int i = 0; i < raw.Length; i++)
            {
                int hi = Nibble(hex[i * 2]);
                int lo = Nibble(hex[i * 2 + 1]);
                if (hi < 0 || lo < 0)
                {
                    return null;
                }
                raw[i] = (byte)((hi << 4) | lo);
            }
            return raw;
        }

        static int Nibble(char c)
        {
            if (c >= '0' && c <= '9')
            {
                return c - '0';
            }
            if (c >= 'a' && c <= 'f')
            {
                return c - 'a' + 10;
            }
            return -1;
        }
    }
}
