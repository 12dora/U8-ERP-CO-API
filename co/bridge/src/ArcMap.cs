using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Xml;

namespace U8Co
{
    // RsXml 的标签 → 列名表。只读 U8 目录下的文件，按文件名缓存到进程结束；不写、不删任何东西。
    // 每个节点形如 <tag …>列名</tag>；存货的 <body> 是 bas_part 分录，不作字段。
    internal sealed class ArcMap
    {
        // 相对 U8 安装目录（config.json 的 u8Home）。
        const string SubDir = @"EAI\XML\RsXml";
        static readonly object Gate = new object();
        static readonly Dictionary<string, ArcMap> Cache = new Dictionary<string, ArcMap>(StringComparer.OrdinalIgnoreCase);
        static readonly ArcMap Empty = new ArcMap();

        readonly List<string> _tags = new List<string>();
        readonly Dictionary<string, string> _canon = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<string, string> _column = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        // RsXml 里的标签，按文件顺序，写法与文件一致。
        public IList<string> Tags
        {
            get { return _tags.AsReadOnly(); }
        }

        // 不认识的标签返回 null。
        public string Canon(string tag)
        {
            string canon;
            if (tag == null || !_canon.TryGetValue(tag, out canon))
            {
                return null;
            }
            return canon;
        }

        public string Column(string tag)
        {
            string column;
            if (tag == null || !_column.TryGetValue(tag, out column))
            {
                return null;
            }
            return column;
        }

        public static ArcMap Of(ArcKind kind)
        {
            ArcMap map;
            // 只读档案没有 RsXml：空表，get 直接按表列名返回（ArcReadRo）；项目用固定标签表（ArcKind.SqlMap）。
            if (kind.RsFile == null)
            {
                return kind.SqlMap ?? Empty;
            }
            lock (Gate)
            {
                if (Cache.TryGetValue(kind.RsFile, out map))
                {
                    return map;
                }
            }
            ArcMap loaded = Load(kind.RsFile);
            lock (Gate)
            {
                if (!Cache.TryGetValue(kind.RsFile, out map))
                {
                    Cache[kind.RsFile] = loaded;
                    map = loaded;
                }
                return map;
            }
        }

        // 固定标签表：标签、列名成对（受控 SQL 写入的档案）。
        internal static ArcMap Fixed(string[] pairs)
        {
            ArcMap map = new ArcMap();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map._tags.Add(pairs[i]);
                map._canon[pairs[i]] = pairs[i];
                map._column[pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static ArcMap Load(string file)
        {
            string text;
            try
            {
                text = File.ReadAllText(Path.Combine(Path.Combine(Paths.U8Home, SubDir), file), Encoding.UTF8);
            }
            catch (Exception)
            {
                throw new BridgeException(503, "u8_unavailable", "U8 EAI 字段表无法读取：" + file);
            }
            ArcMap map = new ArcMap();
            try
            {
                XmlDocument doc = new XmlDocument();
                doc.XmlResolver = null;
                doc.LoadXml(text);
                map.Fill(doc.DocumentElement);
            }
            catch (XmlException)
            {
                throw new BridgeException(503, "u8_unavailable", "U8 EAI 字段表格式无效：" + file);
            }
            if (map._tags.Count == 0)
            {
                throw new BridgeException(503, "u8_unavailable", "U8 EAI 字段表为空：" + file);
            }
            return map;
        }

        void Fill(XmlElement root)
        {
            if (root == null)
            {
                return;
            }
            foreach (XmlNode section in root.ChildNodes)
            {
                XmlElement el = section as XmlElement;
                if (el == null || string.Equals(el.LocalName, "body", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                FillSection(el);
            }
        }

        void FillSection(XmlElement section)
        {
            foreach (XmlNode node in section.ChildNodes)
            {
                XmlElement el = node as XmlElement;
                if (el == null)
                {
                    continue;
                }
                string tag = el.LocalName;
                string column = el.InnerText.Trim();
                if (!Ident(tag) || !Ident(column) || _canon.ContainsKey(tag))
                {
                    continue;
                }
                _tags.Add(tag);
                _canon[tag] = tag;
                _column[tag] = column;
            }
        }

        static bool Ident(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 64)
            {
                return false;
            }
            if (char.IsDigit(name[0]))
            {
                return false;
            }
            for (int i = 0; i < name.Length; i++)
            {
                if (!IdentChar(name[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool IdentChar(char c)
        {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        }
    }
}
