using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using System.Xml;

namespace U8Co
{
    // 加密服务器（许可服务器）地址：config.json 的 licenseServer 非空时用它，否则取 U8 应用服务器配置里登记的加密服务器。
    internal static class LicenseServer
    {
        public const string ConfigKey = "licenseServer";
        const string SettingName = "U8.AA.AppServerConfig.RightServerName";
        const int MaxLength = 80;
        const long MaxFileBytes = 1024 * 1024;
        static readonly Regex HostPattern = new Regex("^[A-Za-z0-9.:-]{1," + MaxLength + "}$", RegexOptions.CultureInvariant);

        // licenseServer：可选字符串，缺省 ""（读应用服务器配置）。非空时只许字母、数字和 . - :，不超过 80 个字符。
        public static string FromConfig(Dictionary<string, object> map)
        {
            object raw;
            if (!map.TryGetValue(ConfigKey, out raw))
            {
                return "";
            }
            string text = raw as string;
            if (text == null)
            {
                throw new InvalidOperationException(ConfigKey + " 必须是字符串；留空表示读应用服务器配置里的加密服务器");
            }
            text = text.Trim();
            if (text.Length > 0 && !Valid(text))
            {
                throw new InvalidOperationException(ConfigKey + " 只能含字母、数字和 . - :，不超过 " + MaxLength + " 个字符");
            }
            return text;
        }

        public static bool Valid(string text)
        {
            return text != null && HostPattern.IsMatch(text);
        }

        // 采样用：配置的，或应用服务器配置里的；都没有时抛 LicenseLeaseError。
        public static string Resolve(BridgeConfig cfg)
        {
            if (!string.IsNullOrEmpty(cfg.LicenseServer))
            {
                return cfg.LicenseServer;
            }
            string found = TryFromBoConfig(cfg.U8Home);
            if (found == null)
            {
                throw new LicenseLeaseError("应用服务器配置里没有加密服务器");
            }
            return found;
        }

        // 读不到、没有这一项或值不合规都返回 null，不抛。
        public static string TryFromBoConfig(string home)
        {
            try
            {
                if (string.IsNullOrEmpty(home))
                {
                    return null;
                }
                FileInfo file = new FileInfo(Path.Combine(Path.Combine(home, "AppServer"), "UFSoft.U8.Framework.Login.BO.config"));
                if (!file.Exists || file.Length > MaxFileBytes)
                {
                    return null;
                }
                return FromXml(File.ReadAllText(file.FullName));
            }
            catch (Exception)
            {
                return null;
            }
        }

        // 任意层级里 name 属性等于 SettingName 的元素，取它的 value。不解析 DTD、不取外部实体。
        public static string FromXml(string xml)
        {
            XmlReaderSettings settings = new XmlReaderSettings();
            settings.DtdProcessing = DtdProcessing.Prohibit;
            settings.XmlResolver = null;
            using (XmlReader reader = XmlReader.Create(new StringReader(xml), settings))
            {
                while (reader.Read())
                {
                    if (reader.NodeType == XmlNodeType.Element && reader.GetAttribute("name") == SettingName)
                    {
                        string value = reader.GetAttribute("value");
                        value = value == null ? null : value.Trim();
                        return Valid(value) ? value : null;
                    }
                }
            }
            return null;
        }
    }
}
