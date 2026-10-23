# AppWorld 真实微型工程 pilot

清单建立于 2026-10-24。它用真实 AppWorld 数据验证七条件实验链路，**不是方法优越性实验**，也不是官方 test 成绩。是否完成模型执行及实际结果应以对应 run 的状态、账本和公开脱敏报告为准；本文件记录执行前的选择与预算。

## 冻结的任务与选择方法

数据环境为 WSL Ubuntu-24.04，解释器 `/home/hxy/.venvs/acl-appworld-py312/bin/python`，AppWorld `0.1.3.post1`，数据根目录 `/home/hxy/acl-appworld-data`。本次读取的官方 ID 文件实际包含 **train 90 条、dev 57 条**。它们与论文原版的 105/60 不同；本轮按已下载文件核验，不把论文数量当成本地数据数量。

| 角色 | 官方 split | Generator | 完整三个任务 |
| --- | --- | --- | --- |
| source | train | `afc0fce` | `afc0fce_1`、`afc0fce_2`、`afc0fce_3` |
| support | dev | `6c2c621` | `6c2c621_1`、`6c2c621_2`、`6c2c621_3` |
| 内部 query | dev | `4ec8de5` | `4ec8de5_1`、`4ec8de5_2`、`4ec8de5_3` |

这轮没有独立 dev 调参任务，也不执行超参数搜索。`82e2fac` 整个 generator 留作环境/模型协议校准，硬性排除于上表所有角色之外；校准结果不用于更换 query。

选择规则在读取任务文案前确定：将完整 generator 按 `SHA256(selection_version:seed:official_split:generator_id)` 排序；seed 为 `20261024`，版本为 `appworld-engineering-pilot-v1`。排除校准 generator 后取第一个 train generator 为 source，第一个 dev generator 为 support、第二个为 query。每个被选 generator 必须正好有三个变体；缺文件或变体数不符直接报错，不自动换成更容易的任务。

选择脚本只读取 train/dev 的 ID 列表，不读取官方 test 列表、test 任务内容、gold solution 或评分源码。选定 ID 后只解码 source/support 的公开 `specs.json` 文案；query 的 spec 只作为不透明字节计算 SHA-256，不解析 instruction。模型成绩和 query 文案均未参与抽样。

## 领域与画像的准确含义

本轮唯一宽领域是 **AppWorld 个人数字事务**，由 benchmark 本身定义。`source.personal_apps` 与 `target.personal_apps` 是互斥的数据角色标识，不表示已证明存在两个不同语义领域。support/query 来自不同 generator，测的是这个宽领域内未见任务模板的执行链路。没有从随机 task ID 推断“金融”“音乐”等窄领域。

公开画像为手写、预定义的 benchmark 通用描述。数值特征只有 `documented_api_environment=1.0` 与 `stateful_application_world=1.0`，全部角色一致，未使用任务难度、答案、评分或 query 特征。运行用本地完整 manifest 会在 source 画像加入所选 source 的公开文案，在 target 画像加入所选 support 的公开文案；不加入 query 文案。

只有一个 source generator，且特征恒定，**不足以识别条件化的跨领域编辑收益**；F/V 排序相同或全部失败都应如实保留。query 也只有一个 generator 的三个相关变体，不能把三条任务、七条件或重复调用当成大量独立统计样本。runner 输出的域 bootstrap 区间在单目标域时不提供可信泛化证据。本轮只检查真实模型、HTTP 用量、构造、反馈优化、冻结、环境重置与终态评分能否连接起来。

## 预算与配置

配置：[appworld_small_pilot.json](../configs/appworld_small_pilot.json)。执行模型的端点、身份、凭据和 token 单价由本地私有模型配置提供，不写入公开清单。

| 阶段 | 计算 | 计划内物理 episode 上限 |
| --- | --- | ---: |
| source | 3 任务 × 11 候选 × 1 seed；空编辑复用基线 | 33 |
| support | G+R、F+R 各 4 次 evaluator callback；每次完整 3 support × 1 seed | 24 |
| query | 3 任务 × 七条件 × 1 seed | 21 |
| 合计 | 不含单独环境/模型协议校准 | **78** |

`source_seeds`、`support_seeds`、`execution_seeds` 均为 `[101]`，`construction_seeds=[17]`，`rsi_budgets=[4]`；每 episode 最多 `max_steps=12`。GEPA 预算单位是 callback，不是不同候选数量；初始候选和重复 parent/child 评估也消耗 callback。若优化器未用满预算，实际 support episode 可少于 24。

78 个 episode 不等于 78 次模型请求。episode 内多轮 actor 调用、D/S 构造调用、GEPA 反思调用，以及模型请求重试应记录完整 ledger。计划数字不包含基础设施重试和校准；这些单列计费，不能伪装成成功任务或源学习标签。严格用量要求 `require_complete_token_usage=true`，成本权重保持 0，未知费用不能填 0 冒充已知。

## 文件与复现入口

| 文件 | 用途 |
| --- | --- |
| [scripts/prepare_appworld_pilot.py](../scripts/prepare_appworld_pilot.py) | 确定性抽样、组隔离验证、opaque spec 哈希、预算计算、本地/公开分离写出 |
| [manifests/appworld_small_pilot.json](../manifests/appworld_small_pilot.json) | 可公开的 ID、组、spec 哈希、通用画像和常量特征；不含任务文案 |
| `runs/local-live/appworld_small_pilot.manifest.local.json` | **真实研究 runner 使用的完整本地 manifest**，含 source/support 文案；Git 忽略 |
| `runs/local-live/appworld_small_pilot.preparation.local.json` | 准备报告：预算、选择 ID、配置及 split 哈希；Git 忽略 |
| [tests/test_appworld_pilot.py](../tests/test_appworld_pilot.py) | 不读 held-out 文案、公开输出不泄漏、预算、校准排除与不覆盖冻结清单等检查 |

公开 manifest 是审计用的脱敏投影；它移除了 source/support 文案，**不能替代本地完整 manifest 后仍声称使用了相同构造输入**。

在仓库根目录、上述 WSL Python 环境中执行以下命令，可读取现有真实数据重新计算预算与哈希而不写文件、不调用模型：

```bash
/home/hxy/.venvs/acl-appworld-py312/bin/python scripts/prepare_appworld_pilot.py \
  --appworld-root /home/hxy/acl-appworld-data \
  --study-config configs/appworld_small_pilot.json \
  --dry-run
```

首次准备时去掉 `--dry-run`，脚本才写出默认文件；若目标已存在会拒绝覆盖。无需调用 `prepare-appworld` 的同生成器默认划分。本清单启用 `group_disjoint_support_query=true`，support/query 的 `generator_id` 集合全局不相交，source/target generator 与角色 domain 也隔离。

真实运行交给项目 CLI，指向本地完整 manifest、私有模型配置及一个**新的空输出目录**。先完成校准，再在首个 query 前冻结全部七个 harness。不要为重跑更改已经打开 query 反馈的选择 seed；基础设施修复与研究设计变更必须留独立运行记录。

## 本轮哈希锚点与已做检查

以下 manifest/config 哈希使用项目 `digest` 的规范 JSON 序列化，**不是缩进后的文件字节哈希**；split/spec 哈希则是原文件字节的 SHA-256。

| 对象 | SHA-256 |
| --- | --- |
| 公开 manifest（规范 JSON） | `f556baf1415e117f4777af3a4f9f8956ac35fa3c7e2098d9a13dd1d05b716196` |
| 本地完整 manifest（规范 JSON） | `c0174abd3da633b0a2b098f48078c0093a16c3e173bc6a256cc5074a20ec6885` |
| StudyConfig（含默认字段，规范 JSON） | `c3e0853fa7dfacc7181109fc72cd484b77e635a1b567db58753ad8fffbe3ce8e` |
| 官方 train ID 文件 | `93d9fe71e7a2e3b7529803d4a20b604f4ebf5ae806f321081140238068189d37` |
| 官方 dev ID 文件 | `9fa976589300ea8905708257144d801d1604b06d85fb0181e381df8a3ba85001` |

本次准备脚本在真实 WSL 数据上执行成功；选择/隐私/预算测试 8 项通过；两个本地输出路径已由 `git check-ignore` 确认忽略。准备阶段没有调用模型，不代表已完成七条件实验证据。第三方 AppWorld 数据与含其文案的轨迹仍遵循[选型文档中的再分发边界](BENCHMARK_SELECTION.md#安装资源与公开仓库边界)，不可因本项目采用 MIT 就明文上传。
