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

初始总预算为 1,000 次请求、5,000,000 token，预检与校准已用 5 次、9,526 token，因此初始研究配置扣除了这部分额度。为避免完整上下文窗口的保守预留过早阻止请求，修订为总计 12,000,000 token，仍为 1,000 次请求；[继续运行配置](../configs/deepseek_flash_pilot_recovery.json) 为研究账本保留 995 次、11,990,474 token。旧账本必须计入这一限额，不能重置额度。

按核验时的峰值输入 0.30 USD/百万 token、输出 1.20 USD/百万 token，以及每请求最多 2,048 输出 token，全部请求的保守费用界为 `12 × 0.30 + (1,000 × 2,048 / 1,000,000) × (1.20 − 0.30) = 5.4432 USD`，仍低于本轮约 6 USD 的费用上限。实际离峰价格、缓存命中和 token 用量单独记账；该计算不等同于提供方账单。[官方价格与上下文限制](https://api-docs.deepseek.com/quick_start/pricing/)。

## 公开结果

完成后用 `scripts/export_appworld_results.py` 验证 manifest、配置、执行源码、冻结工件、全部调用的唯一归属和各条件相同的 query 键，再导出白名单字段。导出包含任务 ID、哈希、成功率、步数和用量；不复制任意自由文本。未完成或 mock 的运行不能被导出为真实完成成绩。

只有三个相关的 query 变体、一个目标域和恒定公开特征，本轮仅验收真实链路。成功或失败都不能据此证明 F 优于其他条件，也不能把单域 bootstrap 区间当成泛化证据。
