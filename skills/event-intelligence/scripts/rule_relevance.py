"""Rule-based topical relevance filter for deepseekdata event candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RuleSet:
    include_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    strong_include_terms: tuple[str, ...] = ()
    require_include: bool = False
    reason_label: str = "default"


RULES: dict[str, RuleSet] = {
    "储能": RuleSet(
        include_terms=(
            "储能",
            "电池储能",
            "新型储能",
            "光伏配储",
            "配储",
            "电网侧",
            "工商业储能",
            "液流电池",
            "钠电",
            "储能电池",
            "储能配置",
        ),
        exclude_terms=(
            "长江存储",
            "长鑫",
            "长存",
            "武汉新芯",
            "存储芯片",
            "存储上游材料",
            "dram",
            "nand",
            "晶圆",
            "半导体",
            "电子气体",
            "扩产红利",
        ),
        strong_include_terms=("储能", "储能配置", "新型储能", "工商业储能", "储能电池"),
        require_include=True,
        reason_label="storage-energy-context",
    ),
    "锂电": RuleSet(
        include_terms=(
            "锂电",
            "电池",
            "锂电池",
            "正极",
            "负极",
            "隔膜",
            "电解液",
            "固态电池",
            "动力电池",
            "储能电池",
            "锂盐",
            "锂矿",
        ),
        exclude_terms=(
            "存储芯片",
            "长鑫",
            "长江存储",
            "武汉新芯",
            "电子气体",
            "半导体材料",
            "磷化铟",
            "东方钽业",
            "存储上游材料",
            "电源管理ic",
        ),
        strong_include_terms=("锂电", "动力电池", "固态电池", "储能电池"),
        require_include=True,
        reason_label="lithium-battery-context",
    ),
    "创新药": RuleSet(
        include_terms=(
            "创新药",
            "药企",
            "临床",
            "治疗",
            "生物药",
            "医药研发",
            "管线",
            "fda",
            "nmpa",
            "适应症",
            "医药",
            "药业",
        ),
        exclude_terms=("药水", "tgv", "电子化学品", "pcb", "半导体工艺液", "工艺液"),
        strong_include_terms=("创新药", "临床", "治疗", "生物药", "管线", "适应症"),
        require_include=True,
        reason_label="innovative-drug-context",
    ),
    "医疗器械": RuleSet(
        include_terms=(
            "医疗器械",
            "诊断设备",
            "ivd",
            "手术机器人",
            "影像设备",
            "医疗耗材",
            "诊断",
            "医疗",
            "器械",
        ),
        exclude_terms=("半导体设备", "通用机械", "永磁", "机器人", "设备市场"),
        strong_include_terms=("医疗器械", "诊断设备", "ivd", "手术机器人", "影像设备", "医疗耗材"),
        require_include=True,
        reason_label="medical-device-context",
    ),
    "光伏": RuleSet(
        include_terms=(
            "光伏",
            "光伏组件",
            "电池片",
            "硅片",
            "逆变器",
            "topcon",
            "hjt",
            "bc电池",
            "xbc",
            "背接触",
            "并网",
            "电价",
            "配储",
        ),
        exclude_terms=("光通信", "光互联", "硅光", "cpo", "ocs", "光模块", "光器件", "光学"),
        strong_include_terms=("光伏", "光伏组件", "电池片", "硅片", "逆变器", "topcon", "hjt", "并网"),
        require_include=True,
        reason_label="solar-pv-context",
    ),
    "消费电子": RuleSet(
        include_terms=(
            "手机",
            "苹果",
            "三星手机",
            "galaxy",
            "可穿戴",
            "终端",
            "mr",
            "ar",
            "面板",
            "声学",
            "摄像头模组",
            "消费电子",
        ),
        exclude_terms=(
            "电子气体",
            "氢氟酸",
            "pcb检测设备",
            "算电协同",
            "光上游透镜",
            "存储上游材料",
        ),
        strong_include_terms=("手机", "苹果", "可穿戴", "终端", "mr", "ar", "消费电子"),
        require_include=True,
        reason_label="consumer-electronics-terminal-context",
    ),
}


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).lower()


def _combined_text(item: dict) -> str:
    metadata = item.get("analysisMetadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    core_logic = metadata.get("core_logic_output", {})
    if not isinstance(core_logic, dict):
        core_logic = {}
    ic_report = metadata.get("ic_report_v10_output", {})
    if not isinstance(ic_report, dict):
        ic_report = {}
    logic_validation = metadata.get("logic_validation_output", {})
    if not isinstance(logic_validation, dict):
        logic_validation = {}

    parts = [
        item.get("title"),
        item.get("compliantTitle"),
        item.get("eventTitle"),
        item.get("oneSentenceSummary"),
        item.get("investmentLogic"),
        core_logic.get("original_summary"),
        core_logic.get("investment_logic"),
        ic_report.get("summary"),
        logic_validation.get("conclusion"),
    ]
    return "\n".join(_normalize_text(part) for part in parts if part)


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    matched = []
    for term in terms:
        if _normalize_text(term) in text:
            matched.append(term)
    return matched


def apply_rule_filter(keyword: str, item: dict) -> dict:
    """Return rule-filter metadata for one event candidate."""

    rules = RULES.get(keyword)
    if rules is None:
        return {
            "ruleFilterPassed": True,
            "ruleFilterReason": "no keyword-specific rule; passed by score filter only",
            "matchedIncludeTerms": [],
            "matchedExcludeTerms": [],
        }

    text = _combined_text(item)
    matched_include = _matched_terms(text, rules.include_terms)
    matched_exclude = _matched_terms(text, rules.exclude_terms)
    matched_strong_include = _matched_terms(text, rules.strong_include_terms)

    if matched_exclude and not matched_strong_include:
        return {
            "ruleFilterPassed": False,
            "ruleFilterReason": f"{rules.reason_label}: excluded by off-topic terms without strong include",
            "matchedIncludeTerms": matched_include,
            "matchedExcludeTerms": matched_exclude,
        }

    if rules.require_include and not matched_include:
        return {
            "ruleFilterPassed": False,
            "ruleFilterReason": f"{rules.reason_label}: missing required topical include terms",
            "matchedIncludeTerms": matched_include,
            "matchedExcludeTerms": matched_exclude,
        }

    if matched_exclude and matched_strong_include:
        reason = f"{rules.reason_label}: passed with strong include; review exclude overlap"
    else:
        reason = f"{rules.reason_label}: passed"

    return {
        "ruleFilterPassed": True,
        "ruleFilterReason": reason,
        "matchedIncludeTerms": matched_include,
        "matchedExcludeTerms": matched_exclude,
    }
