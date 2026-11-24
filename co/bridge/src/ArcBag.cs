using System;
using System.Collections.Generic;

namespace U8Co
{
    // 按插入顺序保存的标签 → 值；标签用 RsXml 的写法，查找不分大小写。
    internal sealed class ArcBag
    {
        readonly List<string> _order = new List<string>();
        readonly Dictionary<string, string> _vals = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public int Count
        {
            get { return _order.Count; }
        }

        public IList<string> Tags
        {
            get { return _order.AsReadOnly(); }
        }

        public bool Has(string tag)
        {
            return tag != null && _vals.ContainsKey(tag);
        }

        public string Get(string tag)
        {
            string value;
            if (tag == null || !_vals.TryGetValue(tag, out value))
            {
                return null;
            }
            return value;
        }

        public void Put(string tag, string value)
        {
            if (tag == null || value == null)
            {
                return;
            }
            if (!_vals.ContainsKey(tag))
            {
                _order.Add(tag);
            }
            _vals[tag] = value;
        }
    }
}
