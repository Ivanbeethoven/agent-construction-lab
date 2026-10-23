# Agent Construction Lab · 0.2

固定一个模型，在执行前按任务族构造 agent harness，并与同模型的反馈优化比较。交付包含可运行代码、精简实验规划、40条相关工作的核验目录、测试、用量账本和历史模拟结果。

**本版已接通模型 HTTP 客户端、真实 AppWorld 环境与官方 GEPA。交付时没有指定真实模型端点/快照/密钥，因此没有真实 LLM 主实验成绩。** 本地 HTTP 测试服务、真实环境接口验证和论文级实验在验收记录中分别标注。

## 先看这三个入口

- [精简实验规划](docs/FIXED_MODEL_EXPERIMENT_PLAN.md)：固定M、七条件、实际任务数量、隔离与成本协议。
- [运行说明](docs/FIXED_MODEL_RUNBOOK.md)：安装、无密钥 smoke、真实模型配置、小规模验收和正式运行命令。
- [实际验收记录](VALIDATION.json)：测试结果、依赖版本、已完成项目和仍需真实模型验收的项目。

Python 3.12 推荐；从项目根目录运行：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements-fixed-model.txt
python -m unittest discover -s tests -v
python -m agent_construction_lab.fixed_model smoke --out runs/my-smoke
python -m agent_construction_lab.fixed_model verify-run --run runs/my-smoke
```

这会经过实际 HTTP 请求和官方 GEPA，运行完整七条件；模型响应来自明示的脚本化本地测试服务。其成绩与用量只验证管线，不能用来证明方法胜率、节省比例或取代 RSI。服务只监听 localhost，无须密钥。

## 七个核心条件

| 条件 | 本包实现 | 复现边界 |
|---|---|---|
| G | 通用冻结提示 | 固定模型基础执行器 |
| D | 同M读取同源摘要直接构建 | 普通经验构建控制 |
| S | 同M在同源模块库中选择 | ModularRSI风格受控移植，不是完整官方复现 |
| V | 同数据的最终价值预测 | 与F共享函数类和候选 |
| F | 条件配对编辑效果预测 | 我们的数值构造器原型 |
| G+R | G起点＋官方GEPA 0.1.4 | 同M反思，仅support反馈 |
| F+R | F起点＋同一GEPA | 衡量剩余反馈优化空间 |

当前真实模型候选空间为系统提示和四个指导模块（plan、verify、tool_docs、error_repair，最多两个）。这是可验证的有限空间原型，没有实现任意工具代码、控制流和架构的自动发明。模块库固定，域画像为公开词法特征；这两处是后续研究扩展点。

所有目标族和条件的 harness 都在首个 query 前冻结，同一族的 query 共用同一工件。source、dev、support、query 在接口和 manifest 中分开；AppWorld split 由实际数据ID列表核验。官方 test 不能进入构造学习或反馈优化。

## 已提供的工程能力

- 一个客户端服务构建、执行和反思，固定模型配置；校验返回身份，不隐藏调用第二模型。
- 每次请求记录调用角色、返回模型、输入/输出/缓存/推理token、时间、失败重试；未知值保持null。
- AppWorld官方执行与终态评分，独立子进程、宿主超时、状态重置；不把私有评分断言送入actor或优化器。
- 同源配对采集、负/零编辑记录、同数据价值预测、四项F消融与严格的源角色检查。
- 官方GEPA预算审计：初始候选、重复parent/child、非法候选请求和未用预算均可查。
- 冻结harness及执行依赖哈希、可搬迁的工件路径、配对域统计与冷启动/摊销成本。

源经验不是免费的。全实验实际总账去重计一次；各方法冷启动对照分别分摊source成本。F/V可能同序，当前实现本身不构成“F必然更强”的证据。

## 目录

| 路径 | 内容 |
|---|---|
| `agent_construction_lab/fixed_model/` | 新的真实模型/环境/构造/GEPA/实验入口 |
| `configs/fixed_model*.json` | 唯一模型模板、验收配置、正式协议配置 |
| `tests/` | 原型、客户端、隔离、构造器、官方GEPA与端到端测试 |
| `scripts/validate_appworld_adapter.py` | 无模型的真实AppWorld接口验收 |
| `runs/fixed_model_smoke/` | 本次七条件HTTP测试运行，明确标为mock |
| `runs/appworld_adapter_validation.json` | 本次真实AppWorld接口验收摘要 |
| `runs/reference/`、`runs/budget11/` | 历史有限状态actor模拟研究，不能充当LLM结果 |
| `docs/BASELINES.md`、`docs/baseline_catalog.json` | 40条文献/框架核验与复现边界 |
| `docs/GEPA_ADAPTER.md` | 官方接口、固定版本和精确预算含义 |
| `docs/EXPERIMENT_PROTOCOL.md`、`docs/Research_Plan.docx` | 前一阶段完整研究框架，当前运行协议以精简规划为准 |
| `docs/SIMULATOR_README.md` | 旧版模拟器与外部worker接口说明 |

## 真实运行尚需配置

填写一个已确定的模型快照、兼容端点和密钥环境变量，并按官方命令下载AppWorld数据。先完成小规模真实模型验收，再运行正式配置。`doctor`会明确报告缺项并返回非零状态，绝不静默退回模拟器。

当前没有完成：真实模型七条件主表、真实模型上的修复正对照、第二强优化器/第二独立基准，也没有完整复现40套相关系统。一个M、一种R、有限模块空间与一个自定义AppWorld划分，只能支撑对应范围内的结论。

旧模拟器仍可用标准库单独运行：

```bash
python -m agent_construction_lab pilot --out runs/my-simulator
```

旧CLI的`--model`是数值选择器文件；新CLI使用`--model-config`指定LLM配置，两者不要混用。
