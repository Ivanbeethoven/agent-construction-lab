# 第二轮 AppWorld 独立任务清单与执行计划

**状态（2026-10-24）：仅完成任务准备和无模型测试，尚无第二轮执行成绩。** 下列数量是预先声明的设计和上限，不是已运行结果。选择 seed `20261025` 是固定抽样参数，不表示实际运行日期。模型配置、修复后的 runtime 和校准用量仍须在正式运行前分别冻结。

本轮承接[第一轮工程验收](APPWORLD_PILOT.md#2026-10-24-已执行结果与审计)发现的结束协议问题，使用全新的任务 generator 检查新协议。第一轮记录保持原样；本轮不是对第一轮 query 重新评分，也不导入其 source 学习数据。新旧任务组隔离不等于证明模型训练数据从未包含 benchmark。

## 固定选择及隔离

只读取官方 `train.txt`、`dev.txt` 的 ID，不读取官方 test 列表或内容。现有安装的官方 ID 数量为 train 90、dev 57。先永久排除已接触的四个 generator：`82e2fac`（旧校准）、`afc0fce`（旧 source）、`6c2c621`（旧 support）、`4ec8de5`（旧 query）。

对剩余 generator 按 `SHA256("appworld-engineering-round2-v1:20261025:<official_split>:<generator_id>")` 升序排列，哈希相同则按 generator ID 排序。train 第一组留给校准，第二、第三组作为 source；dev 第一组作为 support，第二组作为 query。选定后必须每组恰有三个变体；缺失或损坏即失败，不能跳过后替换。选择与任务文案、难度、模型反馈、成功率无关。

| 用途 | 官方 split | generator | 固定任务 ID |
| --- | --- | --- | --- |
| 独立协议校准 | train | `692c77d` | 首次只计划 `692c77d_1`；`_2`、`_3` 同组保留，不进入主试验 |
| source 组一 | train | `ce359b5` | `ce359b5_1`、`ce359b5_2`、`ce359b5_3` |
| source 组二 | train | `27e1026` | `27e1026_1`、`27e1026_2`、`27e1026_3` |
| support | dev | `6bdbc26` | `6bdbc26_1`、`6bdbc26_2`、`6bdbc26_3` |
| query | dev | `6171bbc` | `6171bbc_1`、`6171bbc_2`、`6171bbc_3` |

主试验共 12 个任务；校准 generator 的三个变体全部排除。source/support/query 的 generator 集合互斥，manifest 启用 `group_disjoint_support_query=true`。本轮没有供 `run_study` 自动调参的 dev 角色；官方 dev split 仅为以上 support/query 的数据来源。

任务准备先固定 ID，再读取所选 source/support 文案。query 与校准的 `specs.json` 仅做字节哈希，不解码；查询文案不会参与选题或画像。source 的本地画像包含全部六个 source 文案，target 的本地画像只包含三个 support 文案。公开 manifest 不包含这些文案。

## 研究含义与冻结要求

唯一宽领域仍为 AppWorld 个人数字事务。`source.personal_apps` 和 `target.personal_apps` 是数据角色标识，不是两个被验证的语义领域。两个 source generator 共用同一宽域画像，数值特征仍为预定义的 `documented_api_environment=1.0`、`stateful_application_world=1.0`。

增加到两个 source generator 只能增加任务模板覆盖，**仍无法识别依赖领域特征的条件化迁移效应**。query 仅一个 generator 的三个相关变体，不能提供充分统计检验。所有 source 成败结果都保留；不因 source 全零、support 过易、构造产物相同或 query 成绩不佳而换题。跨条件不强制生成不同 harness；若哈希相同，成绩差异不得归因为方法改进。

父线程批准的第二轮协议方向为：要求工具动作、最多一次有界格式纠正，纠正调用占用同一 episode 的 24 步和实际 token/调用预算，不允许隐藏额外步数。实现和模型端支持须经独立校准验证并记录最终代码/配置哈希；这里不宣称该协议已经运行通过。校准检查终态评分是否有效、是否显式 finish、动作契约及完整用量。基础设施异常不进入源学习标签；actor 协议违规单独标注并保留证据，不能改写仍然有效的官方终态分数，也不能把有效评分表述成执行协议正常。

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

若最终冻结费率为峰值输入 $0.30/百万 tokens、输出 $1.20/百万 tokens，并且所有请求输出都不超过 1,024，则忽略缓存折扣的上界为：

`(24,000,000 - 2,800 × 1,024) × 0.30 / 1,000,000 + (2,800 × 1,024) × 1.20 / 1,000,000 = $9.78048`。

这是基于所列费率和用量约束的估算，不是账单，也不覆盖未计价外部费用。父线程须核对运行时费率与模型配置；提供方用量缺失时不能按零成本继续。第二轮消耗独立列报，项目累计费用再加入已披露的第一轮支出，避免把旧 source 复制或第一轮累计账本重复相加。

## 文件、准备命令和哈希

| 文件 | 用途 |
| --- | --- |
| [prepare_appworld_round2.py](../scripts/prepare_appworld_round2.py) | 固定抽样、排除旧组、预算计算及本地/公开分离 |
| [test_appworld_round2.py](../tests/test_appworld_round2.py) | 隔离、opaque query、完整变体、预算及拒绝覆盖测试 |
| [appworld_round2.json](../manifests/appworld_round2.json) | 可公开任务 ID、角色、校准保留组、spec 哈希和常量特征 |
| `runs/local-live/appworld_round2.manifest.local.json` | runner 使用的完整本地清单，含 source/support 文案；Git 忽略 |
| `runs/local-live/appworld_round2.preparation.local.json` | 本地准备报告，记录预算、配置和 split 哈希；Git 忽略 |

公开清单是脱敏投影，不能当作相同构造输入替代本地完整清单。父线程最终 StudyConfig 应与准备报告比对；模型配置与协议参数单独冻结。可在仓库根目录执行无模型检查：

```bash
/home/hxy/.venvs/acl-appworld-py312/bin/python scripts/prepare_appworld_round2.py \
  --appworld-root /home/hxy/acl-appworld-data \
  --dry-run
```

脚本内置已声明的 StudyConfig；父线程创建最终配置后，可加 `--study-config configs/appworld_round2.json` 复核。去掉 `--dry-run` 会尝试首次写出文件；已有任何目标文件时拒绝覆盖。旧清单和旧运行目录没有被修改。

| 对象 | SHA-256 |
| --- | --- |
| 公开 manifest（规范 JSON） | `0c00c00b93477de336d28dac7dfe5eac0f59c04d419cd82467f0f1508f394c9d` |
| 本地完整 manifest（规范 JSON） | `ee53c683c4a19edd8ae0b951447d3b2df5b392ea341a9d5238b41d0cd48ae126` |
| 准备时 StudyConfig（含默认字段，规范 JSON） | `70acdece9899f0dc91df6f1b570fe2be25874a6ef68f01a38a5a9672e948a54e` |
| 官方 train ID 文件（字节） | `93d9fe71e7a2e3b7529803d4a20b604f4ebf5ae806f321081140238068189d37` |
| 官方 dev ID 文件（字节） | `9fa976589300ea8905708257144d801d1604b06d85fb0181e381df8a3ba85001` |

准备脚本已在真实 WSL AppWorld 数据上完成；9 项测试在 Windows 和 WSL 均通过。准备过程没有请求模型，没有运行 task episode，没有读取官方 test、gold 或 query 文案。这里仅记录计划和准备证据；实际成绩、冻结产物、用量和异常必须在真实运行结束后另行追加。
