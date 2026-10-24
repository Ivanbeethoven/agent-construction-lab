# 首轮 AppWorld 六条件同 harness 的证据诊断

诊断日期：2026-10-24。对象为首次失败运行 `001` 与完成 source 导入续跑的 `002`；执行版本 `4305451922455ca909d98efe64a261974162cd5a`。本次只分析既有 source、构造和 support，未使用 query 成绩来选择解释、改算法或挑题，也没有新增模型调用。原始证据未改写。

**六条件相同有可复现的具体原因：F/V 的全零监督使全部候选打平，S 实际选择了空模块候选，GEPA 虽提出不同文本，但支持集满分使提案不能严格改善。没有发现导致这次合并的构造器或 GEPA 选择实现错误。** 这不能证明六种算法等价，也不能证明反馈优化没有潜在收益。独立的 actor 收尾协议问题必须另外处理，不能追溯修改本轮分数。

完整数值、文件锚点和 SHA-256 见[脱敏诊断 JSON](../reports/appworld/2026-10-24-harness-diagnosis.json)；原研究范围与累计费用见[工程 pilot](APPWORLD_PILOT.md)。本文件不包含任务原文、模型回复、harness 文本、轨迹或模拟账户值。

## 1. 最终相同的是完整执行配置，不只是模块数

| 条件 | 模块 | 系统提示 | 完整 harness 哈希前 12 位 |
| --- | --- | --- | --- |
| G、F、S、V、G+R@4、F+R@4 | 空列表 | 同一通用提示 | `cc95f632b45c` |
| D | plan、verify | D 构造的新提示 | `f2376ba2370b` |

诊断核对了每份 frozen manifest、harness 内容哈希与该 run 的 runtime provenance。`001` 的 33 个 source episode、33 条配对及 source library 与 `002` 完全相同，未根据成绩筛除任何记录。源码若正在为后续新协议修改，旧 artifact 仍对 **002 记录的 runtime 哈希**验证；本诊断不调用修改后的 actor。

## 2. F/V：全零标签、恒定条件与明确的平局规则

source 只有 **1 个 generator 的 3 个变体 × 11 个候选 × 1 个 seed**，合计 33 个物理 episode。33 个官方终态分数全部为 0，33 条配对差值也全部为 0。记录的结束原因是 32 次 `step_budget_exhausted`、1 次 `invalid_action`；这说明在本轮固定预算和协议下没有成功信号，不能直接推断增加步数必然成功。

F 拟合 `score − baseline_score`，V 拟合 `score`。本轮两者的标签向量均为零。岭回归在 α=2 的正则下满足：

\[
\hat\beta=(X^\top X+\alpha I)^{-1}X^\top y=0 \quad\text{when }y=0.
\]

重建与保存的模型一致：F/V 各 33 个分数系数均为 0，每个候选预测分数及 objective 都为 0。以下表格同时适用于 F 和 V；token 差预测保留三位小数，机器报告保存完整浮点值。

| 候选 | 模块 | 预测分数差 | 预测 token 差 | objective |
| --- | --- | ---: | ---: | ---: |
| c00 | 空 | 0 | 0.000 | 0 |
| c01 | plan | 0 | −2033.243 | 0 |
| c02 | verify | 0 | −168.905 | 0 |
| c03 | tool_docs | 0 | 1272.738 | 0 |
| c04 | error_repair | 0 | −2393.128 | 0 |
| c05 | plan、verify | 0 | −1588.255 | 0 |
| c06 | plan、tool_docs | 0 | 5838.407 | 0 |
| c07 | plan、error_repair | 0 | −466.750 | 0 |
| c08 | verify、tool_docs | 0 | 5489.014 | 0 |
| c09 | verify、error_repair | 0 | 474.675 | 0 |
| c10 | tool_docs、error_repair | 0 | −2550.663 | 0 |

`cost_weight=0`，所以非零 token 预测不参与选择。实现使用 `max(predictions, key=objective)`，完全平局时返回确定性候选顺序中的第一项 **c00**。空模块因此被选择，既没有伪造增益，也没有强制回退逻辑。

公共条件特征只有两个恒为 1 的值，加 bias 后形成三份相同列；带模块主效应与交互后的 33 列设计矩阵，数值秩为 11。岭正则保证能求解，但不能从恒定特征识别跨领域条件收益。全零残差不是“没有不确定性”或“模型已学好”的证据；本轮缺少可区分候选的监督与独立任务族。

## 3. S：一次真实选择，不能补写未观察到的理由

S 有且仅有一次成功的 `construction:S` 物理调用。其已验证契约只接受候选库中的 `candidate_id`，冻结的通用提示加空模块只对应 **c00**，因此可唯一反推 S 接受的候选为 c00。

原运行未另行持久化 S 的完整原始响应或选择理由。报告明确将此标为“由冻结产物和验证契约唯一反推”，不声称模型因为省 token、保守性或特定推理而选择 c00。它与 F/V 的输出相同，但不能据此声称内部选择机制相同。

## 4. GEPA：确实提出新 harness，等分提案被拒绝

官方包固定为 **GEPA 0.1.4**。本次读取的 Windows 与实际 WSL 安装中，`optimize_anything.py`、engine、result、acceptance 与 reflective proposer 五个源码文件哈希逐项相同，已写入机器报告。adapter 使用默认 `StrictImprovementAcceptance`，条件为 `new_sum > old_sum`；最终从完整验证池取最佳候选，不用最近一次 parent 重测替代原验证分数。

每个 callback 按冻结 manifest 的完整 **3 个 support 任务 × 1 个 seed** 重建；下表中的 callback 分组、分数及哈希直接来自 episode 记录。阶段名称与“等分拒绝”由固定版本的单轮控制流重建，原 run 未保存专门的 rejection event，因此不能把它伪称为独立事件日志。

| 分支 | Callback / 重建角色 | harness 哈希前 12 位 | 三个任务分数 | objective | tokens |
| --- | --- | --- | --- | ---: | ---: |
| G+R@4 | 1：初始完整验证 | `cc95f632b45c` | 1、1、1 | 1 | 177212 |
| G+R@4 | 2：parent 重测 | `cc95f632b45c` | 1、1、1 | 1 | 168864 |
| G+R@4 | 3：反思提案 | `fa90e8daf48f` | 1、1、1 | 1 | 165501 |
| F+R@4 | 1：初始完整验证 | `cc95f632b45c` | 1、1、1 | 1 | 158836 |
| F+R@4 | 2：parent 重测 | `cc95f632b45c` | 1、1、1 | 1 | 158877 |
| F+R@4 | 3：反思提案 | `744788c13644` | 1、1、1 | 1 | 157463 |

两条分支各产生 **1 次反思、2 个不同 harness、3 次实际 callback、9 个物理 support episode**，没有无效候选。提案的哈希不同，证明反思没有被跳过；但是 `1 > 1` 不成立，提案没有进入新的完整验证，原 seed 获选，`selected_score=1.0`。

每条分支的第 4 次 callback 预算没有被消耗：一次新轮次最坏需要 parent、proposal、full-validation 共 3 个 slot，剩 1 个不够，adapter 按既定保守规则停止。**4 是 callback 上限，不是必须生成四个候选或反思四次。** `skip_perfect_score=False` 使满分 seed 仍能尝试这一轮反思，但不会改变严格改善门槛。

18 个 support episode 都实现官方终态，其中 6 个保留 `invalid_action` 收尾记录。这些协议缺陷与终态成功分开记录；它们不能事后成为新惩罚项来“制造”GEPA 改善。token 数虽有差异，本轮成本权重为 0，同样不参与 objective。

## 5. 判断与后续设计边界

| 问题 | 本轮判断 | 处理 |
| --- | --- | --- |
| F/V 拟合或平局实现错误 | 未发现；源重拟合、全 11 候选预测与冻结选择一致 | 保留 c00，不人为强制不同 |
| GEPA 未执行/误取最新 parent | 未发现；不同提案已执行，默认严格改善与原完整验证选择一致 | 保留实际未用预算与等分结果 |
| source 缺少学习信号 | 已确认：1 个 generator、恒定特征、33 个零标签 | 扩大按公开结构抽样的任务族，保持失败记录 |
| support 缺少改善空间 | 已确认：初始候选已全满分 | 预先增加结构多样性，不能按已见成功率挑“刚好可改善”的任务 |
| actor 结束契约问题 | 记录中存在，但不等于此处的数学退化 | 独立开发集校准，新版本单独跑；旧结果保持原样 |
| 构造/优化细粒度审计 | 原始 S 回复与 GEPA rejection event 未单独记录 | 后续可增加脱敏 candidate ID/hash、callback 序号及选择原因事件；这属于审计增强 |

下一轮已另行冻结按固定 SHA 排序、结果盲法选择的新任务族，排除首轮 source/support/query 及旧校准 generator。扩大为 **2 个 source generator 的 6 个任务**能改善模板覆盖，但在公共特征仍恒定时仍不识别条件化跨领域效应；不得把第二轮包装成正式方法优势实验。协议变更和新数据需要独立版本、独立目录与预先冻结的预算，不将首轮 query 用来调参数或换任务。

更完整的后续研究可按预定义公开应用类型、跨应用依赖、读写结构等分层，先固定 generator 分配及随机种子，再整组收集所有三个变体和全部候选。类别来自公开元数据/允许的开发资料，不来自 gold、评分断言、模型成功率或 query 原文。若某层不足，记录不足而非看结果替换；源标签再次全零时报告实验不可识别，不筛出成功样本伪装代表性。真正的条件效果学习还需要多个独立源域/特征向量及预先确定的画像构建规则。

## 复查方法

`scripts/diagnose_harness_collapse.py` 使用输出字段白名单，只重拟合数值模型，不联系模型或 AppWorld。它检查原始快照、source 导入一致性、候选预测、frozen hashes、callback 的完整任务/seed 顺序，并拒绝覆盖报告或写入原 run。query 行在分析前被排除，`query_results.jsonl`、`query_lock.json`、`summary.json` 不被读取。

```powershell
.venv/Scripts/python.exe scripts/diagnose_harness_collapse.py `
  --run runs/local-live/deepseek-appworld-pilot-002 `
  --original-run runs/local-live/deepseek-appworld-pilot-001 `
  --out runs/local-live/harness-diagnosis-recheck.json

.venv/Scripts/python.exe -m unittest discover -s tests -p test_harness_diagnostics.py -v
```

报告内记录了完整旧代码版本。重拟合要求 constructors、learning、spec、study、GEPA adapter 与共享序列化源码仍匹配该版本；其他新版本 actor 的实现不用于历史诊断。8 项无网络合成测试覆盖全零打平、非零信号可改变选择、GEPA 等分提案、callback 缺失/乱序、错误预测、S 调用证据、隐私字段排除与防覆盖。真实只读诊断通过，新增模型调用数为 0。
