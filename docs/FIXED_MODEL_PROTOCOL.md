# 重建实现的执行协议

本文件说明 v0.3 当前代码行为，不是找回的原协议，也不代表未来 pilot 已正式预注册。领域、样本数及选择依据见 [BENCHMARK_SELECTION.md](BENCHMARK_SELECTION.md)。

1. 固定同一 HTTP 模型配置用于 actor、builder 和 GEPA reflector。真实运行要求请求模型、快照标签及响应身份非空且具体。数字回归器独立标记为非 LLM。
2. source/dev/target 领域互斥，task ID 全局唯一；提供 generator ID 时 source/dev/target 的生成器也互斥。support/query 默认可共享目标 generator 的不同变体；严格跨组实验启用 `group_disjoint_support_query=true`，并要求两者 generator 全局互斥。
3. source 每 task/seed 执行一次空编辑，所有非空候选各执行一次。空编辑结果复用，保留正、零、负配对效应。基础设施失败不会被转成失败分数。dev 不进入构造学习，当前 runner 不执行独立开发调参。
4. G 为固定通用提示；D/S 看相同的按源领域聚合的特征与配对反馈。D 可生成系统提示与模块，S 只能从固定模块组合选择。F/V 用相同 ridge 特征基函数、源行和正则化，分别拟合配对差值/最终分数；F 的预测相对空编辑中心化。固定模块组合共 11 个（0、1、2 模块），D/R 的提示文本可变，故不能说所有方法只有相同的 11 个完整 harness。
5. G+R/F+R 从相应冻结构造结果出发，仅在目标领域 support 上使用官方 GEPA 0.1.4。每个 evaluator callback 运行该 domain 的全部 support × support seeds；初始评估、parent 重测和 candidate validation 都计入实际预算。详见 [GEPA_ADAPTER.md](GEPA_ADAPTER.md)。
6. 保存每个 domain × construction seed × condition 的工件。工件包含模型配置、研究配置、公开领域画像、源配对数据和执行时代码的哈希。全部构造/优化完成后写 query_lock，再进入 query。每次执行前再次验证工件。校验可发现意外改动，但未提供数字签名或对恶意改写全部审计文件的防护。
7. query 各条件使用相同 task/seed keys；同一领域复用同一个冻结 harness，query 反馈不回灌。公开任务指令只在执行时交给 actor；私有 evaluator 最终结果由控制器取得，不成为 actor 的下一步输入。
8. 记录所有物理 HTTP attempts，包括重试和失败。严格模式要求每次调用 input/output/total tokens 完整。估计美元费用只有配置可信价格且 usage 完整时才有效；source 冷启动成本在各方法的反事实比较中分配一次，不能将各方法摊销成本再相加当真实总费用。
9. summary 报告实际 source/support/query episode 数、成功率、工具步数、失败类型、费用与领域等权配对差值。当前 bootstrap 以 domain 为簇；只有两个宽领域时区间很粗，不代替 scenario/issue 级统计设计。正式实验应在看 query 结果前确定主要比较、功效与多重比较策略。

`source_seeds`、`support_seeds`、`execution_seeds` 与 `construction_seeds` 是不同用途的轴。`support_seeds=None` 默认沿用 query 的 execution seeds，显式列表可缩小 support 预算；seed 不是独立任务样本，也不保证远端模型确定性。

失败时保留 run_status、attempt 账本与已发生 episode，并停止整次研究，不自动换模型、换任务或以 mock 补足。成功输出是管线完成信号，不能自动解释为科研假设得到支持。
