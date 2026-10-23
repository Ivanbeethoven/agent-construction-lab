# 核心重建记录

日期：2026-10-24。版本：0.3.0。原始交付缺失的核心 Python 路径已根据公开接口、现存测试、原 README 和历史工件结构重新实现。不存在恢复原始源码字节的声明；原清单仍用来区分历史材料与重建产物。

## 实现范围

| 部分 | 当前行为 | 证据边界 |
| --- | --- | --- |
| 有限状态模拟器 | 实际执行 read/write、重试、幂等、重启恢复、schema 适配；生成并验证 Python harness | 世界与故障规则是重建实现；分数不能当真实 LLM 成绩 |
| 源学习与搜索 | 配对空编辑基线复用、ridge 预测、故障交叉拟合、greedy/beam support 搜索、分组 bootstrap | 数值学习器不是额外 LLM；原算法无法逐字恢复 |
| 固定模型客户端 | 标准库 HTTP、固定模型身份、可审计重试、单次 attempt 用量、抗冻结时间时钟 | 仅本地 HTTP 集成验证；真实服务的 seed/计费行为待核验 |
| 执行与隔离 | 工具/JSON 两种协议、限制步数、私有终态评分、AppWorld 子进程超时与凭据剥离 | AST 策略不是 OS 安全沙箱；真实 AppWorld 运行未验收 |
| 七条件研究 | G/D/S/V/F/G+R/F+R、官方 GEPA 0.1.4、query 屏障、工件及 runtime 哈希、调用归属审计 | mock 明确有脚本偏好；没有产生论文性能证据 |
| 工程入口 | 两个 CLI、配置、运行手册、Windows/Ubuntu CI、原 114 测试及额外回归 | CI 以实际运行状态为准；本地机器只验证 Windows |

`task_fixtures.json` 从原有 `runs/reference/splits.json` 提取公开 TaskSpec，不含历史成功标签；执行器每次建立并运行自己的状态世界，不回放原结果。新增输出统一使用 `runs/local*/`，不覆盖原 `runs/`。

## 复查后修复

构造与反思和 actor 同样检查 input/output/total tokens；未知不能填零或生成科研摘要。每次 run 使用唯一 AppWorld experiment 名称，避免覆盖或并发状态冲突。GEPA 最终报告采用官方完整 validation 分数，而不是最后一次 parent 重测分数。无效源记录、重复候选、重复条件、非法 seed/数值和模块超限在相应边界拒绝。

新增 generator 隔离选项与独立 support seeds，使 benchmark 计划可以明确区分同模板验收和跨模板研究。D/S 的源摘要保留领域及公开特征；超过声明长度直接失败，不静默丢弃领域。

## 尚未完成的研究工作

真实模型快照、价格与端点未配置；真实 AppWorld 包/任务数据未安装验证。SWE-bench、ScienceAgentBench 和 τ 的 adapter 仍待实现，选型文件不代表这些实验已执行。严格 AppWorld 清单须取得真实数据并完成 scenario 分组审计后建立。当前 dev 仅预留，不自动调参。

F/V 可能排序相同，不能通过更换任务或调参保证 F 获胜。小规模领域 bootstrap 的区间不支持广泛泛化结论。原缺失的历史 run、文献文件与结果图没有重新编造。原始 114 个测试内容未修改；新增边界测试单列于 `tests/test_reconstruction_guards.py`。

执行命令与结果见根目录 `RECONSTRUCTION_VALIDATION.json`。该文件记录本轮验证；原 `VALIDATION.json` 保持原样。
