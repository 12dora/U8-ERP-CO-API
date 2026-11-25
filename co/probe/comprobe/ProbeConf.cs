using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace U8Co
{
    // comprobe.exe 旁边的 comprobe.json：测试账套、年度、桥运行目录和 U8 安装目录。
    // 由 Linux 端按 e2e 配置生成并上传，build.ps1 复制到 out\。探测程序不接受命令行参数，站点值只从这里来。
    internal sealed class ProbeConf
    {
        const string FileName = "comprobe.json";
        static readonly Regex Acc = new Regex("^[0-9]{3}$");
        static readonly Regex Year = new Regex("^[0-9]{4}$");
        static readonly Regex WinPath = new Regex(@"^[A-Za-z]:(\\[A-Za-z0-9_. ()-]+)+$");
        static ProbeConf _current;

        public string Account;
        public string DefaultYear;
        public string BridgeRoot;
        public string U8Home;

        public static ProbeConf Current
        {
            get { return _current; }
        }

        public static void Load()
        {
            string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, FileName);
            if (!File.Exists(path))
            {
                throw new BridgeException(500, "internal", "缺少 comprobe.json，请重新 build");
            }
            string text = File.ReadAllText(path);
            if (text.Length > 0 && text[0] == '﻿')
            {
                text = text.Substring(1);
            }
            JavaScriptSerializer ser = new JavaScriptSerializer();
            Dictionary<string, object> map = ser.DeserializeObject(text) as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(500, "internal", "comprobe.json 不是对象");
            }
            _current = Parse(map);
        }

        static ProbeConf Parse(Dictionary<string, object> map)
        {
            ProbeConf conf = new ProbeConf();
            conf.Account = Need(map, "account", Acc);
            conf.DefaultYear = Need(map, "year", Year);
            conf.BridgeRoot = NeedPath(map, "bridgeRoot");
            conf.U8Home = NeedPath(map, "u8Home");
            return conf;
        }

        static string Need(Dictionary<string, object> map, string key, Regex pattern)
        {
            object raw;
            if (!map.TryGetValue(key, out raw) || raw == null)
            {
                throw new BridgeException(500, "internal", "comprobe.json 缺少 " + key);
            }
            string text = Convert.ToString(raw).Trim();
            if (!pattern.IsMatch(text))
            {
                throw new BridgeException(500, "internal", "comprobe.json 的 " + key + " 不合法");
            }
            return text;
        }

        static string NeedPath(Dictionary<string, object> map, string key)
        {
            string text = Need(map, key, WinPath);
            string[] parts = text.Split('\\');
            for (int i = 0; i < parts.Length; i++)
            {
                if (parts[i] == "." || parts[i] == "..")
                {
                    throw new BridgeException(500, "internal", "comprobe.json 的 " + key + " 含 . 或 ..");
                }
            }
            return text;
        }
    }
}
