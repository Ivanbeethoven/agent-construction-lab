# Agent Construction Lab

固定同一个模型，研究能否在执行前依据任务领域特征构造有效的 agent harness，以及构造之后还需要多少反馈优化。

**v0.3：缺失核心已重建，收尾协议 v2 已通过第二轮真实 AppWorld/DeepSeek 试验。** 第二轮完成 105 个研究 episode（66 source、18 support、21 query）；query 官方成功 **16/21**、健康且显式结束 **20/21**，其余一次用尽步数。全轮没有协议违规、格式纠正或自动收尾。含独立校准共 **1,687 次调用、11,540,530 tokens、估算 0.297229299 USD**。见 [第二轮公开结果](reports/appworld/2026-10-24-deepseek-round2/README.md)、[选题与执行计划](docs/APPWORLD_ROUND2.md) 和 [验证记录](APPWORLD_ROUND2_VALIDATION.json)。这是一份重建实现，不是找回的原源码；mock 分数不能作为科研成绩。

第二轮得到三种最终 harness：D 独立；F/V/S/F+R 使用相同的 `plan + error_repair`；G/G+R 使用相同通用方案。F/V 的 11 个候选已不再全平分，两者唯一最高分均为 c07；两个 GEPA 分支的提案都没有胜过重测的父方案。同 harness 的成绩差异不能归因为方法改善。任务组、步数和协议均与首轮不同，跨轮成功率也不能用于估计协议修复的单独因果效应。

[首轮结果](reports/appworld/2026-10-24-deepseek-small/README.md) 原样保留：72 个研究 episode，query 成功 10/21、正常结束 0/21，含预检与校准共 827 次调用、估算 0.150052395 USD。[六条件相同的诊断](docs/APPWORLD_HARNESS_DIAGNOSIS.md) 确认源标签全零导致 F/V 全平分，支持集满分使 GEPA 拒绝等分提案；未发现构造选择代码缺陷。两轮合计 2,514 次调用、16,099,504 tokens、估算 **0.447281694 USD**。这些小规模工程结果不支持方法优越性结论。

接手时原清单 6,393 个文件中有 693 个缺失，包含 23 个核心 Python 文件；原 ZIP 也不含这些文件。历史材料原样保留，见 [接手审计](docs/HANDOVER.md)、[重建说明](docs/RECONSTRUCTION.md) 和 [本轮验证记录](RECONSTRUCTION_VALIDATION.json)。

## 快速运行

使用 Python 3.12，在项目根目录执行：

```bash
python -m venv .venv
# Windows: .venv/Scripts/activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e . -r requirements-smoke.txt
python -m unittest discover -s tests -v
python -m agent_construction_lab.fixed_model smoke --out runs/local-smoke/check-001
python -m agent_construction_lab.fixed_model verify-run --run runs/local-smoke/check-001
construction-lab pilot --config configs/pilot_quick.json --out runs/local-pilot/check-001
```

smoke 启动本地脚本 HTTP 服务，实际执行环境、官方 GEPA、冻结校验和调用账本，无需 API key 或 AppWorld 数据。每次使用新的空输出目录。GitHub Actions 在 Windows / Ubuntu、Python 3.12 上运行同一套检查。

真实模型接入、无费用预检和 AppWorld 准备步骤见 [运行手册](docs/FIXED_MODEL_RUNBOOK.md)。`configs/fixed_model.example.json` 仅含占位值；将真实配置保存为被 Git 忽略的 `configs/fixed_model.local.json`，凭据从环境变量读取。

## 研究条件

| 条件 | 含义 |
| --- | --- |
| G | 通用冻结提示 |
| D | 同一模型读取同源经验摘要后直接构建 |
| S | 同一模型从同源模块库选择 |
| V | 预测候选的最终价值 |
| F | 预测条件配对编辑的效果 |
| G+R | 从 G 出发进行官方 GEPA 反馈优化 |
| F+R | 从 F 出发进行相同预算的 GEPA 反馈优化 |

模块为 plan、verify、tool_docs、error_repair，每个 harness 最多两个。source 用于配对学习，support 只供 R，所有目标条件在 query 前一起冻结。HTTP 重试、构造和反思调用均记账；基础设施错误不会变成训练负标签。详见 [当前执行协议](docs/FIXED_MODEL_PROTOCOL.md) 与 [GEPA 预算定义](docs/GEPA_ADAPTER.md)。

F/V 采用相同特征空间和正则化，可能选出相同候选；当前工程验证不能证明 F 更优。D 和 R 可修改系统提示，S/V/F 在固定模块库中选择，报告时应保留这一搜索空间差异。

## 领域 benchmark 与实验规划

[选型与 pilot 方案](docs/BENCHMARK_SELECTION.md) 核验了官方论文、版本、数据划分、评分方式和资源要求：

- **主基准：AppWorld**，考察跨应用事务与任务族泛化。
- **独立验证：SWE-bench Verified 的科学 Python 库维护任务**，优先评估 Astropy / SymPy；容器 adapter 待实现。
- **探索：ScienceAgentBench verified**，先筛选能确定性评分且数据组独立的科研任务；τ 系列保留为后续交互扩展。

新 AppWorld pilot 拟分配 60 个任务，主运行上限 630 个环境 episode，独立 dev 开发另留 66 个；这不是模型调用次数，也不是已完成结果。参数见 [pilot 建议配置](configs/appworld_pilot_proposal.json)。真实任务清单还需数据审计，不填写虚构 ID。

在扩大到上述正式 pilot 前，已准备 [9 个真实任务的工程试验](docs/APPWORLD_PILOT.md)：source/support/query 各一个完整 generator，严格隔离，共 7 条件、至多 78 个 episode。公开 [任务 ID 与哈希](manifests/appworld_small_pilot.json)，完整文案留在本地；使用当前数据中的 train 90/dev 57 条 ID。它只验证链路，不支持跨领域优越性或统计显著性的结论。

随后完成的[第二轮](docs/APPWORLD_ROUND2.md)盲选 12 个新任务，并排除首轮全部题组：source 两个 generator 共 6 题、support/query 各 3 题，另留独立 train 校准。配置为 [appworld_round2.json](configs/appworld_round2.json) 与 [deepseek_flash_round2.json](configs/deepseek_flash_round2.json)，公开[任务清单](manifests/appworld_round2.json)和[第二轮构造诊断](reports/appworld/2026-10-24-round2-harness-diagnosis.json)。所有域仍共用常量特征，query 仍只有一个 generator 的三个相关变体；增加源题组不构成条件迁移效应的证据。

默认 `prepare-appworld` 是同 generator 不同变体的验收划分。跨 generator 的严格 pilot 必须建立宽领域清单，并设置 `group_disjoint_support_query: true`；不能直接用默认清单声称跨任务族泛化。

## 历史证据与许可

[原始 README](docs/ORIGINAL_README.md)、[原 VALIDATION](VALIDATION.json)、[原文件清单](PACKAGE_MANIFEST.json) 和原有 `runs/` 作为历史材料保留，不由新实现重新背书。`scripts/audit_delivery.py` 比较的是原交付字节；重建文件不会匹配原哈希，剩余缺失历史结果也未伪造。

[MIT License](LICENSE)。外部依赖和 benchmark 数据遵循各自许可，本仓库不包含 AppWorld 数据、私有评分代码或模型凭据。`requirements-tested-linux-py312.txt` 是原交付环境记录；本轮最小验证环境使用 `requirements-smoke.txt`。
