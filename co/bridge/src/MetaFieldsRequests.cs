using System.Collections.Generic;

namespace U8Co
{
    // 不带单据类型的两条读路由在 Requests.ApplyP4 里的登录前校验：幂等结果查询（IdemGet）、字段说明（MetaFieldsReq）。
    // 字段表在 RequestsP4 的 P4Specs。
    internal static partial class Requests
    {
        // 处理了返回 true。
        static bool ApplyMetaRead(Dictionary<string, object> body, WorkItem item, string path)
        {
            // 幂等结果查询：校验 route、幂等键、caller，登录子系统按原请求。
            if (path == IdemGet.Path)
            {
                item.SubId = IdemGet.SubOf(IdemGet.Parse(body).Route);
                return true;
            }
            // 字段说明：校验 type / archive / gl、op、source，登录子系统 AS。
            if (path == MetaFieldsReq.Path)
            {
                MetaFieldsReq.Parse(body);
                item.SubId = MetaFieldsReq.Sub;
                return true;
            }
            return false;
        }
    }
}
