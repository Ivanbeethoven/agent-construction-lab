# 第二轮 AppWorld 独立任务清单与执行计划

**状态（2026-10-24）：独立协议校准与主试验 `deepseek-appworld-round2-001` 均已完成，公开导出校验通过。** 主试验使用提交 `549ba88374181ec861bc80b99c75b9eb4a31cb00`，实际执行 105 个 episode；[公开结果](../reports/appworld/2026-10-24-deepseek-round2/README.md)及文末记录成绩、执行健康和费用。预算章节保留预先声明的设计上限，与实际执行数区分。选择 seed `20261025` 是固定抽样参数，不表示实际运行日期。

本轮承接[第一轮工程验收](APPWORLD_PILOT.md#2026-10-24-已执行结果与审计)发现的结束协议问题，使用全新的任务 generator 检查新协议。第一轮记录保持原样；本轮不是对第一轮 query 重新评分，也不导入其 source 学习数据。新旧任务组隔离不等于证明模型训练数据从未包含 benchmark。

## 固定选择及隔离

只读取官方 `train.txt`、`dev.txt` 的 ID，不读取官方 test 列表或内容。现有安装的官方 ID 数量为 train 90、dev 57。先永久排除已接触的四个 generator：`82e2fac`（旧校准）、`afc0fce`（旧 source）、`6c2c621`（旧 support）、`4ec8de5`（旧 query）。

对剩余 generator 按 `SHA256("appworld-engineering-round2-v1:20261025:<official_split>:<generator_id>")` 升序排列，哈希相同则按 generator ID 排序。train 第一组留给校准，第二、第三组作为 source；dev 第一组作为 support，第二组作为 query。选定后必须每组恰有三个变体；缺失或损坏即失败，不能跳过后替换。选择与任务文案、难度、模型反馈、成功率无关。

| 用途 | 官方 split | generator | 固定任务 ID |
| --- | --- | --- | --- |
| 独立协议校准 | train | `692c77d` | 实际仅运行 `692c77d_1`；`_2`、`_3` 同组保留，不进入主试验 |
| source 组一 | train | `ce359b5` | `ce359b5_1`、`ce359b5_2`、`ce359b5_3` |
| source 组二 | train | `27e1026` | `27e1026_1`、`27e1026_2`、`27e1026_3` |
| support | dev | `6bdbc26` | `6bdbc26_1`、`6bdbc26_2`、`6bdbc26_3` |
| query | dev | `6171bbc` | `6171bbc_1`、`6171bbc_2`、`6171bbc_3` |

主试验共 12 个任务；校准 generator 的三个变体全部排除。source/support/query 的 generator 集合互斥，manifest 启用 `group_disjoint_support_query=true`。本轮没有供 `run_study` 自动调参的 dev 角色；官方 dev split 仅为以上 support/query 的数据来源。

任务准备先固定 ID，再读取所选 source/support 文案。query 与校准的 `specs.json` 仅做字节哈希，不解码；查询文案不会参与选题或画像。source 的本地画像包含全部六个 source 文案，target 的本地画像只包含三个 support 文案。公开 manifest 不包含这些文案。

## 研究含义与冻结要求

唯一宽领域仍为 AppWorld 个人数字事务。`source.personal_apps` 和 `target.personal_apps` 是数据角色标识，不是两个被验证的语义领域。两个 source generator 共用同一宽域画像，数值特征仍为预定义的 `documented_api_environment=1.0`、`stateful_application_world=1.0`。

增加到两个 source generator 只能增加任务模板覆盖，**仍无法识别依赖领域特征的条件化迁移效应**。query 仅一个 generator 的三个相关变体，不能提供充分统计检验。所有 source 成败结果都保留；不因 source 全零、support 过易、构造产物相同或 query 成绩不佳而换题。跨条件不强制生成不同 harness；若哈希相同，成绩差异不得归因为方法改进。

第二轮冻结的协议为 `v2_required_tools`：要求工具动作、最多一次有界格式纠正，纠正调用占用同一 episode 的 24 步和实际 token/调用预算，不允许隐藏额外步数；实现见[固定模型协议](FIXED_MODEL_PROTOCOL.md)。独立校准已检验终态评分、显式 finish、动作契约及完整用量；一次校准通过不保证全部主任务正常结束。基础设施异常不进入源学习标签；actor 协议违规单独标注并保留证据，不能改写仍然有效的官方终态分数，也不能把有效评分表述成执行协议正常。

先完成独立校准，再开始固定清单的主试验。七条件与所有 support 优化结束后，在第一条 query 前冻结全部 harness、配置及 runtime 哈希。query 开始后不再修改该轮设计、提示或实现，不据 query 成绩选择替代任务。若后续需要修复，使用新的声明和独立任务；已有调用不能从累计成本中删除。

本轮同时改变任务组、步数上限、输出长度上限和结束协议。即使本轮表现不同，也不能把跨轮差异单独归因为结束协议修复；首先报告工程链路、有效评分、正常结束率和实际消耗。

## Episode 与调用预算

保持七条件 `G/D/S/V/F/G+R/F+R`，`max_modules=2`，无额外 ablation。source/support/execution seed 均为 `[101]`，construction seed 为 `[17]`，RSI 预算为 `[4]`；每 episode 最多 24 次 actor 请求，单请求输出上限 1,024 tokens。

| 阶段 | 计算 | 计划 episode 上限 |
| --- | --- | ---: |
| source | 6 个任务 × 11 个候选 × 1 seed；空编辑复用基线 | 66 |
| support | 2 个 R 条件 × 4 个 evaluator callback × 3 个 support × 1 seed | 24 |
| query | 3 个任务 × 7 条件 × 1 seed | 21 |
| 主试验 | 不含独立校准 | **111** |

GEPA 的 4 是 evaluator callback 上限；每次有效 callback 都展开完整的三个 support episode。优化器可提前停止或保留未使用预算，实际执行数必须从日志读取。初始候选、parent 重评和 proposal 评估都计费。

主 actor 请求上限为 `111 × 24 = 2,664`。D/S 各一次构造、每个 R 分支通常一次反思，名义主请求数为 2,668；加一个最多 24 请求的校准 episode 和一次 provider 预检，名义累计为 **2,693 次**。四次构造/反思是上述调度的名义值，不代替全局硬限制；反思失败、重试及其他新增请求都必须计入账本。

本轮批准的总限制为 **2,800 次模型请求、24,000,000 tokens、10 USD**，包含预检和校准。运行主配置必须从这两个调用/token 上限中扣除预检和校准的**实际**消耗，不能只扣名义值。每请求 `request_token_reserve=1,048,576`，用于保守准入，不表示每次都会消耗这么多 tokens。达到任一限制就停止，不能以更换目录重置已花费额度。

预注册预算采用峰值输入 $0.30/百万 tokens、输出 $1.20/百万 tokens；所有请求输出都不超过 1,024 时，忽略缓存折扣的保守上界为：

`(24,000,000 - 2,800 × 1,024) × 0.30 / 1,000,000 + (2,800 × 1,024) × 1.20 / 1,000,000 = $9.78048`。

这是基于所列费率和用量约束的估算，不是账单，也不覆盖未计价外部费用。实际费用按冻结配置中的离峰费率和提供方用量计算；提供方用量缺失时不能按零成本继续。第二轮消耗独立列报，项目累计费用再加入已披露的第一轮支出，避免把旧 source 复制或第一轮累计账本重复相加。

## 文件、准备命令和哈希

| 文件 | 用途 |
| --- | --- |
| [prepare_appworld_round2.py](../scripts/prepare_appworld_round2.py) | 固定抽样、排除旧组、预算计算及本地/公开分离 |
| [test_appworld_round2.py](../tests/test_appworld_round2.py) | 隔离、opaque query、完整变体、预算及拒绝覆盖测试 |
| [appworld_round2.json](../manifests/appworld_round2.json) | 可公开任务 ID、角色、校准保留组、spec 哈希和常量特征 |
| `runs/local-live/appworld_round2.manifest.local.json` | runner 使用的完整本地清单，含 source/support 文案；Git 忽略 |
| `runs/local-live/appworld_round2.preparation.local.json` | 本地准备报告，记录预算、配置和 split 哈希；Git 忽略 |

公开清单是脱敏投影，不能当作相同构造输入替代本地完整清单。实际 StudyConfig 与准备报告哈希一致；模型配置与协议参数单独冻结。可在仓库根目录执行无模型检查：

```bash
/home/hxy/.venvs/acl-appworld-py312/bin/python scripts/prepare_appworld_round2.py \
  --appworld-root /home/hxy/acl-appworld-data \
  --dry-run
```

脚本内置已声明的 StudyConfig；可加 `--study-config configs/appworld_round2.json` 复核已冻结的配置。去掉 `--dry-run` 会尝试首次写出文件；已有任何目标文件时拒绝覆盖。旧清单和旧运行目录没有被修改。

| 对象 | SHA-256 |
| --- | --- |
| 公开 manifest（规范 JSON） | `0c00c00b93477de336d28dac7dfe5eac0f59c04d419cd82467f0f1508f394c9d` |
| 本地完整 manifest（规范 JSON） | `ee53c683c4a19edd8ae0b951447d3b2df5b392ea341a9d5238b41d0cd48ae126` |
| 准备及实际主试验 StudyConfig（含默认字段，规范 JSON） | `70acdece9899f0dc91df6f1b570fe2be25874a6ef68f01a38a5a9672e948a54e` |
| 官方 train ID 文件（字节） | `93d9fe71e7a2e3b7529803d4a20b604f4ebf5ae806f321081140238068189d37` |
| 官方 dev ID 文件（字节） | `9fa976589300ea8905708257144d801d1604b06d85fb0181e381df8a3ba85001` |

准备脚本已在真实 WSL AppWorld 数据上完成；9 项测试在 Windows 和 WSL 均通过。准备过程没有请求模型，没有运行 task episode，没有读取官方 test、gold 或 query 文案。以下分别记录校准、启动参数和运行完成后审计的主试验结果。

## 2026-10-24 校准完成与主试验启动

独立校准目录为本地忽略的 `runs/local-live/deepseek-round2-calibration-001/`。仅执行预先选定的 `692c77d_1`，每题上限 24 步；该 generator 的全部三个变体均未进入主试验。校准有 10 次环境工具动作，随后一次显式 `finish` 请求；`valid_task_outcome=true`、官方 `score=1.0`、`success=true`、`execution_healthy=true`、`finished=true`、`finish_mode=explicit_tool`。协议违规、纠正及失败请求均为 0，没有使用自动结束。

校准实际使用 **11 次请求、66,343 tokens、估算 0.002192496 USD**：输入 65,020、输出 1,323，其中缓存输入 56,832。输入、输出、总 token 和费用字段完整；提供方未返回独立 reasoning-token 数量，不能把该字段当作已知的零。费用使用已冻结的离峰配置费率计算，不是服务商发票。

校准摘要的 26 个 `source_sha256` 与主试验启动时的执行源码逐一相同，当前执行提交为 `549ba88374181ec861bc80b99c75b9eb4a31cb00`。校准模型配置与主配置只有 `max_model_calls`、`max_total_tokens` 两个预算字段不同；模型别名、采样、工具协议、输出长度等保持相同。规范 JSON 哈希使用仓库 `_io.digest`，不是缩进文件的字节哈希。

| 冻结对象 | SHA-256 |
| --- | --- |
| [主模型配置](../configs/deepseek_flash_round2.json)，含默认字段 | `3f6dd8f4b313da580f15c3a2d783cdabb13d9e3b50fa2ecd92ee712c6cc44448` |
| [校准模型配置](../configs/deepseek_flash_round2_calibration.json)，含默认字段 | `213b4932360d3bb841b4789d92cea730c8bb7c5b0e0b0c878efe9e582cb53809` |
| [主 StudyConfig](../configs/appworld_round2.json)，含默认字段 | `70acdece9899f0dc91df6f1b570fe2be25874a6ef68f01a38a5a9672e948a54e` |
| 本地 `calibration_summary.json`（文件字节） | `a32a5f400d2063c56ed6939a4f5988a75e8ff730c877f3e113cbd070193d7d47` |

原计划为独立 provider 预检预留一次请求；本轮实际没有另发该请求，已发生的额外调用只有以上校准。主试验上限已按实际扣减为 **2,789 次请求、23,933,657 tokens**，与校准合计仍不超过 2,800 次和 24,000,000 tokens。第一轮预检及其费用仍归第一轮，不能重复记入本轮。主配置每次输出上限 1,024，保守准入预留 1,048,576 tokens；本轮总费用上限仍为 10 USD。

主试验目录是 `runs/local-live/deepseek-appworld-round2-001/`，重新采集全部 66 个 source 设计单元，不导入第一轮的 source 或费用账本。实际启动命令见[真实运行记录第二轮章节](APPWORLD_LIVE_RUN.md#第二轮独立任务主试验已完成)。以上是启动时的冻结记录；完成后的实际执行情况如下。

## 2026-10-24 已执行结果与诊断

主运行正常退出，公开[机器可读报告](../reports/appworld/2026-10-24-deepseek-round2/report.json)标记 `completed=true`、`mock_model=false`。实际为 **66 source + 18 support + 21 query = 105 个 episode**。七份目标 harness 在 query 前冻结；导出校验确认各条件 query 的 task/seed 配对相同、每次物理调用只有一个归属、执行源码哈希与 provenance 一致。实际使用 18 个 support episode，低于预注册上限 24；两个 R 分支各执行 3 个 evaluator callback、1 次反思，并各剩余 1 个未用 callback 额度。

| 条件 | 官方终态成功/3 | 健康且显式结束/3 | 最终 harness 组 |
| --- | ---: | ---: | --- |
| G | 2/3 | 3/3 | 通用基线 |
| D | 1/3 | 3/3 | 直接构造 |
| S | 2/3 | 2/3 | c07 |
| V | 3/3 | 3/3 | c07 |
| F | 2/3 | 3/3 | c07 |
| G+R@4 | 3/3 | 3/3 | 通用基线 |
| F+R@4 | 3/3 | 3/3 | c07 |

全部 21 次 query 均获得有效官方终态评分，16 次成功；20 次健康且显式使用 `finish` 结束，另 1 次属于 S 的 `step_budget_exhausted`。四次已显式结束的失败为 `task_not_solved`。没有 query 协议违规、自动结束或纠正请求，全部 16 个成功 episode 都健康且显式结束。这里的分母是七条件对同三个任务的执行次数，不是 21 个独立任务。

各阶段都存在有效官方终态评分，但执行健康不可由评分代替：source 成功 25/66、显式结束 35/66，另外 31 次耗尽步数，其中 1 次官方终态已成功，仍保留其未正常结束标记；support 成功 4/18，18 次均显式结束。主试验三个阶段的协议违规、纠正轮次和纠正物理请求均为 0，因此本轮没有提供真实触发纠正分支的效果证据。所有 1,676 次主模型请求均有用量记录，没有失败请求。

[只使用 source/support 的构造诊断](../reports/appworld/2026-10-24-round2-harness-diagnosis.json)解释了三种最终 harness：

- G 与 G+R@4 保留相同的通用基线，哈希 `cc95f632b45cf2ced718d173296713f281fe9d2c2e4d398b6fd122531c331cd0`。
- F、V、S 与 F+R@4 共用候选 c07（`plan`、`error_repair`），哈希 `7ba90301afc6241b28923f531265b3a849edfbe42058bbf8830ad9dc4ac16384`。
- D 使用直接构造的提示及 `plan`、`verify`，哈希 `385c421b7c5700843b1f1d59da2408443abeb7ae18818a66d7746861444d6466`；公开记录不包含提示正文。

source 的配对效应有 6 个正值、3 个负值、57 个零。按保存的 source 重新拟合与原数值构造器一致：F 和 V 的唯一最高候选都为 c07，本轮不是全零标签造成的平局选择。S 发出一次模型选择请求，冻结结果同样为 c07；其模型理由未保存，不能推测理由。两个 source generator 仍共享同一个特征向量，33 列拟合基的数值秩为 11；这些样本内变化并不能识别条件化迁移收益。

G+R@4 的三次 support callback 得分依次为 1、1/3、0，最终仍返回通用基线；诊断只陈述该序列和保存的选择结果，没有将其伪装成已保存的拒绝事件。F+R@4 的初始、parent 重评和 proposal 得分均为 0，最终保留 c07；依据固定 GEPA 的严格改进规则可重建等分 proposal 未被采用，该解释是控制流重建。两个 R 分支均评估过不同的 proposal，但最终没有改变对应初始 harness。因此 R 与非 R 的 query 成绩差异不能解释为反馈优化增益；F、V、S 之间也存在同样限制。

| 费用归属 | 请求数 | 总 tokens | 按配置估算 USD |
| --- | ---: | ---: | ---: |
| 第二轮主试验 | 1,676 | 11,474,187 | 0.295036803 |
| 第二轮独立校准 | 11 | 66,343 | 0.002192496 |
| **第二轮合计** | **1,687** | **11,540,530** | **0.297229299** |
| 第一轮（含其失败构造、预检与校准） | 827 | 4,558,974 | 0.150052395 |
| **项目两轮累计** | **2,514** | **16,099,504** | **0.447281694** |

第二轮没有导入第一轮调用；第一轮累计行已包含其 001→002 的历史调用，未再次叠加失败目录的 397 次。费用是提供方 token 用量与冻结费率的估算，reasoning-token 分项仍未知，不是服务商账单。

本轮支持的结论是：固定清单的真实执行、终态评分、冻结和记账链路完成，且观察到了有效 source 差异和三种最终 harness。它不能证明 F 优越、条件化泛化、GEPA 收益或新协议的独立因果效果。单个 query generator 的三个变体、常量公开特征、未传模型 seed 和可变模型别名仍限制这些解释；不据本轮成绩替换任务或事后修改该轮设计。
