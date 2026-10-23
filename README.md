# Agent Construction Lab

固定同一个模型，研究能否在执行前依据任务特征构造有效的 agent harness，以及构造之后还需要多少反馈优化。

**当前状态：不完整的 v0.2 研究材料快照，尚不能运行主程序。** 2026-10-23 接手时，原清单中的 6,393 个文件仅有 5,700 个存在，缺少 693 个；原 ZIP 同样缺失。现存交付文件的 SHA-256 全部匹配。缺失内容包括核心源码、运行配置、实验协议和部分历史产物。见 [接手记录](docs/HANDOVER.md) 和 [完整性报告](docs/DELIVERY_AUDIT.json)。

> This is an incomplete research snapshot. Core implementation and configuration files are missing from the supplied archive. Bundled results are synthetic/mock evidence, not live-LLM research results.

## 研究问题

项目拟比较七个条件：

| 条件 | 含义 |
| --- | --- |
| G | 通用冻结提示 |
| D | 同一模型读取同源经验摘要后直接构建 |
| S | 同一模型从同源模块库选择 |
| V | 预测候选的最终价值 |
| F | 预测条件配对编辑的效果 |
| G+R | 从 G 出发进行 GEPA 反馈优化 |
| F+R | 从 F 出发进行同样的 GEPA 反馈优化 |

原设计限定在系统提示及 plan、verify、tool_docs、error_repair 四个指导模块（最多两个）的候选空间，要求 source、dev、support、query 分离，并在 query 前冻结 harness。以上是原交付说明和测试所描述的设计；核心实现尚待找回后核验。

## 现在可以查看什么

- [接手记录与后续工作](docs/HANDOVER.md)：工程缺口、研究边界和恢复顺序。
- [原始项目说明](docs/ORIGINAL_README.md)：逐字保留交付时的 README；其中安装指令和缺失文件链接暂不可用，相对链接仍按原项目根目录解释。
- [模拟器历史报告](runs/reference/report.html)：下载后用浏览器打开；仅为合成环境结果。
- [七条件 mock 摘要](runs/fixed_model_smoke/summary.json)：保存的摘要标明 mock，记录 22 个 source、14 个 support、28 个 query episode。
- [原交付验收声明](VALIDATION.json) 与 [原文件清单](PACKAGE_MANIFEST.json)：作为历史证据保留，不代表本仓库已重新通过验收。
- `tests/`：已有测试代码，可辅助恢复后验收；当前 13 个模块均因缺少项目模块而导入失败。

## 核验交付完整性

仅使用 Python 3.10+ 标准库，无需模型账号或第三方依赖：

```bash
python scripts/audit_delivery.py --output docs/DELIVERY_AUDIT.json
```

当前预期返回码为 `1`，表示交付不完整；完整且哈希一致时才返回 `0`。工具以原 `PACKAGE_MANIFEST.json` 为基准；原 README 已保存在 `docs/ORIGINAL_README.md`，核验时使用该副本。新增的接手文档和工具不在原交付清单中。若持有原 ZIP，可添加 `--archive /path/to/Agent_Construction_Lab.zip` 检查其中是否包含缺失文件。

原项目测试入口为：

```bash
python -m unittest discover -s tests -v
```

在找回缺失源码前，该命令会因 `ModuleNotFoundError` 失败。真实模型配置、AppWorld 数据及端到端验收也尚未在当前环境完成。

## 证据边界

当前材料没有真实 LLM 主实验成绩。mock HTTP 运行和有限状态模拟器结果只能作为管线与协议的历史验证材料，不能证明 F 优于 V、节省真实推理费用，或证明反馈优化可以被替代。F/V 的可区分性、构造与优化的公平比较，以及真实环境泛化是后续研究重点。

## 许可

[MIT License](LICENSE)。外部依赖和数据集仍遵循各自的许可，本仓库不附带 AppWorld 数据或模型凭据。
