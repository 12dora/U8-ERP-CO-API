# U8 CO 使用指南

账套、操作员、口令由本服务注入，body 里不要写。year、date 缺省为配置年度和今天，可在 body 里覆盖（跨年度两个一起给）。

## 写入流程
1. 解析：u8_resolve 把客户、存货、部门等名称换成编码。exact 才能直接用；ambiguous / partial 先让用户确认；none 不要猜。
2. 查结构：u8_describe type=<单据类型>（可加 op=update / op=generate source=<来源>）看能做的操作、field_refs 和 fields（当前账套模板的字段中文名 label、类型、必填、枚举值）；archive=<档案>、gl=true 同理；route=<路由> 看请求体结构。
3. 预演：u8_write dry_run=true。mode=rollback 表示 U8 真跑一遍后回滚；validate 只做了前置校验（warnings 含 validate_only）。把 docs 给用户确认。
4. 写入：同样的 body，dry_run=false。每个正式写入都会返回 idempotency_key，记下来。
5. 核对：u8_read vouchers/load（或 gl/vouchers/load、archives/get）看结果。

## 出错
- 结果 isError=true 时看 error.code、error.field（出错字段路径，如 lines.0.cinvcode）和 error.hint。
- retryable=true（busy、rate_limited、u8_license_full 等）：按 retry_after 秒后重试。
- 504 outcome_unknown：写入可能已经生效。不要直接重试：用 u8_idempotency_get（同一 route 和 key）查，或先读取核对。
- 重试同一请求时带上原来的 idempotency_key，服务只执行一次；同一个 key 不能用于不同的请求。
- state_mismatch：先 load 看单据当前状态。
- 存货核算记账、期末处理（ia/post、ia/period_end）和经过存货核算的月末结账（periods/close 的 module=ia 或 through）要跑几分钟，本服务对它们按 long_timeout_s（缺省 1000 秒）等，不要中途重试；先 dry_run 看 detail.counts。拒绝时 error.detail 可能带补充（如 uncosted）。

## 应收应付处理
- arap/transfer、merge、red_offset、exchange_gain、bad_debt 返回处理号 cancel_no：取消用 arap/process/cancel（汇兑损益用 exchange_gain/cancel），制单用 arap/process/voucher。汇兑损益、bad_debt 默认关闭（403 feature_disabled），打开后只限测试账套（403 test_account_only）；汇兑损益制单须给 pl_code。

## 读取
- 找单据：u8_read vouchers/search（type 加 code_like、partner、dept、inventory、date_from/date_to、verified 等条件；合同号等录在表头自定义项里的用 defines，如 {"define1": "HT202601001"} 或 {"define1": {"like": "…"}}）。
- 批量：vouchers/load_many（ids 最多 20，COM 读取的类型最多 5）、archives/get_many（codes 最多 20）；单项失败在该项的 error 里，不影响其他项；用 fields 投影时每项仍保留 id / code / error。
- u8_read 缺省 compact=true（去掉空值）。fields 只取需要的字段，如 fields=head.ccode,lines.cinvcode,lines.iquantity。
- 列表翻页：把上一页返回的 next 作为下一次 body 的 after。meta 很大，查结构用 u8_describe。
