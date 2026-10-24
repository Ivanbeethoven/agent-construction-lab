# AppWorld 本地真实模型运行

本轮使用 CC Switch 已保存的 DeepSeek 官方端点配置。模型预检确认 `deepseek-flash` 可用；服务文档在 2026-10-24 将它标为 `DeepSeek-V4.1-Flash-1017`。响应只返回别名，不能据此证明模型权重不可变。`reasoning_effort="none"`、`send_seed=false`，seed 仅用于配对实验。模型设置见 [初始配置](../configs/deepseek_flash_pilot.json)，任务选择见 [冻结清单说明](APPWORLD_PILOT.md)。

## 环境与凭据

实际环境是 WSL Ubuntu 24.04、Python 3.12.3、AppWorld 0.1.3.post1、GEPA 0.1.4，数据置于 Linux 文件系统。安装、版本和无模型调用的验收见 [环境记录](APPWORLD_ENVIRONMENT.md)。模型请求从 WSL 发出，Windows 只负责读取用户选定的 CC Switch 配置并启动进程。

`scripts/run_with_ccswitch.py` 以只读模式打开本机 `~/.cc-switch/cc-switch.db`，只取 `id=deepseek, app_type=opencode` 的条目，并要求端点与研究配置中的 `https://api.deepseek.com/v1` 完全一致。key 通过本地标准输入管道传给 WSL 子进程，在子进程中临时设为指定环境变量；不写配置、命令参数或调用账本。原 CC Switch 配置不修改。无 CC Switch 的机器可自行设置 `ACL_DEEPSEEK_API_KEY`，直接运行 Linux 入口。

```powershell
.venv/Scripts/python.exe scripts/run_with_ccswitch.py `
  --distribution Ubuntu-24.04 `
  --python /home/hxy/.venvs/acl-appworld-py312/bin/python `
  --model-config configs/deepseek_flash_pilot.json `
  --appworld-root /home/hxy/acl-appworld-data `
  --manifest runs/local-live/appworld_small_pilot.manifest.local.json `
  --manifest-sha256 c0174abd3da633b0a2b098f48078c0093a16c3e173bc6a256cc5074a20ec6885 `
  --study-config configs/appworld_small_pilot.json `
  --out runs/local-live/a-new-run
```

输出目录必须为空。原始 manifest、公开任务文案、模拟账户信息、模型轨迹和冻结的提示全部留在 Git 忽略的 `runs/local-live/`。本仓库 MIT 许可不改变 AppWorld 数据的再分发条件。

## 首次尝试与协议修订

首次运行 `deepseek-appworld-pilot-001` 完成 33 个 source episode 后，在 D 构造器停止。397 次模型请求均记录了用量；第 397 次 HTTP 请求成功，但返回的 modules 字段不符合唯一性、合法名称或数量约束。没有保存模型响应全文，不能进一步断定是哪一种字段错误。尚无 support/query 执行，也没有冻结的目标 harness。失败结果见 [公开失败记录](../reports/appworld/2026-10-24-deepseek-attempt-001.json)。

修复只补全构造提示中的输出契约；仍严格验证模块，不截断、不替换、不使用模拟结果兜底。继续运行必须保留旧目录、旧账本和失败构造费用，复用完整 source 观测；不能因为标签好坏选择保留哪些 source。模型参数、任务、种子和每题 12 步限制不变。协议修订在任何 query 执行之前完成。

本次恢复只适用于唯一的 `001 → 002`。校验器不提供跨进程、跨输出目录的全局预算池；若 002 失败，不能再次从 001 启动并重新取得同一额度。必须合并 002 的全部额外支出，且一旦已有 query 执行，就不能沿用本次冻结前修复流程更改设计。

原始证据锚点为 `5d03e8d19f1c0642c4051bb19ceae977000fbb962db29d4b1d5ef1e50c901cda`。`scripts/resume_appworld_construction.py --source-run runs/local-live/deepseek-appworld-pilot-001 --anchor-only` 可只读重算。实际续跑使用上述启动命令，同时将模型配置改为 `configs/deepseek_flash_pilot_recovery.json`、输出目录改为 `runs/local-live/deepseek-appworld-pilot-002`，增加 `--resume-from runs/local-live/deepseek-appworld-pilot-001 --resume-anchor <上述锚点>`。只有本机完整私有证据存在时才能通过这些校验。

初始总预算为 1,000 次请求、5,000,000 token，预检与校准已用 5 次、9,526 token，因此初始研究配置扣除了这部分额度。为避免完整上下文窗口的保守预留过早阻止请求，修订为总计 12,000,000 token，仍为 1,000 次请求；[继续运行配置](../configs/deepseek_flash_pilot_recovery.json) 为研究账本保留 995 次、11,990,474 token。旧账本必须计入这一限额，不能重置额度。

按核验时的峰值输入 0.30 USD/百万 token、输出 1.20 USD/百万 token，以及每请求最多 2,048 输出 token，全部请求的保守费用界为 `12 × 0.30 + (1,000 × 2,048 / 1,000,000) × (1.20 − 0.30) = 5.4432 USD`，仍低于本轮约 6 USD 的费用上限。实际离峰价格、缓存命中和 token 用量单独记账；该计算不等同于提供方账单。[官方价格与上下文限制](https://api-docs.deepseek.com/quick_start/pricing/)。

## 公开结果

完成后用 `scripts/export_appworld_results.py` 验证 manifest、配置、执行源码、冻结工件、全部调用的唯一归属和各条件相同的 query 键，再导出白名单字段。导出包含任务 ID、哈希、成功率、步数和用量；不复制任意自由文本。未完成或 mock 的运行不能被导出为真实完成成绩。

续跑来源路径记录在执行平台内，因此本轮导出也在同一 WSL 环境执行：

```bash
python scripts/export_appworld_results.py \
  --run runs/local-live/deepseek-appworld-pilot-002 \
  --public-manifest manifests/appworld_small_pilot.json \
  --out reports/appworld/a-new-public-export \
  --environment-report runs/local-live/appworld-environment-002.json \
  --preflight-report runs/local-live/provider-preflight/chat-probe.json \
  --calibration-summary runs/local-live/deepseek-calibration-001/calibration_summary.json
```

正式研究总账已包含 001 的源调用和失败构造，不能把 001 的总额再相加。预检与校准单列在 `extra_validation.additional_spend`；`new_run_usage` 是 002 新发出的请求，`imported_original_usage` 是导入的历史请求，两者之和才是累计研究支出。

只有三个相关的 query 变体、一个目标域和恒定公开特征，本轮仅验收真实链路。成功或失败都不能据此证明 F 优于其他条件，也不能把单域 bootstrap 区间当成泛化证据。

## 实际完成情况

`deepseek-appworld-pilot-002` 使用执行源码提交 `4305451922455ca909d98efe64a261974162cd5a` 完成，复用 33 个 source episode，新增 18 个 support 和 21 个 query；七个目标 harness 在 query 前全部冻结。研究累计 822 次调用、4,549,448 token、估算 0.149298567 USD，其中已经包含首次 397 次调用和失败 D 构造。加上预检与校准共 **827 次、4,558,974 token、0.150052395 USD**。两个运行目录的调用总数不能直接相加。

[公开结果](../reports/appworld/2026-10-24-deepseek-small/README.md) 保留全部七条件和 21 条逐次指标。官方终态成功次数为 D 1/3、F 1/3、G 2/3、S 0/3、V 1/3、G+R@4 3/3、F+R@4 2/3。除 D 外其余六条件最终 harness 的哈希一致；API 不传 seed，这些是同一策略的不同执行，不能把表中差异解释成优化带来的增益。

全部 21 次 query 都获得一次私有的官方终态评分，10 次通过，但执行健康与任务成功必须分开：17 次最后返回普通文本，没有使用 `finish` 工具或 JSON，导致 `invalid_action`；4 次耗尽 12 步。17 次响应均非空且 `finish_reason=stop`，不是输出截断；10 个成功 episode 均已有官方终态 pass。没有基础设施错误，也没有把这些协议错误隐藏为正常结束。本轮运行和评分保持原样；后续应在独立开发任务上改进结束协议，再预注册更大规模实验。

Windows 与 WSL/Linux 在启动修复后的真实运行前均通过 208 项单元/集成测试。公开导出的后续诊断增强不改变执行源码、任务、提示或实际成绩；其检查记录见根目录 `APPWORLD_VALIDATION.json`。原重建验收记录单独保留。

## 第二轮独立任务主试验已完成

2026-10-24，第二轮使用提交 `549ba88374181ec861bc80b99c75b9eb4a31cb00` 完成 `runs/local-live/deepseek-appworld-round2-001/`，正常退出并通过公开导出校验。完整任务选择、隔离规则、预算与哈希见[第二轮计划和结果](APPWORLD_ROUND2.md)。第一轮原始记录和公开结果均保留，不用第二轮结果覆盖。

本轮排除所有第一轮接触的 generator，固定六个 source、三个 support、三个 query 任务，另留 `692c77d` 整组作独立协议校准。与第一轮相比，actor 协议改为 `v2_required_tools`，最多一次纠正；纠正消耗本 episode 的 24 步额度。每请求最多输出 1,024 tokens。任务组、步数、输出上限和协议同时变化，不能用跨轮成绩差异孤立估计协议修复效果。当前特征仍为常量；相同 harness 也不能被解释成不同优化策略。

校准仅运行 `692c77d_1`，使用[校准配置](../configs/deepseek_flash_round2_calibration.json)，上限 24 步。记录显示有效官方评分成功、显式 `finish`、正常执行，0 次协议违规、0 次纠正、0 次失败请求。实际消耗 **11 次请求、66,343 tokens、估算 0.002192496 USD**。校准记录的全部 26 个执行源码哈希与主试验启动源码一致；该校准不是 query 结果。启动器的 24 步转发和两个入口对 0/101 的拒绝在 Windows、WSL 各通过 18 项凭据隔离测试，测试没有真实 API 调用。

第二轮没有新增独立付费预检，因此[主模型配置](../configs/deepseek_flash_round2.json)从总限额扣除校准后为 **2,789 次请求、23,933,657 tokens**；主阶段与校准合计上限仍是 2,800 次、24,000,000 tokens、10 USD。两份模型配置仅预算字段不同。下文按实际账本分别列出主阶段、校准和项目累计支出，没有复制第一轮累计账本相加。

以下是已执行的主命令记录；现有本地原始证据目录不覆盖或重复启动：

```powershell
.venv/Scripts/python.exe scripts/run_with_ccswitch.py `
  --distribution Ubuntu-24.04 `
  --python /home/hxy/.venvs/acl-appworld-py312/bin/python `
  --model-config configs/deepseek_flash_round2.json `
  --appworld-root /home/hxy/acl-appworld-data `
  --manifest runs/local-live/appworld_round2.manifest.local.json `
  --manifest-sha256 ee53c683c4a19edd8ae0b951447d3b2df5b392ea341a9d5238b41d0cd48ae126 `
  --study-config configs/appworld_round2.json `
  --out runs/local-live/deepseek-appworld-round2-001
```

主模型配置规范哈希为 `3f6dd8f4b313da580f15c3a2d783cdabb13d9e3b50fa2ecd92ee712c6cc44448`，StudyConfig 规范哈希为 `70acdece9899f0dc91df6f1b570fe2be25874a6ef68f01a38a5a9672e948a54e`，均与本地运行目录中保存的配置一致。任务和 query 前冻结约束遵循[固定协议](FIXED_MODEL_PROTOCOL.md)。主试验完成后才读取公开成绩补充本节，未读取任务文案调整选择。

[第二轮公开报告](../reports/appworld/2026-10-24-deepseek-round2/README.md)记录实际 **66 source、18 support、21 query，共 105 个 episode**。七份目标 harness 在 query 前冻结，全部物理调用归属唯一，各条件的 query 配对键相同，执行源码哈希与 provenance 一致。主试验共 **1,676 次请求、11,474,187 tokens、估算 0.295036803 USD**；加校准后第二轮为 **1,687 次、11,540,530 tokens、0.297229299 USD**。再加第一轮已披露的 0.150052395 USD，项目两轮共 **0.447281694 USD**，没有重复计入第一轮失败构造或导入 source 的费用。

第二轮 query 的官方终态成功次数为 G 2/3、D 1/3、S 2/3、V 3/3、F 2/3、G+R@4 3/3、F+R@4 3/3。21 次均获有效终态评分；20 次健康且显式 finish，S 的另 1 次耗尽 24 步。其余四次失败属于显式结束但 `task_not_solved`；全部 16 次成功都健康结束。source 成功 25/66、显式结束 35/66，31 次耗尽步数中有 1 次官方评分已成功；support 成功 4/18，18 次均显式结束。主试验没有协议违规、纠正或自动结束，也没有失败模型请求；未触发纠正分支意味着本轮不能评价该分支的实际收益。

[构造诊断](../reports/appworld/2026-10-24-round2-harness-diagnosis.json)只使用 source/support 证据：七条件对应三种最终 harness。F、V 都从非全零 source 标签唯一选出 c07（`plan`、`error_repair`），S 的一次模型选择也返回 c07；F+R@4 最终保留同一产物。G 与 G+R@4 共用通用基线，D 单独使用直接构造的提示及 `plan`、`verify`。两个 R 分支各运行三次 support callback、一次反思，均评估了新 proposal，但最终未改变初始 harness。因此同组条件的成绩差异不能解释为方法或反馈优化增益。

本轮仍只有两个 source generator、一个公开特征向量和一个 query generator 的三个变体。它完成了真实工程验收，并不构成 F 优越或跨域迁移的证据；也不能把与第一轮的差异单独归因于结束协议。费用为配置估算，模型别名和 API 不传 seed 的限制仍然存在。
