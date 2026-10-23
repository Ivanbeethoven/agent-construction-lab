#!/usr/bin/env python3
"""Build a Chinese, offline HTML report from completed pilot output.

Standard library only. This script reads and checks the saved results; it does
not run an experiment, tune a model, drop methods, or rewrite any source data.
The main CSV tables must agree with summary.json before a report is emitted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "generic": "通用初始 agent",
    "conditional_uplift": "条件增益构建",
    "all_compatible": "最大兼容组合（上限 2）",
    "nearest_good_endpoint": "最近成功端点",
    "scalar_final_value": "Scalar 最终效用预测",
    "public_contract_rule": "公共契约规则",
    "generic+greedy_rsi": "通用初始 + Greedy RSI",
    "conditional_uplift+greedy_rsi": "条件构建 + Greedy RSI",
    "generic+beam_rsi": "通用初始 + Beam RSI",
    "conditional_uplift+beam_rsi": "条件构建 + Beam RSI",
    "uplift_no_failure": "消融：移除失败特征",
    "uplift_no_interactions": "消融：移除机制交互",
    "uplift_no_uncertainty": "消融：移除不确定性惩罚",
}
ORDER = list(LABELS)
RSI_METHODS = [name for name in ORDER if "+" in name]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def number(value: Any, decimals: int = 0) -> str:
    return f"{float(value):,.{decimals}f}"


def percent(value: Any) -> str:
    return f"{float(value) * 100:.2f}%"


def points(value: Any) -> str:
    adjusted = float(value) * 100
    if abs(adjusted) < 0.0000001:
        adjusted = 0.0
    return f"{adjusted:+.2f} pp"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return result


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _table_value(value: Any) -> Any:
    if isinstance(value, str):
        if value in {"True", "False", "true", "false"}:
            return value.lower() == "true"
        try:
            return float(value)
        except ValueError:
            return value
    return value


def verify_csv(summary_rows: Sequence[Mapping[str, Any]], csv_rows: Sequence[Mapping[str, Any]],
               keys: Sequence[str], name: str) -> None:
    """Reject stale or mixed-run CSV input instead of silently picking a source."""
    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(_table_value(row[field]) for field in keys)

    left = {key(row): row for row in summary_rows}
    right = {key(row): row for row in csv_rows}
    if len(left) != len(summary_rows) or len(right) != len(csv_rows):
        raise ValueError(f"Duplicate table keys in {name}")
    if set(left) != set(right):
        raise ValueError(f"{name} does not contain the same rows as summary.json")
    for row_key, expected in left.items():
        actual = right[row_key]
        if set(expected) != set(actual):
            raise ValueError(f"{name}: columns differ for {row_key}")
        for field, value in expected.items():
            parsed = _table_value(actual[field])
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                equal = isinstance(parsed, (int, float)) and math.isclose(
                    float(value), float(parsed), rel_tol=1e-10, abs_tol=1e-10)
            else:
                equal = value == parsed
            if not equal:
                raise ValueError(f"{name}: {row_key} / {field} differs from summary.json")


def verify_comparable(reference: Mapping[str, Any], sensitivity: Mapping[str, Any]) -> None:
    for name, summary in (("reference", reference), ("budget11", sensitivity)):
        if summary.get("real_llm_evidence") is not False:
            raise ValueError(f"This simulator report requires real_llm_evidence=false ({name})")
        if summary.get("actor") != "simulated_finite_state_actor":
            raise ValueError(f"Unexpected actor for the simulator report: {name}")
        methods = summary["methods"]
        if len({row["method"] for row in methods}) != len(methods):
            raise ValueError(f"Duplicate method rows: {name}")
    if sensitivity["config"]["candidate_budget"] != 11:
        raise ValueError("The budget11 sensitivity input must use candidate_budget=11")
    if reference["source_data_sha256"] != sensitivity["source_data_sha256"]:
        raise ValueError("Budget comparison requires the same source dataset")
    if reference["dataset"] != sensitivity["dataset"]:
        raise ValueError("Budget comparison requires identical dataset and role counts")
    excluded = {"candidate_budget", "bootstrap_samples", "bootstrap_seed"}
    relevant = set(reference["config"]) | set(sensitivity["config"])
    mismatched = [key for key in relevant - excluded
                  if reference["config"].get(key) != sensitivity["config"].get(key)]
    if mismatched:
        raise ValueError(f"Budget comparison changed more than candidate budget: {sorted(mismatched)}")
    left = {row["method"]: row for row in reference["methods"]}
    right = {row["method"]: row for row in sensitivity["methods"]}
    if set(left) != set(right):
        raise ValueError("Budget comparison requires the same methods")
    for method in left:
        if left[method]["query_executions"] != right[method]["query_executions"]:
            raise ValueError(f"Query denominators differ for {method}")


def ordered_methods(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = {row["method"]: row for row in summary["methods"]}
    return [rows[name] for name in ORDER if name in rows] + [
        rows[name] for name in sorted(set(rows) - set(ORDER))]


def label(method: str) -> str:
    return LABELS.get(method, method)


def color(method: str) -> str:
    if method == "conditional_uplift":
        return "#087f8c"
    if method in {"scalar_final_value", "public_contract_rule"}:
        return "#b15c25"
    if "+" in method:
        return "#7361aa"
    if method.startswith("uplift_no_"):
        return "#6aa9a9"
    return "#7e8b98"


def link(path: Path, output: Path, text: str | None = None) -> str:
    relative = os.path.relpath(path, output.parent).replace(os.sep, "/")
    return f'<a href="{esc(relative)}">{esc(text or path.name)}</a>'


def success_svg(summary: Mapping[str, Any]) -> str:
    rows = ordered_methods(summary)
    row_height, top = 42, 61
    height = top + len(rows) * row_height + 20
    x0, width = 290, 540
    chunks = [f'<svg viewBox="0 0 1110 {height}" role="img" aria-labelledby="success-title success-desc">',
              '<title id="success-title">各方法 query 成功率与支持集成本</title>',
              '<desc id="success-desc">成功率来自保存的 method_summary.csv。支持成本列为实际支持集执行次数与模拟器操作数；这些结果不含 LLM 实证。</desc>',
              '<text x="0" y="20" class="svg-heading">方法 / SIMULATOR</text>',
              '<text x="930" y="20" text-anchor="middle" class="svg-heading">支持执行 / 支持操作</text>']
    for fraction in (0, .25, .5, .75, 1):
        x = x0 + width * fraction
        chunks.append(f'<line x1="{x}" y1="42" x2="{x}" y2="{height - 15}" stroke="#e2e7eb"/>')
        chunks.append(f'<text x="{x}" y="34" text-anchor="middle" class="svg-axis">{fraction:.0%}</text>')
    for index, row in enumerate(rows):
        method = str(row["method"])
        y = top + index * row_height
        rate = float(row["query_success_rate"])
        chunks.extend([
            f'<text x="0" y="{y + 17}" class="svg-label">{esc(label(method))}</text>',
            f'<rect x="{x0}" y="{y}" width="{width * rate:.3f}" height="25" rx="4" fill="{color(method)}"/>',
            f'<text x="{x0 + width * rate + 8:.3f}" y="{y + 18}" class="svg-value">{percent(rate)}</text>',
            f'<text x="930" y="{y + 18}" text-anchor="middle" class="svg-cost">{number(row["support_executions"])} / {number(row["support_cost_units"])}</text>',
        ])
    chunks.append("</svg>")
    return "".join(chunks)


def support_svg(reference: Mapping[str, Any], sensitivity: Mapping[str, Any]) -> str:
    a = {row["method"]: row for row in reference["methods"]}
    b = {row["method"]: row for row in sensitivity["methods"]}
    methods = [method for method in RSI_METHODS if method in a and method in b]
    values = [float(table[method]["support_cost_units"]) for table in (a, b) for method in methods]
    maximum = max(values, default=1) or 1
    scale = 10 ** math.floor(math.log10(maximum))
    maximum = math.ceil(maximum / scale * 2) / 2 * scale
    x0, width, top, row_height = 290, 660, 65, 69
    height = top + len(methods) * row_height + 20
    budget = reference["config"]["candidate_budget"]
    chunks = [f'<svg viewBox="0 0 1110 {height}" role="img" aria-labelledby="support-title support-desc">',
              '<title id="support-title">默认预算与 budget11 的支持集操作成本</title>',
              '<desc id="support-desc">实际发生的模拟器操作数，包含工具调用与计费的本地操作。源数据成本和 query 执行成本另表报告。</desc>',
              f'<rect x="290" y="4" width="13" height="13" rx="2" fill="#087f8c"/><text x="311" y="16" class="svg-axis">预算 {budget}</text>',
              '<rect x="423" y="4" width="13" height="13" rx="2" fill="#c38338"/><text x="444" y="16" class="svg-axis">预算 11</text>']
    for fraction in (0, .25, .5, .75, 1):
        x = x0 + width * fraction
        chunks.append(f'<line x1="{x}" y1="49" x2="{x}" y2="{height - 20}" stroke="#e2e7eb"/>')
        chunks.append(f'<text x="{x}" y="39" text-anchor="middle" class="svg-axis">{number(maximum * fraction)}</text>')
    for index, method in enumerate(methods):
        y = top + index * row_height
        chunks.append(f'<text x="0" y="{y + 23}" class="svg-label">{esc(label(method))}</text>')
        for offset, table, fill in ((0, a, "#087f8c"), (25, b, "#c38338")):
            value = float(table[method]["support_cost_units"])
            bar_width = width * value / maximum
            chunks.extend([
                f'<rect x="{x0}" y="{y + offset}" width="{bar_width:.3f}" height="18" rx="3" fill="{fill}"/>',
                f'<text x="{x0 + bar_width + 8:.3f}" y="{y + offset + 14}" class="svg-value">{number(value)}</text>',
            ])
    chunks.append("</svg>")
    return "".join(chunks)


def matrix(summary: Mapping[str, Any], optimizer: str) -> str:
    cells = {(bool(row["construction"]), bool(row["deployment_rsi"])): row
             for row in summary["four_cells"] if row["optimizer"] == optimizer}
    if set(cells) != {(False, False), (False, True), (True, False), (True, True)}:
        raise ValueError(f"Incomplete four-cell matrix for {optimizer}")
    pieces = [f'<article class="matrix-panel"><h3>{optimizer.title()} 搜索</h3>',
              '<table class="matrix"><thead><tr><th>初始化</th><th>冻结后直接执行</th><th>先在支持集搜索</th></tr></thead><tbody>']
    for constructed, name in ((False, "通用初始"), (True, "条件构建")):
        pieces.append(f'<tr><th>{name}</th>')
        initial = float(cells[constructed, False]["query_success_rate"])
        for searched in (False, True):
            row = cells[constructed, searched]
            rate = float(row["query_success_rate"])
            effect = f'较本行冻结值 {points(rate - initial)}' if searched else '构建期目标环境执行：0'
            pieces.append(f'<td class="{"searched" if searched else "frozen"}"><strong>{percent(rate)}</strong>'
                          f'<span>{effect}</span><small>支持执行 {number(row["support_executions"])} 次<br>'
                          f'支持操作 {number(row["support_cost_units"])} 单位</small></td>')
        pieces.append('</tr>')
    pieces.append('</tbody></table></article>')
    return "".join(pieces)


def comparison(summary: Mapping[str, Any], left: str, right: str) -> Mapping[str, Any] | None:
    for row in summary["paired_comparisons"]:
        if row["method_a"] == left and row["method_b"] == right:
            return row
        if row["method_a"] == right and row["method_b"] == left:
            return {**row, "method_a": left, "method_b": right,
                    "mean_delta": -float(row["mean_delta"]),
                    "ci_low": -float(row["ci_high"]), "ci_high": -float(row["ci_low"])}
    return None


def comparison_table(summary: Mapping[str, Any]) -> str:
    pairs = [
        ("conditional_uplift", "generic"),
        ("conditional_uplift", "scalar_final_value"),
        ("conditional_uplift", "public_contract_rule"),
        ("conditional_uplift", "generic+greedy_rsi"),
        ("conditional_uplift", "generic+beam_rsi"),
        ("conditional_uplift+greedy_rsi", "conditional_uplift"),
        ("conditional_uplift+beam_rsi", "conditional_uplift"),
    ]
    pieces = ['<div class="table-wrap"><table><thead><tr><th>成对差值：左 − 右</th><th>均值差</th>'
              '<th>描述性 95% 区间</th><th>配对观测</th></tr></thead><tbody>']
    for left, right in pairs:
        row = comparison(summary, left, right)
        if row is None:
            continue
        pieces.append(f'<tr><td>{esc(label(left))} − {esc(label(right))}</td>'
                      f'<td class="numeric">{points(row["mean_delta"])}</td>'
                      f'<td class="numeric">[{points(row["ci_low"])}, {points(row["ci_high"])}]</td>'
                      f'<td class="numeric">{number(row["paired_observations"])}</td></tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


def method_table(summary: Mapping[str, Any]) -> str:
    pieces = ['<div class="table-wrap"><table class="methods"><thead><tr><th>方法</th><th>Query 成功率</th>'
              '<th>Query 次数</th><th>Query 平均操作</th><th>支持执行次数</th><th>支持工具调用</th>'
              '<th>支持操作合计</th></tr></thead><tbody>']
    for row in ordered_methods(summary):
        method = str(row["method"])
        mean_cost = float(row["query_cost_units"]) / int(row["query_executions"])
        style = ' class="emphasized"' if method in {"conditional_uplift", "scalar_final_value", "public_contract_rule"} else ""
        pieces.append(f'<tr{style}><td>{esc(label(method))}<code>{esc(method)}</code></td>'
                      f'<td class="numeric">{percent(row["query_success_rate"])}</td>'
                      f'<td class="numeric">{number(row["query_executions"])}</td>'
                      f'<td class="numeric">{number(mean_cost, 2)}</td>'
                      f'<td class="numeric">{number(row["support_executions"])}</td>'
                      f'<td class="numeric">{number(row["support_tool_steps"])}</td>'
                      f'<td class="numeric">{number(row["support_cost_units"])}</td></tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


def budget_table(reference: Mapping[str, Any], sensitivity: Mapping[str, Any]) -> str:
    a = {row["method"]: row for row in reference["methods"]}
    b = {row["method"]: row for row in sensitivity["methods"]}
    budget = reference["config"]["candidate_budget"]
    pieces = [f'<div class="table-wrap"><table><thead><tr><th>方法</th><th>预算 {budget} 成功率</th>'
              '<th>预算 11 成功率</th><th>成功率变化</th><th>支持执行：默认 → 11</th></tr></thead><tbody>']
    for method in ["generic", "conditional_uplift", *RSI_METHODS]:
        if method not in a:
            continue
        left, right = a[method], b[method]
        pieces.append(f'<tr><td>{esc(label(method))}</td><td class="numeric">{percent(left["query_success_rate"])}</td>'
                      f'<td class="numeric">{percent(right["query_success_rate"])}</td>'
                      f'<td class="numeric">{points(float(right["query_success_rate"]) - float(left["query_success_rate"]))}</td>'
                      f'<td class="numeric">{number(left["support_executions"])} → {number(right["support_executions"])}</td></tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


def delta_table(reference: Mapping[str, Any], sensitivity: Mapping[str, Any]) -> str:
    pieces = ['<div class="table-wrap"><table><thead><tr><th>预算 / 搜索器</th><th>冻结构建增益<br>U − B</th>'
              '<th>通用初始的 RSI 增益<br>R(B) − B</th><th>条件构建后残余增益<br>R(U) − U</th>'
              '<th>冻结 U 与 RSI 对照之差<br>U − R(B)</th></tr></thead><tbody>']
    for summary in (reference, sensitivity):
        rows = {row["method"]: float(row["query_success_rate"]) for row in summary["methods"]}
        for optimizer in ("greedy", "beam"):
            base, constructed = rows["generic"], rows["conditional_uplift"]
            base_rsi = rows[f"generic+{optimizer}_rsi"]
            built_rsi = rows[f"conditional_uplift+{optimizer}_rsi"]
            values = (constructed - base, base_rsi - base, built_rsi - constructed, constructed - base_rsi)
            pieces.append(f'<tr><td>{summary["config"]["candidate_budget"]} / {optimizer.title()}</td>'
                          + "".join(f'<td class="numeric">{points(value)}</td>' for value in values) + '</tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


def positive_control(summary: Mapping[str, Any]) -> str:
    control = summary.get("positive_repair_control")
    if not control:
        return ""
    pieces = ['<h3>优化器正控：能够搜索到协同修复吗？</h3>',
              '<p>单独取两个公共风险类别相同的 dev 任务，分别作为支持与 query，使用独立种子。该诊断不用于训练或调参；它检查搜索器能否发现 retry 与幂等的协同修复。</p>',
              '<div class="table-wrap"><table><thead><tr><th>搜索器 / 上限</th><th>实际评估候选</th><th>选择的机制</th>'
              '<th>Query：初始 → 搜索后</th><th>支持执行 / 工具调用</th></tr></thead><tbody>']
    for row in control["rows"]:
        selected = " + ".join(row["selected_mechanisms"]) or "no-op"
        pieces.append(f'<tr><td>{esc(str(row["optimizer"]).title())} / {row["candidate_budget"]}</td>'
                      f'<td class="numeric">{number(row["candidates_evaluated"])}</td><td><code>{esc(selected)}</code></td>'
                      f'<td class="numeric">{percent(row["baseline_query_success_rate"])} → {percent(row["repaired_query_success_rate"])}</td>'
                      f'<td class="numeric">{number(row["support_executions"])} / {number(row["support_tool_steps"])}</td></tr>')
    pieces.append('</tbody></table></div><p class="note">这是一个 query 任务、4 个执行种子的机制正控，不能证明主实验中的支持分布匹配或整体搜索器足够强。Greedy 与 Beam 的不同表现也说明：没有找到修复不等于不存在可用修复。正控执行成本单独列入下方完整账本。</p>')
    return "".join(pieces)


def accounting_table(summary: Mapping[str, Any]) -> str:
    accounting = summary["accounting"]
    phases = [("源数据采集（全局共享一次）", "source_shared_offline"),
              ("目标域支持集搜索（所有方法）", "support"),
              ("最终 Query 评估（所有方法）", "query")]
    phases += [(title, key) for title, key in (
        ("机制正控：支持集搜索", "positive_control_support"),
        ("机制正控：Query 与共享基线", "positive_control_query"),
    ) if key in accounting]
    phases.append(("本次实验全部物理执行", "all_physical_executions"))
    pieces = ['<div class="table-wrap"><table><thead><tr><th>阶段</th><th>环境执行次数</th>'
              '<th>工具调用次数</th><th>模拟器操作单位</th></tr></thead><tbody>']
    for title, key in phases:
        row = accounting[key]
        pieces.append(f'<tr><td>{title}</td><td class="numeric">{number(row["executions"])}</td>'
                      f'<td class="numeric">{number(row["tool_steps"])}</td><td class="numeric">{number(row["cost_units"])}</td></tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


CSS = """
:root { color-scheme: light; --ink:#1e2937; --muted:#5f6e7e; --line:#dfe6eb; --teal:#087f8c; --paper:#fff; }
* { box-sizing:border-box; }
body { margin:0; background:#eef2f4; color:var(--ink); font-family:Inter,"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",Arial,sans-serif; line-height:1.75; }
main { max-width:1280px; margin:36px auto; background:var(--paper); box-shadow:0 12px 48px #1a304012; border-radius:16px; overflow:hidden; }
header { padding:48px 56px 35px; border-top:7px solid var(--teal); background:linear-gradient(135deg,#f8fcfc,#fff 68%); }
.eyebrow { color:var(--teal); letter-spacing:.16em; font-size:12px; font-weight:750; }
h1 { font-size:38px; line-height:1.32; margin:16px 0; letter-spacing:-.025em; }
h2 { font-size:25px; margin:0 0 14px; line-height:1.4; }
h3 { font-size:18px; margin:0 0 13px; }
p { margin:10px 0; }
.subtitle { max-width:970px; color:var(--muted); font-size:16px; }
.tags { display:flex; flex-wrap:wrap; gap:9px; margin:21px 0 0; }
.tag { border-radius:30px; background:#e7f1f2; color:#176771; padding:4px 13px; font-size:12px; font-weight:650; }
.tag.warning { background:#f9e9df; color:#974b23; }
section { padding:34px 56px; border-top:1px solid var(--line); }
.section-index { display:inline-block; color:var(--teal); font-size:13px; font-weight:750; margin-bottom:8px; letter-spacing:.1em; }
.lede { font-size:18px; line-height:1.8; }
.note { color:var(--muted); font-size:13px; }
.callout { border-left:4px solid #c38338; padding:16px 20px; background:#fcf5ed; border-radius:0 8px 8px 0; margin:20px 0; }
.callout.teal { border-color:var(--teal); background:#edf7f7; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }
.stat { border:1px solid var(--line); padding:18px 20px; border-radius:9px; }
.stat span { display:block; color:var(--muted); font-size:12px; }
.stat strong { display:block; font-variant-numeric:tabular-nums; font-size:30px; line-height:1.3; margin:10px 0; color:var(--teal); }
.stat small { color:var(--muted); font-size:12px; }
.stat.challenge strong { color:#a45124; }
.chart { margin:22px 0 12px; overflow-x:auto; }
svg { display:block; width:100%; min-width:850px; font-family:inherit; }
.svg-label { font-size:14px; fill:#314254; }
.svg-heading { font-size:12px; font-weight:700; fill:#5f6e7e; letter-spacing:.03em; }
.svg-axis { font-size:12px; fill:#627487; }
.svg-value { font-size:12px; fill:#314254; font-variant-numeric:tabular-nums; }
.svg-cost { font-size:13px; fill:#4b5d70; font-variant-numeric:tabular-nums; }
.legend { display:flex; gap:20px; flex-wrap:wrap; color:var(--muted); font-size:12px; }
.dot { width:9px; height:9px; border-radius:2px; display:inline-block; margin-right:6px; }
.table-wrap { overflow-x:auto; margin:18px 0; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; font-weight:650; background:#f3f6f8; color:#45566a; }
th,td { padding:12px 13px; border-bottom:1px solid var(--line); vertical-align:middle; }
thead th { white-space:nowrap; }
td.numeric { white-space:nowrap; text-align:right; font-variant-numeric:tabular-nums; }
tr.emphasized td { background:#f7fbfb; }
td code { display:block; font-size:10px; color:#6c7d8d; margin-top:3px; line-height:1.4; }
.methods td:first-child { min-width:244px; }
.matrices { display:grid; grid-template-columns:1fr 1fr; gap:22px; margin:22px 0; }
.matrix-panel { min-width:0; }
.matrix { table-layout:fixed; font-size:11px; }
.matrix th:first-child { width:76px; }
.matrix th, .matrix td { padding:12px 10px; border:1px solid var(--line); }
.matrix thead th { white-space:normal; }
.matrix td strong { display:block; color:var(--teal); font-size:28px; line-height:1.3; font-variant-numeric:tabular-nums; }
.matrix td span { display:block; font-size:10px; margin:7px 0; }
.matrix td small { display:block; color:var(--muted); font-size:10px; line-height:1.8; }
.matrix .frozen { background:#eff8f8; }
.matrix .searched { background:#f5f3f9; }
.matrix .searched strong { color:#7361aa; }
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:26px; }
.facts { margin:0; }
.facts div { display:flex; justify-content:space-between; gap:20px; border-bottom:1px solid var(--line); padding:9px 0; }
.facts dt { color:var(--muted); }
.facts dd { margin:0; font-variant-numeric:tabular-nums; font-weight:600; text-align:right; }
ul,ol { padding-left:23px; }
li { margin:9px 0; }
a { color:var(--teal); text-underline-offset:3px; }
.hash { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:10px; overflow-wrap:anywhere; line-height:1.6; }
.provenance td:first-child { width:220px; }
footer { background:#f3f6f8; padding:24px 56px; color:var(--muted); font-size:12px; }
@media(max-width:900px) { main { margin:0; border-radius:0; } header,section { padding:26px 24px; } h1 { font-size:29px; } .stats { grid-template-columns:1fr 1fr; } .stat { padding:14px 12px; } .stat strong { font-size:25px; } .matrices,.two-col { grid-template-columns:1fr; } footer { padding:22px 24px; } }
@media print { body { background:white; } main { max-width:none; margin:0; box-shadow:none; border-radius:0; } header,section { padding:20px 24px; } h1 { font-size:29px; } h2,h3 { break-after:avoid; } tr,.stat,.matrix-panel,.callout { break-inside:avoid; } .chart { overflow:visible; } svg { min-width:0; } .table-wrap { overflow:visible; } table { font-size:10px; } th,td { padding:8px; } .matrices { grid-template-columns:1fr 1fr; } .matrix td strong { font-size:23px; } a { color:inherit; } }
"""


def render(reference: Mapping[str, Any], sensitivity: Mapping[str, Any], files: Sequence[Path], output: Path) -> str:
    methods = {row["method"]: row for row in reference["methods"]}
    uplift = float(methods["conditional_uplift"]["query_success_rate"])
    generic = float(methods["generic"]["query_success_rate"])
    scalar = float(methods["scalar_final_value"]["query_success_rate"])
    rule = float(methods["public_contract_rule"]["query_success_rate"])
    dataset, config, accounting = reference["dataset"], reference["config"], reference["accounting"]
    budget = config["candidate_budget"]
    ref_link = link(files[0], output)
    method_link = link(files[1], output)
    four_link = link(files[2], output)
    budget_link = link(files[3], output, "budget11 / summary.json")
    if scalar >= uplift and rule >= uplift:
        main_finding = "更简单的 Scalar 与公共契约规则控制均未低于条件增益构建。当前结果不支持条件构建优于这些控制。"
    elif scalar >= uplift or rule >= uplift:
        main_finding = "至少一个更简单的控制未低于条件增益构建。当前结果不支持条件构建稳定优于全部控制。"
    else:
        main_finding = "条件增益构建在本轮均值上高于 Scalar 与公共契约规则；这一模拟器结果仍不足以支持真实领域或 LLM 泛化结论。"
    provenance = "".join(
        f'<tr><td>{link(path, output, ("budget11 / " if index == 3 else "") + path.name)}</td>'
        f'<td class="hash">{hashlib.sha256(path.read_bytes()).hexdigest()}</td></tr>'
        for index, path in enumerate(files)
    )
    positives = int(dataset["positive_effect_rows"])
    negatives = int(dataset["negative_effect_rows"])
    source_rows = int(dataset["paired_source_rows"])
    neutral = source_rows - positives - negatives
    query_n = int(methods["conditional_uplift"]["query_executions"])
    artifact_count = int(accounting["artifacts_built"])
    dev_count = dataset.get("dev_tasks_not_used_for_training_or_tuning", dataset.get("unused_dev_tasks", 0))
    dev_control_count = dataset.get("dev_tasks_used_only_in_positive_control", 0)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>构建时专门化 · 合成环境研究报告</title><style>{CSS}</style></head>
<body><main>
<header><div class="eyebrow">AGENT CONSTRUCTION LAB / REPRODUCIBLE PILOT</div>
<h1>构建时专门化<br>合成环境运行报告</h1>
<p class="subtitle">用源任务上的配对机制干预训练构造器，在目标实例执行前冻结 agent，再比较支持集搜索能带来多少额外改善。所有图表来自已经保存的运行结果。</p>
<div class="tags"><span class="tag warning">SIMULATOR · 无 LLM 实证</span><span class="tag">默认候选预算 {budget}</span>
<span class="tag">敏感性预算 11</span><span class="tag">最多 2 个机制</span><span class="tag">构建期目标环境执行 0 次</span></div>
</header>

<section><span class="section-index">01 / 先看结果</span><h2>把对照结果放在主结论里</h2>
<p class="lede">{main_finding}</p>
<p>冻结条件构建的 query 成功率为 <strong>{percent(uplift)}</strong>；Scalar 控制为 <strong>{percent(scalar)}</strong>；公共契约规则为 <strong>{percent(rule)}</strong>。条件构建相对通用初始 agent 的变化为 <strong>{points(uplift - generic)}</strong>。这些数值来自同一批 query 任务及执行种子。</p>
<div class="stats">
<div class="stat"><span>冻结条件构建 · Query</span><strong>{percent(uplift)}</strong><small>{number(query_n)} 次执行，含重复种子</small></div>
<div class="stat"><span>条件构建 − 通用初始</span><strong>{points(uplift - generic)}</strong><small>成功率百分点差</small></div>
<div class="stat challenge"><span>Scalar − 条件构建</span><strong>{points(scalar - uplift)}</strong><small>正值表示 Scalar 更高</small></div>
<div class="stat challenge"><span>公共契约规则 − 条件构建</span><strong>{points(rule - uplift)}</strong><small>正值表示规则控制更高</small></div>
</div>
<div class="callout"><strong>这份报告不证明可以“消灭 RSI”。</strong> 构造器按目标实例的公开特征初始化，而 RSI 在同一 family 的异质支持任务上选机制，支持目标与实例目标可能不一致。加上有限的机制词表、{dataset["query_families"]} 个合成 family 和充分公开的风险类别，较小甚至为负的残余改进只能作为原型诊断。</div>
<p class="note">数据来源：{ref_link}、{method_link}。pp 表示百分点；全部方法和失败执行均保留在后续表格中。</p>
</section>

<section><span class="section-index">02 / 方法比较</span><h2>成功率与支持集成本同时呈现</h2>
<p>条形长度表示 query 成功率。右侧显示为该方法实际发生的支持集执行次数与支持操作单位；冻结方法的支持集成本为 0，但学习方法仍使用了离线源数据与拟合计算。</p>
<div class="legend"><span><i class="dot" style="background:#087f8c"></i>条件增益构建</span><span><i class="dot" style="background:#b15c25"></i>Scalar / 规则控制</span><span><i class="dot" style="background:#7361aa"></i>支持集搜索</span><span><i class="dot" style="background:#6aa9a9"></i>消融</span></div>
<div class="chart">{success_svg(reference)}</div>
<p class="note">SIMULATOR：图中没有 LLM 推理结果。这里不以环境执行次数推导美元、token 或真实系统延迟。来源：{method_link}。</p>
{method_table(reference)}
<p class="note">最大兼容组合按预声明机制顺序选取，最多 2 个；它不是同时开启全部 4 个机制。Scalar 和最近成功端点均为本原型中的简化控制。支持操作包含被计费的本地操作，因此不必等于工具调用数。</p>
</section>

<section><span class="section-index">03 / 四格诊断</span><h2>构建收益与后续搜索收益分开读</h2>
<p>每个单元格展示最终 query 成功率，支持成本覆盖该单元格全部目标实例与构建种子。冻结行的 query 结果在两种搜索器面板中重复引用，不是新增独立实验。</p>
<div class="matrices">{matrix(reference, "greedy")}{matrix(reference, "beam")}</div>
<p class="note">数据来源：{four_link}。B＝通用初始，U＝条件构建，R＝在支持集上搜索后的冻结结果。</p>
{delta_table(reference, sensitivity)}
<div class="callout teal"><strong>判断“替代 RSI”还缺少的证据：</strong> 需要目标定义匹配、能够确实改善初始 agent 的有效优化器，并在公平总预算下证明初始质量接近该对照、残余改善受到限制且生命周期成本更低。本轮四格仅覆盖固定 4 种机制、最多 2 种组合的搜索，不满足上述完整结论。</div>
</section>

<section><span class="section-index">04 / 预算敏感性</span><h2>把候选预算提高到 11 后发生什么</h2>
<p>两次运行使用相同源数据、任务角色划分、构建与执行种子、模型设置。预算单位是对一个候选机制组合进行完整支持集评估；初始候选也占一个单位。11 是最多两机制时的完整词表大小，搜索提前停止或接口限制仍可能使实际评估数量低于上限。</p>
{budget_table(reference, sensitivity)}
<div class="chart">{support_svg(reference, sensitivity)}</div>
<p class="note">来源：{ref_link} 与 {budget_link}。提高候选预算不会自动修复支持任务与目标实例的分布不匹配，也不等价于增加任意代码编辑能力。</p>
{positive_control(reference)}
</section>

<section><span class="section-index">05 / 不确定性</span><h2>保留配对结构，限制区间解释</h2>
<p>差值按同一任务、构建种子与执行种子配对，并以完整 family 为单位重采样。下列区间是已保存的描述性 family bootstrap 区间；只有 {dataset["query_families"]} 个共享同一生成器的合成 family，不能据此声称具有确认性检验效能。</p>
{comparison_table(reference)}
<p class="note">默认使用 {number(config["bootstrap_samples"])} 次 bootstrap。区间未作多重比较校正；本报告不据此宣布统计显著、等效或非劣。增加执行种子不会增加独立 family 数。来源：{ref_link} 中 paired_comparisons。</p>
</section>

<section><span class="section-index">06 / 数据与生命周期账本</span><h2>离线采集、支持搜索、最终执行都有记录</h2>
<div class="two-col"><div><h3>任务与重复</h3><dl class="facts">
<div><dt>源任务 / family</dt><dd>{dataset["source_tasks"]} / {dataset["source_families"]}</dd></div>
<div><dt>目标支持任务</dt><dd>{dataset["support_tasks"]}</dd></div>
<div><dt>目标 query 任务 / family</dt><dd>{dataset["query_tasks"]} / {dataset["query_families"]}</dd></div>
<div><dt>dev 任务（未用于训练或调参）</dt><dd>{dev_count}</dd></div>
<div><dt>其中仅用于机制正控</dt><dd>{dev_control_count}</dd></div>
<div><dt>每任务：构建种子 × query 种子</dt><dd>{len(config["construction_seeds"])} × {len(config["query_execution_seeds"])}</dd></div>
<div><dt>生成的冻结 artifact</dt><dd>{number(artifact_count)}</dd></div>
</dl></div><div><h3>源配对干预</h3><dl class="facts">
<div><dt>配对记录总数</dt><dd>{number(source_rows)}</dd></div>
<div><dt>成功率方向为正的记录</dt><dd>{number(positives)}</dd></div>
<div><dt>成功率方向为负的记录</dt><dd>{number(negatives)}</dd></div>
<div><dt>成功状态相同的记录</dt><dd>{number(neutral)}</dd></div>
<div><dt>其中 no-op 配对记录</dt><dd>{number(dataset["no_op_rows"])}</dd></div>
<div><dt>每个源任务的执行种子数</dt><dd>{len(config["source_execution_seeds"])}</dd></div>
</dl></div></div>
<p class="note">正 / 负方向按二元 success 的成对变化计算；“成功状态相同”不代表操作成本、部分完成度或事件轨迹也相同。共享 baseline 的引用不重复计作物理执行。</p>
{accounting_table(reference)}
<p><strong>成本口径：</strong> <code>tool_steps</code> 统计工具调用，<code>cost_units</code> 另外包含被计费的 retry 决策、幂等键派生等本地操作。当前学习和搜索效用是成功率减工具调用惩罚，不直接优化总操作单位。源数据采集在全局账本中计费一次，不能因为部署时没有支持搜索而将其视为零成本。</p>
<p>冻结构造器的目标环境执行总数为 <strong>{number(accounting["construction_environment_executions"])}</strong>。构建、模型拟合的耗时及候选评分数在原始结果中单独记录；这些本地墙钟测量不构成真实 LLM 成本测量。摊销后的成本结论还需声明未来部署任务量和各方法的冷启动分摊规则。</p>
<p class="note">本次默认运行的整体墙钟时间记录为 {number(reference["elapsed_seconds"], 2)} 秒，仅用于复现诊断。来源：{ref_link} 中 accounting / training 及同目录 execution_ledger.jsonl。</p>
</section>

<section><span class="section-index">07 / 解释边界与复现依据</span><h2>这次运行证明了哪些链路</h2>
<ol>
<li><strong>机制真实执行。</strong> 环境有状态、工具调用、超时后的已提交副作用、重复写入、worker 重启与恢复事件；retry、幂等、checkpoint、schema adapter 改变可执行 harness 行为。执行者是有限状态 actor。</li>
<li><strong>构建阶段只看公共规格。</strong> 实际故障是否发生与发生位置在执行时由任务身份和隐藏执行种子确定；公共风险类别仍是较强信息，规则控制因此必须保留。</li>
<li><strong>隔离的是任务身份和监督角色。</strong> 源 / dev / test 的 family 名称不重叠，但都共享同一事务生成器；这不是跨真实领域泛化。支持集按公开接口能力预先选择，query 保留接口受限任务和未见组合。</li>
<li><strong>Query 不参与优化。</strong> source-only 训练、支持集候选评估、最终冻结 artifact 的 query 评估分别记录。失败执行计入分母；没有按效果剔除任务或方法。</li>
<li><strong>冻结可被核验，但不保证语义正确。</strong> agent 源码、manifest 与依赖摘要提供相对于可信外部 digest 的篡改检测；静态语法和接口检查不等于行为正确性、代码沙箱或数字签名。</li>
</ol>
<h3>结果文件与 SHA-256</h3>
<p class="note">报告生成器读取下列文件，校验 CSV 与主 JSON 一致后渲染；不运行实验、不修改数据、不选择更好的一次结果。页面所有样式与 SVG 均已内嵌，无需网络即可查看。</p>
<div class="table-wrap"><table class="provenance"><thead><tr><th>输入文件</th><th>文件 SHA-256</th></tr></thead><tbody>{provenance}</tbody></table></div>
<p class="note">源配对数据摘要：<span class="hash">{esc(reference["source_data_sha256"])}</span></p>
</section>
<footer>Agent Construction Lab · 合成实验结果报告 · SIMULATOR / NO LLM EVIDENCE<br>图表显示已保存的观测；pp 为百分点。完整原始结果与程序共同构成复现依据。</footer>
</main></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the saved simulator pilot as a standalone Chinese HTML report")
    parser.add_argument("--reference", type=Path, default=ROOT / "runs" / "reference", help="Directory containing the default-budget run")
    parser.add_argument("--budget11", type=Path, default=ROOT / "runs" / "budget11", help="Directory containing candidate_budget=11 sensitivity run")
    parser.add_argument("--output", type=Path, help="HTML path (default: reference/report.html)")
    args = parser.parse_args()
    reference_dir, sensitivity_dir = args.reference.resolve(), args.budget11.resolve()
    output = (args.output or reference_dir / "report.html").resolve()
    files = [reference_dir / "summary.json", reference_dir / "method_summary.csv",
             reference_dir / "four_cells.csv", sensitivity_dir / "summary.json"]
    try:
        reference = load_json(files[0])
        sensitivity = load_json(files[3])
        verify_csv(reference["methods"], load_csv(files[1]), ("method",), "method_summary.csv")
        verify_csv(reference["four_cells"], load_csv(files[2]), ("optimizer", "construction", "deployment_rsi"), "four_cells.csv")
        verify_comparable(reference, sensitivity)
        document = render(reference, sensitivity, files, output)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(f"Report written: {output}")


if __name__ == "__main__":
    main()
