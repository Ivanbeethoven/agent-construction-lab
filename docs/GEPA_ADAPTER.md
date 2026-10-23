# GEPA 适配与预算口径

重建实现位于 `agent_construction_lab/fixed_model/gepa_adapter.py`，调用已安装的官方 `gepa==0.1.4` 的 `gepa.optimize_anything.optimize_anything`，不是本仓库重新实现的贪心搜索或模拟优化器。缺少该包或版本不一致时直接失败；升级版本前需重新检查官方调用行为与预算测试。

## 输入、候选空间和模型身份

`optimize_harness(initial, evaluator, reflector, *, max_evaluations, seed=0, max_modules=2)` 接收初始 harness、support evaluator 和 reflector。初始与最终 harness 都经过同一 schema 验证：恰好包含 `schema_version`、`system_prompt`、`modules`，schema 为整数 1，system prompt 为非空文本，模块只能来自 `plan`、`verify`、`tool_docs`、`error_repair`，不得重复，且不得超过配置的 `max_modules`（全局上限 2）。

GEPA 可以修改 system prompt 和上述有限模块组合，不能引入任意执行代码、额外 API 或新的模块。与 G/F 等初始构建共用这个 harness schema。source 阶段的 11 个组合覆盖 0/1/2 模块；prompt 文本空间不是被这 11 个组合穷尽的，因此也不能声称所有方法搜索了完全相同的候选文本。

reflector 通过 study 的同一个 `OpenAICompatibleClient` 调用，与执行和构建共享固定模型配置及响应身份校验。它收到公共 support 轨迹与汇总 support 得分；终态评价细节不会作为轨迹事件传入。反馈按 `support_feedback_max_chars` 截断。query 结果不进入搜索；所有方法完成搜索、冻结并写入 query lock 后才执行 query。

## 预算不是模型调用数

`max_evaluations` / `rsi_budgets` 计的是 **candidate evaluator 请求数**。在 study 中，每个有效候选请求都会执行所属 target domain 的所有 support tasks × **support_seeds**，均使用新环境。`support_seeds` 可以配置为独立 seed 列表；省略或设为 `null` 时默认沿用 `execution_seeds`。query 使用 `execution_seeds`，因此两者应分别核对，避免增加 query 重复时无意增加优化成本。这里没有候选结果缓存，也没有跨候选复用环境状态。一次 episode 可包含多次模型请求；反思模型调用也另行记录。因此 R@4 不等于 4 次 LLM 调用，也不等于 4 个 support tasks。

对每个“R 方法 × target domain × construction seed”，计划内物理 support episode 上限为 **evaluator callback budget × support task 数 × support seed 数**；总量对方法、domain、construction seed 和预算档累加。比如预算 4、6 个 support tasks、1 个 support seed，最多为 24 个 support episodes；若仅把 support seeds 改成 3 个，上限就成为 72。实际失败和额外基础设施重试必须单独审计，不能从候选数推算已经完成的有效评分数。60 任务 pilot 的分配和总预算以 [BENCHMARK_SELECTION.md](BENCHMARK_SELECTION.md) 为准，两份通用配置不等于已注册的 pilot 协议。

官方优化器先评估 seed，消耗 1 个槽位。随后一轮最坏可能需要 parent/minibatch 评估、proposal/minibatch 评估、full validation 评估，共 3 个槽位。适配层仅在剩余槽位至少为 3 时允许开始下一轮，并保留 evaluator 入口的硬上限检查。

| 总预算 | seed 后的行为 |
| --- | --- |
| 1、2、3 | 只评估 seed，剩余预算不足以启动完整一轮 |
| 4 | 可启动一轮，最坏总共 4 次 evaluator 请求 |
| 16 | 若每轮用满 3 次，最多完成 5 轮；实际以账本为准 |

某一轮未进入 full validation 时可能使用不到 3 次，剩余槽位可能支持后续轮次。保守预留不是必须花完预算的规则，最终 `unused_evaluation_budget` 可以非零；不能用名义 R 预算代替真实计算量。

GEPA 配置为 `parallel=False`、`cache_evaluation=False`、`raise_on_exception=True`、`reflection_minibatch_size=1`、`skip_perfect_score=False`。即便 seed 得分完美，也不因完美分数自动取消预算内的反思。优化器的数值 seed 与模型请求 seed 分别记录，不能据此宣称远端模型确定性。

## 非法候选、失败和审计

非法 JSON、越界模块或非法 harness schema 消耗一个 candidate request 槽位，但不执行环境 rollout；仅向优化器返回极低的内部候选选择值及结构错误反馈。这不是 benchmark task score，也不能写入 source/query 成败标签。

有效候选的 evaluator 或 reflector 发生模型身份、网络、使用量、环境等错误时，异常向外传播。对 reflector 使用专门的内部中断包装，使 GEPA 的内部异常重试不会悄悄重复已失败的模型调用；外部重新抛出原异常。study 中止并保存失败状态和实际请求账本，不能把基础设施故障当作零分，也不会改用模拟器。

`support_searches.jsonl` 保存选中的 harness、score、`official_gepa_version` 与 audit：

- `candidate_evaluation_requests`：所有请求槽位，包括非法候选。
- `actual_evaluations`：实际完成的有效候选 support 评估。
- `invalid_candidate_evaluations`：被 schema/模块边界拒绝的候选。
- `reflection_calls`：反思调用次数；物理 HTTP 尝试细节见 `model_usage.jsonl`。
- `gepa_metric_calls`：官方优化器报告的计数，用于交叉检查。
- `unused_evaluation_budget`：名义上限减去已使用请求槽位。

support episodes 的模型尝试归 episode 所有，reflector 的尝试归对应 construction 所有。每个物理 attempt 必须且只能有一个 owner。G+R 与 F+R 分别从 G/F 冻结前的初始构建出发，基础构建成本仍须计入方法成本；公共 source 成本分摊是反事实成本口径，不可重复计入物理总费用。

## 验证

在已安装 `gepa==0.1.4` 的虚拟环境运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_fixed_gepa.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_fixed_study.py -v
```

Linux 使用 `.venv/bin/python` 替换解释器路径。第一组测试检查官方依赖、版本、评估/反思预算及失败行为，第二组通过 localhost HTTP 脚本模型审计七条件的完整流程。脚本模型的分数只能用于流程验收，不能证明 GEPA 或 F 的科研效果。完整准备步骤见 [FIXED_MODEL_RUNBOOK.md](FIXED_MODEL_RUNBOOK.md)。
