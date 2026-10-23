# 领域 benchmark 选型与小规模实验方案

检索与核验日期：**2026-10-24，Asia/Shanghai**。本文为选型阶段的研究方案，不是原交付文件的恢复或实验成绩。选型阶段只读取了官方论文、仓库和轻量数据集元数据；后续真实环境、任务清单与模型运行分别见 [环境验收](APPWORLD_ENVIRONMENT.md)、[9 任务工程试验](APPWORLD_PILOT.md) 和 [真实运行记录](APPWORLD_LIVE_RUN.md)。表中的 AppWorld 105/60 是论文划分；本轮实际下载数据的 train/dev ID 数为 90/57，已另行记录哈希。

## 建议与研究边界

**主基准继续使用 AppWorld；第二独立基准选 SWE-bench Verified 的科学 Python 库维护任务；ScienceAgentBench verified 作为科研数据分析探索项；τ 系列留作后续交互式客服扩展。** 这个顺序优先满足终态可客观评分、环境可重置、训练与测试隔离，并控制新增工程量。

本项目研究的是固定模型 M 下的执行前 harness 构造：G、D、S、V、F、G+R、F+R 使用相同模块词表与数量上限，R 使用同模型 GEPA。需要检验的是跨任务族的构造收益及剩余反馈优化收益，不是某个模型的榜单排名。当前模块库仅有四个提示模块及最多两个模块的组合；D/R 可额外修改系统提示，而 S/V/F 在固定模块组合中选择，完整文本搜索空间并不相同。因此结论须保留这一差异，并限于当前接口与模块库。代码重建、mock HTTP smoke 和真实 benchmark 结果必须分别记录。项目背景依据 [原 README](ORIGINAL_README.md)、[接手记录](HANDOVER.md) 与 [端到端测试](../tests/test_fixed_study.py)。

第一轮在每个 benchmark 内独立采集 source、学习构造器和评估，避免把“换了领域、任务接口、源数据”同时发生的变化解释为跨基准零样本迁移。AppWorld 学出的构造器直接迁移到软件维护，是后续单独预注册的问题。

## 官方事实与适配判断

| 基准及明确领域 | 可执行任务、重置和评分 | 划分与泛化单元 | 本项目判断 |
| --- | --- | --- | --- |
| **AppWorld：个人信息、待办、支付分账、音乐等跨应用事务** | 本地模拟应用与 API；新建任务世界恢复任务数据库与时间。以初末数据库状态的程序化测试评分，包括非预期修改。 | 原论文 250 个 scenario、每个 3 个变体，共 750 任务；train 105、dev 60、test_normal 168、test_challenge 417。challenge 涉及训练未见的 Amazon/Gmail。 | 主基准；已有 adapter 重建基础。以 scenario/generator 分组，不能把同一模板换个人名当成独立泛化。官方测试与本项目内部 pilot 分开。 |
| **τ-bench / τ²-bench：航空、零售、电信故障排查** | 用户由 LLM 模拟；后端状态可重置。τ² 的电信用户也能操作工具。评分依赖版本和 `reward_basis`，不能概括为全是数据库匹配。 | 电信有组合任务生成机制；旧版 τ² 系列的 train/test 文件可用。persona 变体不等于新任务族。 | 暂不作为首个独立验证：需新增对话式 actor、用户模拟器账本、通信评分核验。适合以后研究“构造能否减少沟通与协调失败”。 |
| **SWE-bench Verified：科学 Python 库的软件维护** | 给定 issue 与固定仓库提交，产出补丁；Docker 环境运行任务测试。主结果为解决率，不需要用 LLM 判断补丁是否成功。 | Verified 为 500 条 test 任务，含 12 个仓库；source 另从 legacy SWE-bench 的 train 取，不能拿 Verified test 做 source。 | 第二独立基准；先评估 Astropy、SymPy 等科学软件维护方向的可运行任务。需要新增文件编辑/终端工具和容器 adapter；不能直接复用 AppWorld 的 Python API 执行接口。 |
| **ScienceAgentBench verified：生信、计算化学、GIS、心理认知** | 从真实研究工作抽取任务，生成独立 Python 程序和结果文件；提供任务评分脚本与容器 harness。部分可视化由 GPT-4o 评分。 | 原基准 102 任务来自 44 篇论文；当前 verified 元数据仍为 102 条。应按原论文、原仓库和数据源分组。 | 科研相关性最强，但需要先筛出确定性评分任务、审查数据依赖与样本数。不能把全量当成无额外模型裁判的主基准。 |

上述 AppWorld 规模与 challenge 定义来自[论文 §3.4](https://arxiv.org/html/2407.18901v1#S3.SS4)，重置与评分来自[官方使用说明](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md)。τ 的任务与用户交互定义见[原始论文](https://arxiv.org/abs/2406.12045)及[τ² 论文](https://arxiv.org/abs/2506.07982)。SWE-bench 机制见[官方仓库](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/README.md)与[Verified 数据卡](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/78f471bf655a3137b2e8a75af1501690ec009ec3)。ScienceAgentBench 依据[ICLR 2025 论文](https://arxiv.org/abs/2410.05080)及[官方 README](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/README.md)。表中“本项目判断”为基于这些事实的研究选择。

### AppWorld 的两个不同实验问题

**内部 pilot：有同领域 support 的任务族泛化。** 从官方 train/dev 中构造互斥的 source、内部 dev、support、query。内部 query 只是我们保留的开发数据，不可称为官方 test 成绩。宽领域可先考虑个人信息/待办、个人财务/分账；具体是否有足够独立 scenario，须由实际数据清单核验，不能凭关键词凑数。

**当前自动准备工具仅供同生成器验收。** 重建代码的 `prepare-appworld` 将每个 generator 作为一个 domain，再把同一 generator 的不同变体分到 support/query。它可以验证加载、执行、重置与冻结流程，衡量的是 **within-generator 的变体迁移**；它没有实现本方案所要求的“同宽领域、不同 generator”泛化。不能把自动生成的 manifest 直接当成下面的严谨 pilot 清单。[当前准备实现](../agent_construction_lab/fixed_model/environments.py)

严谨 pilot 需要在下载并审计真实数据后，人工建立宽领域 profile 与 generator 到宽领域的映射；一个宽领域应包含多个独立 generator。随后将完整 generator 组分到 source/dev/support/query，保存真实任务 ID、原始 split 和哈希。manifest 必须启用 `group_disjoint_support_query=true`：所有相关任务明确提供 `generator_id`，且 support 与 query 的 generator 集合在**所有 domain 合并后**仍无交集，不能通过改写 domain 名称绕过检查。这一开关检查 support/query 的严格隔离，不能替代 source/dev 与其他角色的完整组级审计。本次尚未下载 AppWorld 数据，因此没有填写或推测任何 AppWorld task ID。

**正式评估：遵守官方 test 的冻结评测。** 官方 test_normal/test_challenge 不进入构造学习、GEPA 或手工错误分析。尤其 Amazon/Gmail 在官方 challenge 中是未见应用，train/dev 里不能自然获得同应用的目标 support。若 G+R/F+R 只在已见应用的 support 上优化，应报告为“源支持集优化后迁移”；不能据此推出“目标反馈优化没有用”。若另造 Amazon/Gmail support，就改变了官方未见应用设定，必须单列自定义实验。官方[开发限制](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md#no_entry_sign-agent-development-restrictions)与[论文划分](https://arxiv.org/html/2407.18901v1#S3.SS4)是这一区分的依据。

本方案的跨生成器实验要求 scenario 的三个变体整体放入同一个数据角色；前述同生成器验收是另一种范围更窄的设置。只以任务 ID 不重复为标准不足以排除模板泄漏。官方[任务生成器指南](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/guides/developing_new_task_generators.md)解释了 scenario、生成器、初始状态、验证解与评分之间的关系。分组元数据只供数据管理员使用；测试难度、解法所用 API、私有评分断言不能成为构造器画像。

### τ 系列必须冻结具体版本

原 [tau-bench 仓库](https://github.com/sierra-research/tau-bench)已明确提示其航空/零售任务过时。`tau2-bench` 这个仓库名也不再唯一指向 2025 年 τ² 论文版本：当前已有 τ³ 组件；**v2.0.0 于 2026-10-20 UTC 发布**，官方声明评分与 1.x 不可直接比较，并建议需要 1.x 可比性的研究使用 v1.0.1。[官方 v2.0.0 发布说明](https://github.com/sierra-research/tau2-bench/releases/tag/v2.0.0)

如果后续接入，建议第一轮固定 **v1.0.1 / `fc0055dc4e0a316c3f83133267fbd6faaa770992` 的文本模式**，仅 airline/retail/telecom。该版本的划分文件核验为：airline train/test = 30/20，retail = 74/40，telecom = 74/40；telecom 另有 `small`、`base`、`full` 视图，不能把重叠视图当成额外独立样本。[airline 清单](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/data/tau2/domains/airline/split_tasks.json)、[retail 清单](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/data/tau2/domains/retail/split_tasks.json)、[telecom 清单](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/data/tau2/domains/telecom/split_tasks.json)

v1.0.1 的通信评分使用字符串匹配，而当前主分支默认改为语义 LLM 裁判。即使固定 v1.0.1，仍须对所有入选任务核验 `reward_basis`，选择 `EvaluationType.ALL`，并确认没有需要 NL_ASSERTION 的评分项；不能让诊断性自然语言检查悄悄增加裁判调用。参考[固定版本通信评分代码](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/src/tau2/evaluator/evaluator_communicate.py)、[固定版本评分调度代码](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/src/tau2/evaluator/evaluator.py)与[当前评分文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)。

用户模拟器也是付费模型调用。严格“只有一个模型身份”的实验应让用户模拟器同样使用 M，并独立记录其角色、提示、采样配置与费用；若另用固定用户模型 U，研究表述必须改为“被比较的构造/执行/反思模型固定为 M，环境用户模型 U 固定”。不能遗漏 U。`solo_mode` 会改变双控制任务的含义，只能作为消融。后续最小 pilot 可做 retail train 内 source 12、dev 6、support 6，query 从 test 预先抽 12；先完成两条对话的重置/计费核验，再运行七条件、两次重复的 168 个 query episode。重复试验报告 pass^k 时，要和“至少成功一次”的 pass@k 区分；原 [τ-bench 论文](https://arxiv.org/abs/2406.12045)讨论的是可靠性 pass^k。

### ScienceAgentBench 的轻量元数据审计

2026-04-30 的官方更新要求使用 `verified` split 与 `benchmark_verified.zip`；仓库评分 CLI 的默认 split 仍可见 `validation`，因此接入时必须显式传 `verified` 并对齐工件版本。[更新说明](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/README.md)、[评分入口](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/evaluation/harness/run_evaluation.py)

本次读取官方 Hugging Face `default/verified` 的 102 行元数据，仅统计领域、输出后缀、原仓库和任务 ID；没有下载原始科研数据或参考程序。数据 revision 为 `9c6e96c9e74572e979b0930ee735041cef528cb7`。[固定数据版本](https://huggingface.co/datasets/osunlp/ScienceAgentBench/tree/9c6e96c9e74572e979b0930ee735041cef528cb7)、[官方行数据 API](https://datasets-server.huggingface.co/rows?dataset=osunlp%2FScienceAgentBench&config=default&split=verified&offset=0&length=100)

| 领域 | 全部任务 | 非 PNG 输出候选 | 需要注意 |
| --- | ---: | ---: | --- |
| Computational Chemistry | 20 | 11 | 不少为深度学习；同一 deepchem 来源出现多次 |
| Geographical Information Science | 27 | 3 | 其中 1 个为 TIF；不能直接算成数值任务 |
| Bioinformatics | 27 | 11 | 多个任务共用 CAMDA-DILI 或 DeepPurpose 等来源 |
| Psychology and Cognitive science | 28 | 13 | NeuroKit、BioPsyKit、cogsci-jnmf 等来源有多条任务 |

所以仅有 **37 条非图像输出候选，加 1 条 TIF**；“非图像后缀”并不证明评分脚本没有 LLM、样本不重叠或任务能在 CPU 上低成本完成。跨领域也会共享源仓库，例如 deepchem 与 BioPsyKit，因此只按 `domain` 分组仍会泄漏。上述数字是本次脚本对公开元数据的计算结果，不是官方声称的确定性评分子集规模。

可以先审查数值任务候选 ID 21/87（GIS）、29/34/35（NeuroKit）、37/44/45（BioPsyKit）、58/60（syllogistic-nvc）。这些仅是环境核验候选，**不是已验证可运行或已分配给最终 query 的任务**。同源组不能拆开。第一轮只执行 6 个开发任务做工件重置、金标准与错误输出对照；确认足够独立组后，再考虑 source 8、dev 4、support 4、query 8 的自定义 24 任务 pilot。数量不足则缩小结论或换基准，不以同论文变体补足样本。

默认只用程序可执行率与原任务脚本的成功率；不得把与 gold 程序的 CodeBERT 相似度当成科学正确性。要先审查评分函数是否真的验证输出目标、容差是否合理、错误结果能否被拒绝。[官方评分实现](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/compute_scores.py)展示了不同评分分量及输出文件检查。

## 建议的两个 pilot

以下数量是**执行计划上限与分配目标**，不是已经选定的任务清单。只有数据审计生成不可变 ID manifest、满足角色与分组约束后才可以启动。抽样用固定种子 20261024；按元数据与环境可用性选择，禁止按 M 的 query 成功率选择。

### Pilot A：AppWorld，先验证研究管线

| 角色 | 任务分配目标 | 可见性与用途 |
| --- | --- | --- |
| source | 官方 train 中 6 个 scenario × 3 变体 = 18 任务 | 四模块、最多两模块共 11 个候选；每任务同种子执行一次每候选，包含空编辑基线，物理 episode 共 198 |
| 内部 dev | 官方 train 中另外 2 个 scenario × 3 = 6 任务 | 预留给独立、预注册的构造器/超参数选择；最多全候选验证 66 episode，不并入 source 配对训练标签。当前 `run_study` 保留这些任务但不执行 dev 调参 |
| support | 官方 dev 中 4 个 scenario × 3 = 12 任务；两个目标宽领域各 6 任务 | G+R/F+R 各使用同一 support；每个“方法×目标领域”最多 4 次 evaluator callback，每次 6 任务 × 1 个 support seed，即最多 24 个物理 episode，合计最多 96 |
| 内部 query | 官方 dev 中另外 8 个 scenario × 3 = 24 任务；两个目标宽领域各 12 任务 | 七条件 × 24 任务 × 2 执行重复 = 336 episode；query 前冻结每个目标领域的全部七个 harness |

60 个分配任务中有 6 个 dev 任务仅预留。当前主运行的计划上限为 **198 source + 96 support + 336 query = 630 个物理环境 episode**；若另行执行预注册的 dev 开发步骤，最多增加 66 个，总开发与评估预算才是 **696 个**。这两个数字均不是已经运行的结果，也不等于模型请求数；一个 episode 可含多轮请求。基础设施重试须单独预注册、计费并记录，不能隐藏在成功 episode 的统计之外。

本 pilot 的配置要求是 `source_seeds=[101]`、`support_seeds=[101]`、`execution_seeds=[101,102]`、`construction_seeds=[17]`、`rsi_budgets=[4]`。`support_seeds` 为可选项，`None` 默认沿用 `execution_seeds`；此处必须显式设为 `[101]`，否则两个执行 seed 会使 support 上限从 96 增至 192，主运行上限相应变为 726。若服务端不保证 seed 确定性，记录这一限制，不能把两次结果视为精确可重复配对。

**GEPA 的 `rsi_budgets=4` 表示 evaluator callback 次数，不是物理 episode 数。** 当前研究 runner 每次 callback 执行本 domain 的完整 support × `support_seeds`，所以每个方法/领域最多 `4 × 6 × 1 = 24` 个物理 episode。初始候选及重复 parent/child 评估只要触发 callback 就消耗预算；一次反思或一个新候选可能对应不同的 callback 消耗，不能把四次 callback 解释成四次反思或四个不同候选。实际调用次数、支持任务展开数、未用预算与失败尝试都要以 adapter 审计和 episode 账本核验；source 的空编辑复用基线不能重复收费。[研究 runner](../agent_construction_lab/fixed_model/study.py)

两个目标领域必须同时有不同 scenario 的 support/query；若实际元数据无法满足这个目标，应在看模型结果前调整并保存新 manifest，而不是跨角色拆同一 scenario。首轮只检查失败类型、成本口径、冻结和统计能否正确运行，样本不足以支持论文级优越性结论。

正式阶段在冻结完整研究方案后，再对官方 test_normal 和 test_challenge 分别报告。完整运行三个变体才可按官方定义计算 SGC；只抽部分变体时报告 task success 与明确的子集分母。主比较提前限定为 F−D、F−S、F−V、(F+R)−F、(G+R)−G，以及相同总成本下的表现，避免事后挑最好的条件。

### Pilot B：SWE-bench Verified，验证独立领域

优先考察 **Astropy 与 SymPy** 两个目标库：前者代表科学计算中的坐标/单位等接口，后者代表符号计算。它们是待元数据和环境核验的选择，不是已完成复现的结果。source 选择其他仓库的训练任务；目标库的 support 仍取训练任务，使“跨仓库 source 学习、目标仓库少量 support、未见 issue query”含义明确。

官方数据源需要区分：截至本次核验，legacy [`princeton-nlp/SWE-bench`](https://huggingface.co/datasets/princeton-nlp/SWE-bench/tree/e48e2bd1e9fecd5bbd641e9414ac59da9f2e69f6)具有 train/dev/test；当前 `SWE-bench/SWE-bench` 数据集服务只列出 dev/test，不能把组织别名替换当成完全相同的训练来源。[legacy split API](https://datasets-server.huggingface.co/splits?dataset=princeton-nlp%2FSWE-bench)、[当前 split API](https://datasets-server.huggingface.co/splits?dataset=SWE-bench%2FSWE-bench)

计划 source 6 条训练 issue、内部 dev 2 条、support 4 条目标库训练 issue、query 12 条 Verified test issue（每个目标库 6 条）。source 全 11 候选共 66 episode；每个 R 方法×目标库最多 12 个 support episode，合计 48；query 七条件两次重复共 168；主运行计划上限为 **282 个环境 episode**。独立 dev 开发步骤最多增加 22 个，总预算才是 **304 个**，当前 `run_study` 不自动执行该 dev 步骤。若 SWE adapter 沿用完整 support callback 语义，每库 2 个 support、`support_seeds=[101]` 时，这一上限对应 `rsi_budgets=[6]`，不能直接沿用 AppWorld 的 `[4]` 却声称有 12 次评分。先做 2 条非最终 query 的环境校准，再做这轮实验。若 legacy train 的环境无法用固定任务镜像复现，第二基准仍标为未就绪，不能用 Verified test 临时填 source。

独立于 agent 工具视野的容器负责评分：actor 只见 issue、基准提交的文件和允许的开发测试；gold patch、test patch、评测测试列表、未来提交历史与评测脚本不挂载到 actor。允许 agent 运行原仓库开发测试，不允许用最终隐藏评分来反复调 query。每次运行从相同 base commit 和全新工作目录开始，隔离 stdout、修改和依赖；评分 `run_id` 包含 condition、seed、task、harness hash，避免缓存复用别的方法结果。官方说明特别指出结果缓存只按 `run_id/instance_id` 识别，换补丁却沿用 run_id 会误用旧结果。[当前运行说明](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/README.md)

该 pilot 报告为“预注册的 Verified 科学软件子集”，不是全量 500 题官方成绩。以 issue 为统计单位，并按仓库给出结果；两个仓库只能支持两个目标库上的观察，不能泛化为所有软件领域。

## 所有基准共用的隔离、统计与验收要求

1. **数据角色落盘。** manifest 记录 `benchmark/version/task_id/group_id/domain/original_split/role/input_hash`。AppWorld 的 group 是 scenario，并用明确的 `generator_id` 供严格验证器检查；τ 电信是去除 persona 后的故障组合/生成器族；SAB 是原论文、源仓库与数据集的连通组；SWE 是 issue、对应修复 PR/重复 issue 关系。本方案的跨组实验要求任务与组的角色交集均为零；`prepare-appworld` 的 within-generator 验收需单列，不能声称满足此条件。宽领域相同可服务于同领域泛化，但必须明示。
2. **构造器只用许可信息。** source 生成配对编辑经验；dev 如用于调参，须由独立的预注册开发步骤执行，当前 `run_study` 不读取它生成调参结果；support 仅供 R。目标宽领域的画像来自预先声明的公开描述或 support，不能先把 query 全集读成摘要来指导构造。query 指令只在执行已冻结 harness 时对 actor 可见。
3. **全部条件一起冻结。** 第一次 query 前写入候选库、构造器、提示、环境/数据/依赖、预算与模型身份的哈希。每个领域的 query 复用同一个冻结工件。query 后失败日志不回灌到 constructor 或 GEPA，不跨任务保留模型记忆或工作目录。
4. **反馈权限一致。** 训练与 support 的合法运行反馈可以使用；私有评分源码、gold solution 和测试断言不交给 actor/优化器。评分器位于独立进程或容器。只看项目日志的字段名不足以证明没有泄漏，应检查实际模型请求与挂载路径。
5. **失败不能伪装成能力标签。** HTTP 身份不匹配、环境起不来、数据缺失、评分器异常是 infrastructure failure，保留费用与重试，不能写成 F 的负编辑标签。任务内超步数与明确业务失败按预注册规则处理。失败率和有效分母一起公开。
6. **以任务/组为样本。** 各条件使用相同 query 任务与执行重复。按 scenario/issue/原论文等相关性单元配对统计；任务、变体、随机种子和 token 都不是可任意累加的独立样本。首轮报每条件成功率、差值与不确定区间，不把很小 pilot 的显著性当正式发现。正式功效与样本数在开发数据上确定，并预先处理多个主要比较。
7. **成本按真实调用计。** ledger 分构造、source、dev、support、query、用户模拟、评分、失败重试；环境 CPU/GPU/镜像成本另列。报告 source 的冷启动成本及明确 N 次部署的摊销公式。未知 token/价格留空，不能从 mock 推真实费用。保留 F/V 排序一致的可能，不能通过挑选 benchmark 保证 F 胜出。
8. **真实就绪门槛。** 固定一个模型快照、端点与返回身份；完成无模型环境重置与评分对照；确认同一状态重复评测一致；用非 query 校准任务验证正确方案成功、空方案/破坏方案失败；验证基础设施故障不会混入训练标签；再完成七条件真实小跑和冻结核验，最后才扩规模。

对预算先用少量训练任务测得每 episode 的输入/输出/推理 token、时间分布，再乘上述计划上限并加入明确的重试额度。没有指定模型与单价前，不给出美元或人民币总价。

## 安装、资源与公开仓库边界

| 项目 | 官方已核验要求 | 本项目执行选择与尚未核验项 |
| --- | --- | --- |
| AppWorld | 当前 README 为 Python 3.11+，需安装包、解包代码、下载数据，提供 Docker server。 | 延续本项目 Python 3.12；优先独立 Linux/WSL2 worker，Windows 主机做调度。本次未证明 Windows 原生环境可用；实际包版本与数据版本须同时冻结。[官方安装说明](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md#floppy_disk-installation) |
| τ v1.0.1 | `requires-python >=3.12,<3.14`；MIT；文本运行需要模型服务。 | Python 3.12 独立环境；先关闭非必要的语音/知识检索组件。Windows 兼容性与用户模拟成本未实测。[pyproject](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/pyproject.toml)、[许可证](https://github.com/sierra-research/tau2-bench/blob/v1.0.1/LICENSE) |
| SWE-bench | Docker；官方建议 x86_64、120 GB 可用磁盘、16 GB RAM、8 核；ARM 支持为实验性。 | 先 Linux 容器、1 worker、两任务校准；不要把全量资源建议误认为小 pilot 一定占用 120 GB。镜像可获取性、legacy train 的构建脚本兼容性待测。[官方资源说明](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/README.md) |
| ScienceAgentBench | README 示例 Python 3.10；推荐 Docker。官方容器文档估计运行时磁盘约 `5 + workers × 25` GB，缓存全部 instance 镜像可能达 2,600 GB。 | 单独 worker，不强行和本项目 3.12 主环境混装。先 1 worker、`cache_level=base`；文档资源数字来自原版评测，verified 的实际开销仍需重测。[容器文档](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/docs/20250110_docker/README.md) |

**MIT 项目许可不会覆盖第三方 benchmark。** AppWorld 的公共通用代码为 Apache-2.0，受保护的任务/应用/API 部分另有公开再分发须加密的要求；只在本仓库放 adapter、下载说明、ID/哈希与适当聚合指标，不提交解包数据、私有评分程序或包含受保护原文的明文轨迹。[AppWorld 许可说明](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/README.md#lock_with_ink_pen-license)

SAB 代码为 MIT，多数任务为 CC BY 4.0，rasterio/matminer 来源保留各自许可；SWE-bench harness 标为 MIT，但上游被测仓库的代码仍应遵循原许可。下载、运行与公开再分发是不同的操作；本轮选型不把第三方任务包复制进本项目。[SAB 许可说明](https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/README.md)、[SWE-bench 许可](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/LICENSE.md)

## 接下来的可并行工作包

| 工作包 | 子 agent 可交付物 | 完成标准 |
| --- | --- | --- |
| AppWorld 数据与环境 | 本地版本/数据清单、角色 manifest、reset/evaluate 日志 | 非 query 校准对照通过，scenario 无跨角色，发布工件不含受保护原文 |
| 科学软件维护 adapter | 隔离的终端/文件接口、固定容器与评分连接、2 条校准任务报告 | base commit 可复位、gold/测试补丁不暴露、错误补丁失败、缓存不跨条件复用 |
| SAB 评分资格审计 | 37 个非图像候选的原论文/数据分组、是否调用裁判、依赖/资源/许可表 | 逐条判定后才决定是否存在足够任务；不得仅用后缀自动确认入选 |
| 实验协议审计 | 七条件预算表、模型角色账本、query 屏障与泄漏检查 | 对实际请求和磁盘权限做检查；mock 和真实结果分目录标记 |

这些是代码重建后可派发的任务，不表示本次已经运行它们。当前已完成的是选型和官方资料核验。仍未知：最终模型快照与费率、本机 Docker/WSL2 与磁盘条件、实际 AppWorld scenario 领域分布、legacy SWE train 任务的容器可复现性、SAB verified 具体评分脚本的确定性与合格任务数、公开预训练语料对所选任务的污染程度。最后一项无法通过本项目的 split 检查完全消除，只能记录模型数据边界并采用去重、冻结与后续新任务验证。

## 检索记录与版本锚点

使用 `nature-academic-search` 的 `multi-source-search` 流程。学术 MCP 的 `search_papers/search_arxiv` 未挂载；技能预检中 PubMed、CrossRef、arXiv 端点均可达。OpenAlex 回退检索 `AppWorld benchmark` 返回 HTTP 429，因此改用 arXiv/ACL/OpenReview 官方论文、作者 GitHub 仓库和官方 Hugging Face 元数据，不依赖第三方排行榜转述。以 DOI/arXiv ID 去重；同一论文的仓库、数据卡是补充证据，不计为新论文。一次 AppWorld v2 HTML 地址返回 404 后改读实际存在的 v1；GitHub tags 路由不受连接器支持，改用官方 releases 和 commit 接口核验版本。

| 去重后的核心论文 | 年份/来源 | 标识与本文用途 |
| --- | --- | --- |
| Trivedi et al., *AppWorld: A Controllable World of Apps and People for Benchmarking Interactive Coding Agents* | ACL 2024 | [DOI 10.18653/v1/2024.acl-long.850](https://doi.org/10.18653/v1/2024.acl-long.850)：可控执行、任务生成器、终态与 collateral damage 评分 |
| Yao et al., *τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains* | arXiv 2024 | [2406.12045](https://arxiv.org/abs/2406.12045)：客服领域政策、用户模拟与可靠性 |
| Barres et al., *τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment* | arXiv 2025 | [2506.07982](https://arxiv.org/abs/2506.07982)：电信双控制与组合生成任务 |
| Jimenez et al., *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* | ICLR 2024 | [OpenReview](https://openreview.net/forum?id=VTF8yNQM66)、[2310.06770](https://arxiv.org/abs/2310.06770)：issue 到补丁的执行评测；Verified 是后续数据子集 |
| Chen et al., *ScienceAgentBench: Toward Rigorous Assessment of Language Agents for Data-Driven Scientific Discovery* | ICLR 2025 | [2410.05080v3](https://arxiv.org/abs/2410.05080v3)：四科学领域、独立程序输出与科研任务评分 |

| 对象 | 本次读取的版本锚点 |
| --- | --- |
| AppWorld 文档代码 | [`42b5bcf3cd334fee33f0c37c02070a9f5807add5`](https://github.com/StonyBrookNLP/appworld/commit/42b5bcf3cd334fee33f0c37c02070a9f5807add5)；不代表已经选定本项目运行包版本 |
| τ 后续接入候选 | [`v1.0.1 / fc0055dc4e0a316c3f83133267fbd6faaa770992`](https://github.com/sierra-research/tau2-bench/commit/fc0055dc4e0a316c3f83133267fbd6faaa770992) |
| SWE-bench harness 文档 | [`02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`](https://github.com/SWE-bench/SWE-bench/commit/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e)；当前 v5 CLI 与 legacy 入口均需做实际兼容性核验 |
| ScienceAgentBench 文档代码 | [`c26e151ed601ba109dc4d35e057ff8e73fec469d`](https://github.com/OSU-NLP-Group/ScienceAgentBench/commit/c26e151ed601ba109dc4d35e057ff8e73fec469d) |

执行 manifest 还必须补齐数据工件 SHA-256、镜像 digest、任务 ID 列表、依赖 lock 和评分配置；文档链接或可移动的 `latest` 镜像标签不能替代这些信息。
