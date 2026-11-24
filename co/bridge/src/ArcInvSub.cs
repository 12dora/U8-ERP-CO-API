using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货的 Inventory_Sub 扩展属性（采购、批次、质量、定价自由项、标准成本等）的 EAI 标签和取值校验。
    // inventoryxmlrs.xml 的表头只列了其中 14 列（请购超额上限 fbuyexcess、安全库存方法 isurenesstype 等）；U8 随带的导入样式表
    // EAI\XML\StyleSheet\Inventory.xsl 的表头标签更全，其余的 Sub 列都在里面（标签小写，个别是驼峰），经同一条 IClsCommon 进来。
    // 这些标签作为扩展标签（ArcKind.Extra）并入存货的字段表：get 的 fields 按字段表里的标签返回，Sub 列合并读取；
    // 其余 35 个 Sub 列（EAI 不写）作为只读标签并入，get 返回，写入 400「不能设置字段」；
    // 新增、修改只发调用方给出的，不从模板、不从当前行带（它们由 U8 的计划、批次、质量模块维护，缺省值 U8 自己补）。
    // 登录前按列类型校验（ArcCol）：bit 收 0 / 1 / true / false，整数、smallint 收十进制整数，浮点收十进制小数；不收空串。
    internal static class ArcInvSub
    {
        const string Inventory = "inventory";
        internal const string FieldsIgnored = "u8_fields_ignored";

        // 样式表表头里有、RsXml 里没有的 Sub 列（标签与列同名，SQL 列名不分大小写）。一律按小写发送：U8 的存货服务按小写列名取值，
        // 样式表里的驼峰标签（iPlanCheckDay 等）原样发送时实测不写入。
        static readonly string[] ExtraBits = new string[]
        {
            "bbondedinv", "binbyprocheck", "binvkeypart", "bbatchcreate"
        };
        static readonly string[] ExtraInts = new string[]
        {
            "iwarrantyperiod", "iacceptearlydays", "isupplyperiodtype", "iavailabilitydate"
        };
        static readonly string[] ExtraShorts = new string[]
        {
            "iwarrantyunit", "iexpiratdatecalcu", "ibomexpandunittype", "idrawtype"
        };
        static readonly string[] ExtraFloats = new string[]
        {
            "finvciqexch", "finvoutuplimit"
        };
        // 序号 1–10 的成组标志：批次属性、控制自由项取值范围、采购 / 委外 / 销售定价自由项。
        static readonly string[] BitFamilies = new string[]
        {
            "bbatchproperty", "bcontrolfreerange", "bpurpricefree", "bompricefree", "bsalepricefree"
        };
        // 其余的 Sub 列：样式表里没有的、建档时间这类 U8 自己写的，以及样式表里有但 U8 的存货 EAI 实测不写的（计划检验天数、物料周期、
        // 时格、关键需求预测、需求跟踪方式、八个标准成本列）：只读，get 返回，不能写。键 cInvSubCode 不在其中（就是存货编码）。
        static readonly string[] ReadOnlyCols = new string[]
        {
            "bCheckSubitemCost", "bConsiderFreeStock", "bCoupon", "bFeatureMatch", "bImport", "bIsAttachFile", "bMaintenance",
            "bProcessMaterial", "bProcessProduct", "bProduceByFeatureAllocate", "bStoreCard", "bSuitRetail", "bX64", "cInvAppDocNo",
            "fMaterialCost", "fProcessCost", "fRoundFactor", "iCalcStartingPoint", "iMaintenanceCycle", "iMaintenanceCycleUnit",
            "iNearRejectDays", "dInvCreateDatetime", "bSCkeyProjections", "iTimeBucketId", "iPlanCheckDay", "iMaterialsCycle",
            "iRequireTrackStyle", "fCurLLaborCost", "fCurLVarManuCost", "fCurLFixManuCost", "fCurLOMCost", "fNextLLaborCost",
            "fNextLVarManuCost", "fNextLFixManuCost", "fNextLOMCost"
        };
        // RsXml 表头里已有标签的 Sub 列（标签原样用 RsXml 的写法）：只参与取值校验，不进扩展标签。
        static readonly string[] MappedBits = new string[] { "bPUQuota", "bInvROHS", "bPrjMat", "bInvAsset", "bSrvProduct" };
        static readonly string[] MappedInts = new string[] { "idatesum", "iAcceptDelayDays" };
        static readonly string[] MappedShorts = new string[] { "isurenesstype", "idatetype", "idynamicsurenesstype" };
        static readonly string[] MappedFloats = new string[] { "fbuyexcess", "ibestrowsum", "ipercentumsum", "fPrjMatLimit" };

        // 标签（不分大小写）→ 类型：b bit、s smallint、i int、f 浮点或小数。
        static readonly Dictionary<string, char> Types = Build();
        static readonly string[] Pairs = BuildPairs();

        // 存货字段表追加的标签、列名成对（ArcKind.Extra）：可写的扩展标签，后面是只读的 Sub 列。
        internal static string[] ExtraPairs()
        {
            return (string[])Pairs.Clone();
        }

        // 只读的 Sub 列并入存货的不可写标签（ArcKind.Block）。
        internal static string[] BlockWith(string[] block)
        {
            string[] all = new string[block.Length + ReadOnlyCols.Length];
            block.CopyTo(all, 0);
            ReadOnlyCols.CopyTo(all, block.Length);
            return all;
        }

        // 登录前：存货新增、修改的 Sub 列取值。
        internal static void Check(ArcReq req)
        {
            if (req.Kind.Name != Inventory)
            {
                return;
            }
            IList<string> tags = new List<string>(req.Fields.Tags);
            for (int i = 0; i < tags.Count; i++)
            {
                char type;
                if (Types.TryGetValue(tags[i], out type))
                {
                    req.Fields.Put(tags[i], ArcCol.Of(tags[i], type, false).Clean(req.Fields.Get(tags[i])));
                }
            }
        }

        // 新增、修改成功后在新连接上核对：调用方给的每个 Sub 列都已落库。U8 的存货 EAI 对不认的标签、被别的列的取值压住的值
        // （例如 bBatchCreate 为 0 时批次属性 bBatchProperty1–10 恒为 0）不报错，只是不写，所以逐列对一遍。
        // 不符 502 u8_fields_ignored，detail.fields 是 { 标签: { sent, stored } }；档案本身已经保存。
        // 全部落库（或不是存货）返回 null，否则返回要抛出的异常。
        internal static BridgeException Verify(ArcReq req, Dictionary<string, string> row)
        {
            if (req.Kind.Name != Inventory || row == null)
            {
                return null;
            }
            Dictionary<string, object> fields = new Dictionary<string, object>();
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                char type;
                string stored;
                if (!Types.TryGetValue(tags[i], out type))
                {
                    continue;
                }
                row.TryGetValue(req.Map.Column(tags[i]), out stored);
                string sent = req.Fields.Get(tags[i]);
                if (!ArcCol.Of(tags[i], type, false).Same(sent, stored))
                {
                    Dictionary<string, object> pair = new Dictionary<string, object>();
                    pair["sent"] = sent;
                    pair["stored"] = stored == null ? null : stored.Trim();
                    fields[tags[i]] = pair;
                }
            }
            if (fields.Count == 0)
            {
                return null;
            }
            string names = string.Join("、", new List<string>(fields.Keys).ToArray());
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["fields"] = fields;
            return new BridgeException(502, FieldsIgnored, "档案已保存，但 U8 未写入以下字段：" + names).WithDetail(detail);
        }

        static Dictionary<string, char> Build()
        {
            Dictionary<string, char> map = new Dictionary<string, char>(StringComparer.OrdinalIgnoreCase);
            Add(map, ExtraBits, 'b');
            Add(map, ExtraInts, 'i');
            Add(map, ExtraShorts, 's');
            Add(map, ExtraFloats, 'f');
            Add(map, MappedBits, 'b');
            Add(map, MappedInts, 'i');
            Add(map, MappedShorts, 's');
            Add(map, MappedFloats, 'f');
            for (int i = 0; i < BitFamilies.Length; i++)
            {
                for (int n = 1; n <= 10; n++)
                {
                    map[BitFamilies[i] + n.ToString(CultureInfo.InvariantCulture)] = 'b';
                }
            }
            return map;
        }

        static string[] BuildPairs()
        {
            List<string> names = new List<string>();
            names.AddRange(ExtraBits);
            names.AddRange(ExtraInts);
            names.AddRange(ExtraShorts);
            names.AddRange(ExtraFloats);
            for (int i = 0; i < BitFamilies.Length; i++)
            {
                for (int n = 1; n <= 10; n++)
                {
                    names.Add(BitFamilies[i] + n.ToString(CultureInfo.InvariantCulture));
                }
            }
            names.AddRange(ReadOnlyCols);
            string[] pairs = new string[names.Count * 2];
            for (int i = 0; i < names.Count; i++)
            {
                pairs[i * 2] = names[i];
                pairs[i * 2 + 1] = names[i];
            }
            return pairs;
        }

        static void Add(Dictionary<string, char> map, string[] names, char type)
        {
            for (int i = 0; i < names.Length; i++)
            {
                map[names[i]] = type;
            }
        }
    }
}
