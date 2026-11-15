using System;
using System.Collections.Generic;

namespace U8Co
{
    // meta 路由的 features 扩展点：由签名自检等模块填充；此处只返回当前可公开的摘要。
    // signatures：COM 签名自检报告（按功能分组，见 SigReport）；自检未完成时只有 {"summary":"pending"}。
    internal static class MetaHook
    {
        internal static Dictionary<string, object> MetaFeatures()
        {
            Dictionary<string, object> features = new Dictionary<string, object>();
            try
            {
                features["signatures"] = SigCheck.MetaView();
            }
            catch (Exception)
            {
                Dictionary<string, object> fallback = new Dictionary<string, object>();
                fallback["summary"] = "unknown";
                features["signatures"] = fallback;
            }
            // mobile_push：config.json 的 mobilePush 实际生效值（审批时是否推送 U8 移动审批）。
            features["mobile_push"] = U8Resolve.MobilePush;
            // clean_orphan_tasks：config.json 的 cleanOrphanTasks（审批流操作后清理 QM 终审插件留下的孤儿任务）。
            features["clean_orphan_tasks"] = TaskOrphans.Enabled;
            // replicated_writes：config.json 的 enableReplicatedWrites（第二级写入总开关，缺省关闭）。
            features["replicated_writes"] = TestAccountGate.Enabled;
            return features;
        }
    }
}
