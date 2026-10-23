# 固定模型实验运行手册

本手册对应 2026-10-24 重建实现。先完成本地测试和脚本模型 smoke，再接入真实 AppWorld 与模型。这里的配置是新建的运行建议，不是找回的原始配置，也不是已经注册的科研协议。历史 `runs/fixed_model_smoke/` 等目录是原交付证据，不应覆盖或用新代码重新解释其哈希。

## 1. Python 3.12 与最小测试环境

在仓库根目录执行。无需激活虚拟环境；显式使用其解释器可以避免系统 Python 混用。基础包没有必装第三方依赖；全套本地测试和 smoke 需要官方 `gepa==0.1.4` 与 `freezegun`，不需要安装 AppWorld 或下载任务数据。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements-smoke.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements-smoke.txt
.venv/bin/python -m unittest discover -s tests -v
```

只安装 `-e .` 可以使用基础模拟器；缺少 GEPA/freezegun 时，部分测试会跳过，不能据此声称全套验收完成。不要为了复现而直接使用 `pip install -e ".[fixed-model]"`：该 extra 也会安装真实 AppWorld。

## 2. 不调用外部模型的 smoke

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model smoke --out runs/local-smoke/check-001
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model verify-run --run runs/local-smoke/check-001
```

Linux：

```bash
.venv/bin/python -m agent_construction_lab.fixed_model smoke --out runs/local-smoke/check-001
.venv/bin/python -m agent_construction_lab.fixed_model verify-run --run runs/local-smoke/check-001
```

每次使用新的空输出目录，例如下一次改成 `check-002`。程序拒绝覆盖非空目录。`runs/local*/` 已被 Git 忽略。

smoke 使用 localhost HTTP 脚本模型、真实可执行库存状态、官方 GEPA、冻结 artifact 和实际请求账本，覆盖 G、D、S、V、F、G+R@4、F+R@4 七条件。模型行为有意偏向部分模块，用于检查流程；成功率不能作为科研结论。它不读取 API key，也不产生外部模型费用。

`verify-run` 核对运行完成状态、query 前冻结记录、harness 和当前运行时代码哈希。移动输出目录后仍可验证；修改运行时代码后应使用对应代码提交重新验证，不能重写历史哈希让检查通过。验证成功不等于 benchmark 性能有效。

## 3. 固定模型配置与凭据

将 `configs/fixed_model.example.json` 复制为 `configs/fixed_model.local.json`，在本地副本中替换所有 `REPLACE_...`。此文件名已被 Git 忽略。

```powershell
Copy-Item configs/fixed_model.example.json configs/fixed_model.local.json
```

```bash
cp configs/fixed_model.example.json configs/fixed_model.local.json
```

`model` 是请求模型 ID；`model_snapshot` 记录固定快照；`expected_response_model` 必须与服务响应中的模型身份完全一致。`base_url` 是 OpenAI-compatible Chat Completions 的 API 根地址，通常以 `/v1` 结尾，不能含凭据、query 或 fragment。配置中的 `api_key_env: "ACL_API_KEY_ENV"` 表示读取**名字就是 `ACL_API_KEY_ENV` 的环境变量**，不是在 JSON 中保存密钥，也不是再间接查找另一个变量名。

PowerShell 可通过凭据输入框将密钥放入当前进程环境：

```powershell
$aclCredential = Get-Credential -UserName api -Message "Enter the model API key as Password"
$env:ACL_API_KEY_ENV = $aclCredential.GetNetworkCredential().Password
Remove-Variable aclCredential
```

Bash 可通过不回显输入完成同样设置：

```bash
read -r -s -p 'Model API key: ' ACL_API_KEY_ENV
export ACL_API_KEY_ENV
```

根据服务实际能力设置 `max_tokens_parameter`（`max_tokens` 或 `max_completion_tokens`）、`temperature`、`reasoning_effort` 和 `send_seed`。不支持的可选字段应显式设为 `null`，不支持请求种子的服务应显式设 `send_seed: false`；请求种子本身不保证托管模型确定性。运行记录保存这些选择。价格字段未配置时保持 `null`，费用未知不能写成 0 美元。

## 4. 可选的真实 AppWorld 安装

本项目固定 `appworld==0.1.3.post1`，与 `gepa==0.1.4` 一起使用。以下 `install` 与 `download data --root` 命令已核对该固定版本 wheel 中的 `appworld/cli.py`；参考 [固定版本发布页](https://pypi.org/project/appworld/0.1.3.post1/) 与 [官方安装说明](https://github.com/StonyBrookNLP/appworld#floppy_disk-installation)。这里给出操作步骤，不表示当前已下载数据或已通过真实环境测试。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install "appworld==0.1.3.post1"
.\.venv\Scripts\python.exe -m appworld.cli install
.\.venv\Scripts\python.exe -m appworld.cli download data --root appworld_data
.\.venv\Scripts\python.exe scripts/validate_appworld_adapter.py --appworld-root appworld_data --out runs/local-live/adapter-check-001.json
```

Linux：

```bash
.venv/bin/python -m pip install 'appworld==0.1.3.post1'
.venv/bin/python -m appworld.cli install
.venv/bin/python -m appworld.cli download data --root appworld_data
.venv/bin/python scripts/validate_appworld_adapter.py --appworld-root appworld_data --out runs/local-live/adapter-check-001.json
```

`appworld.cli install` 解包官方安装包中的代码，`download data` 才下载任务数据；两者不可相互替代。`appworld_data/` 已被 Git 忽略。保存实际依赖版本、下载日期及数据来源，固定 Python 包版本并不自动固定远端数据内容。AppWorld 数据遵循其官方许可，不因本仓库采用 MIT 而改变。

适配检查使用官方 train 任务，不调用模型，检查公开 API、状态变更、终态评分、fresh-world reset 和文件读取策略。需要报告 `completed: true` 才算该环境接通。Windows IPC 的 fixture 测试通过不证明 AppWorld 的全部上游依赖在 Windows 可用；若安装或运行失败，保留错误并在独立 Linux/WSL Python 3.12 环境验证，不能回退到 builtin 后仍标记为 AppWorld 结果。

## 5. Doctor、清单准备与小规模真实验收

以下命令用 Windows 解释器示例；Linux 将 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。运行真实模型前先执行只读预检：

```powershell
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model doctor --model-config configs/fixed_model.local.json --appworld-root appworld_data --out runs/local-live/doctor-001.json
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model prepare-appworld --appworld-root appworld_data --out configs/appworld_manifest.local.json
```

`doctor` 不发模型请求，不验证真实服务可达性，也不等于真实任务已通过。占位模板、缺失环境变量、缺少固定版本依赖或数据根目录配置会使其失败。`prepare-appworld` 读取官方 split 成员和公共 `specs.json`，不读取任务解法、私有评价断言或 ground-truth 特征。

默认准备的是**同 generator 内的自定义验收划分**：官方 train 中不同 generator 分为 source/dev；官方 dev 中每个 target generator 单独构成一个 domain，该 generator 的不同任务变体分为 support/query。source/dev/target 的 generator 和 domain 均互斥，但一个 target domain 的 support/query 仍共享 generator。因此默认输出仅适合 within-generator 管线验收，不能证明未见 scenario 的泛化，也不是官方榜单协议。profile 仅来自所属 source/dev/support 的公开 instruction；官方 test_normal/test_challenge 不参与自动准备。

严格研究 pilot 必须另行制作宽领域 manifest：一个 target domain 可以包含多个 generator，但其 support 与 query 必须来自互不重叠的 generator。在清单根对象中设置 `"group_disjoint_support_query": true`，让验证器拒绝跨 support/query 复用 generator；不能仅给默认清单添加该标志，因为默认清单本身不满足此约束。60 任务 Pilot A 的角色、种子和物理 episode 预算以 [BENCHMARK_SELECTION.md](BENCHMARK_SELECTION.md) 为准，具体任务 ID 仍待审计与冻结；本手册和示例配置不构成已确认的预注册。

`configs/fixed_model_acceptance.json` 限制种子、步数和优化预算，**不限制任务数**。第一次 within-generator 真实验收可从准备好的清单另存 `configs/appworld_acceptance.local.json`，显式选择少量 source/dev generator 与 target generator，并保持每个 target 的不同 support/query 任务实例。严格 pilot 则改用前述宽领域、generator-disjoint 清单。删除未用 domain，保留 task ID、generator ID、benchmark_split 的真实关系；不得只改官方 split 字段来重分类任务。所有条件共用同一个清单，任务选择应在调用模型前记录并冻结。

```powershell
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model run --manifest configs/appworld_acceptance.local.json --model-config configs/fixed_model.local.json --study-config configs/fixed_model_acceptance.json --appworld-root appworld_data --out runs/local-live/acceptance-001
.\.venv\Scripts\python.exe -m agent_construction_lab.fixed_model verify-run --run runs/local-live/acceptance-001
```

`run` 会产生真实模型调用和可能的费用。先核对清单中各角色的任务数及服务单价；此命令没有美元总额硬上限。以 `max_modules=2` 为例，源阶段每个 task/source seed 有 11 次物理 episode（1 个 baseline + 10 个非空干预；no-op 复用 baseline）。一次 GEPA candidate evaluation 会执行当前 domain 的全部 support tasks × **support_seeds**，并不等于一次模型调用。

每个“R 方法 × target domain × construction seed”的计划内 support episode 上限为 **evaluator callback budget × support task 数 × support seed 数**；全部 support 上限还需对 R 方法、domain、construction seed 及预算档求和。实际调用不足预算、非法候选、失败与重试分别按账本记录，不把名义上限当成实耗。query 则使用 **query task 数 × execution seed 数 × construction seed 数 × condition 数**。`support_seeds` 可以显式配置独立列表；省略或设为 `null` 时默认沿用 `execution_seeds`，只增加 query 重复而不想增加优化成本时必须显式设置它。两份示例均显式设置 support seeds；预算细节见 [GEPA_ADAPTER.md](GEPA_ADAPTER.md)。

所有 construction 和 support 搜索完成并冻结后才开始 query。同一 domain/condition/construction seed 的 harness 复用于各 query task。模型身份不匹配、网络/子进程异常、缺失必要 token usage 等基础设施问题会中止运行并保留账本，不作为失败任务标签写入学习数据。修复后使用新输出目录；当前不支持在半次运行中恢复续跑。

## 6. 正式研究前的扩展与证据边界

`configs/fixed_model_study.json` 是多种子、较大预算的**通用建议起点**：source/execution 分别为 `[101, 211, 307]`，support 明确为 `[101, 102, 103]`，construction 为 `[17, 29, 43]`，R 预算 16，最多 15 步。它不等同于 60 任务 Pilot A 的配置，也不是已确认的预注册；该 pilot 的预算与种子以 [BENCHMARK_SELECTION.md](BENCHMARK_SELECTION.md) 为准，应另存准确对应的本地配置。扩大研究时使用事先冻结的严格清单和新输出目录，不要在看过 query 成绩后调配置再把同一 query 当未见测试集。

dev 任务在当前实现中用于清单隔离，不进行自动 dev 反馈调参；因此不要把 JSON 中出现 dev 角色写成“完成 dev 模型选择”。本配置还不是统计功效论证、正式实验规划或原始论文协议。bootstrap 按 domain 重采样；增加 seed 不会等价增加独立 domain，少量 domain 的区间不能支撑强泛化结论。

运行后审阅 `run_status.json`、`model_usage.jsonl`、`episodes.jsonl`、`source_pairs.jsonl`、`constructions.jsonl`、`support_searches.jsonl`、`query_lock.json`、`query_results.jsonl` 和 `summary.json`。`model_usage.jsonl` 是物理请求用量来源；各方法分摊的 source 成本不能再次相加冒充实际总支出。真实 traces 可能含任务内容，保留在被忽略的 `runs/local-live/`，公开实验时另行准备可公开的汇总与可复现清单。

重建代码通过测试，证明已覆盖被检查的实现行为；脚本 smoke 证明接口贯通；无模型适配检查证明环境接通。只有冻结协议下完成的真实模型、真实 benchmark 运行才能用于研究分析，而且仍须说明自定义 split、候选空间和统计推断范围。

实际采集 source 前还应核算摘要尺寸。D/S 的摘要按源领域保留公开特征与候选统计；超出 `source_summary_max_chars` 会报错，不能静默丢弃领域。较大的清单须在非 query 开发阶段确定足够的上下文预算。60 任务 pilot 的建议参数位于 `configs/appworld_pilot_proposal.json`，其 source 摘要上限为 60000 字符；仍需真实任务清单和实际模型上下文容量核验。
