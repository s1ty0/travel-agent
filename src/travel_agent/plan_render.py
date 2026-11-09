"""Render server-validated proposal fields; never turn unknowns into promises."""

import re
from decimal import Decimal
from urllib.parse import quote


def _escape(value):
    text = " ".join(str(value).splitlines())
    return re.sub(r"([\\`*_{}\[\]()#+!|<>])", r"\\\1", text)


def _time(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def render_markdown(result):
    lines = ["# 旅行规划草案", "", "建议时间未经营业、预约或票务核验。当前没有执行购买。", ""]
    plan = result.get("plan")
    if result.get("status") == "stopped" and plan:
        lines.append(
            "修正过程已停止；下方保留最近一次未通过方案。原因："
            + _escape(result.get("issue") or "unknown")
            + "。"
        )
    if result.get("replans", 0):
        lines.append(f"本次已请求 {result['replans']} 次有限修正，沿用原服务预算和调用上限。")
    if not plan:
        lines.extend(
            ["本次未生成可用草案。", "", "原因：" + _escape(result.get("issue") or "unknown")]
        )
    else:
        if plan["status"] == "invalid_plan":
            lines.extend(["校验发现冲突，以下安排不能作为可执行行程。", ""])
        limit = plan.get("constraints", {}).get("max_sightseeing_places")
        if limit is not None:
            lines.append(f"本次景点上限：全程最多 {limit} 处不同景点，按地点来源 ID 计数。")
        day = None
        for activity in plan["activities"]:
            if activity["day"] != day:
                day = activity["day"]
                lines.extend([f"## 第 {day} 天", ""])
            interval = _time(activity["start_minute"]) + "–" + _time(activity["end_minute"])
            lines.append(f"- {interval}：{_escape(activity['place_name'])}。")
            if activity["address"]:
                lines.append("  地址：" + _escape(activity["address"]) + "。")
            for claim, review in zip(
                activity.get("claims", []), activity.get("claim_reviews", []), strict=False
            ):
                value = "需要预约" if claim["value"] else "无需预约"
                state = "引用证据被拒绝" if review["status"] == "rejected_evidence" else "待核实"
                lines.append(f"  预约要求主张：{value}（{state}；不表示已预约或有名额）。")
                lines.append(
                    "  文本支持："
                    + _escape(review["semantics"]["status"])
                    + "；日期范围："
                    + _escape(review["date"]["status"])
                    + "；来源身份未认证。"
                )
            for comparison in activity.get("evidence_comparisons", []):
                messages = {
                    "conflict": "资料对出行日的预约要求有明确文字冲突，核实后再采用。",
                    "potential_conflict": "预约资料说法不一致，适用日期不充分，需要核实。",
                    "different_textual_periods": "不同说法注明了不同适用时段，需先确认出行日。",
                    "no_detected_conflict": "有限句式内未发现适用冲突，仍需核实来源和规则。",
                    "insufficient": "缺少足以比较本次预约要求的正文依据，不能据此确认要求。",
                    "incomplete": "资料比较未完整完成，不能据此确认要求。",
                }
                lines.append("  多份资料核查：" + messages[comparison["status"]])
                if not comparison["complete_within_bounds"]:
                    lines.append("  资料比较未完整完成，仍可能有未检查的信息。")
                lines.append("  引用数量不代表独立证明，现有证据仍需核实。")
        lines.extend(
            [
                "",
                "## 待核实",
                "",
                "旅行费用尚不完整，不能确认满足旅行预算；营业、预约、住宿、无障碍和跨城交通仍需核实。",
            ]
        )
        for check in plan["checks"]:
            if check["status"] == "fail":
                lines.append("- 校验冲突：" + _escape(check["rule"]) + "。")
            elif check["rule"] == "transfer_duration" and check["status"] == "unknown":
                lines.append("- 相邻地点交通时间尚未查询，不保证可以按时抵达。")
        if plan["missing_fields"]:
            lines.append("- 需求还有待补充或确认的条件，当前保留为草案。")
        lines.extend(["", "## 来源", ""])
        for document in result.get("reference_documents", []):
            lines.append(
                f"- 用户导入资料：{_escape(document['title'])}；"
                f"类型：{_escape(document['acquisition'])}，未独立核实。"
            )
            lines.append("  导入时间：" + _escape(document["imported_at"]) + "。")
        for activity in plan["activities"]:
            for citation in activity.get("citations", []):
                lines.append(
                    "- 引文（待核实）：" + _escape(citation["quote"]) + "；"
                    "来源 ID：" + _escape(citation["source_id"]) + "。"
                )
        seen = set()
        for batch in result["sources"]:
            for hit in batch["hits"]:
                if hit["source_id"] in seen:
                    continue
                seen.add(hit["source_id"])
                if hit["evidence_kind"] == "search_snippet":
                    link = quote(hit["url"], safe=":/?&=#%+@;,")
                    lines.append(f"- [{_escape(hit['title'])}]({link})（搜索摘要，未核实原文）。")
                elif hit["evidence_kind"] == "provider_place_record":
                    lines.append(
                        f"- 高德地点：{_escape(hit['name'])}；ID：{_escape(hit['poi_id'])}。"
                    )
                else:
                    lines.append(
                        f"- 高德步行估算：{hit['distance_meters']} 米、"
                        f"{hit['duration_seconds']} 秒。"
                    )
            lines.append(
                "  获取时间："
                + _escape(batch["retrieved_at"])
                + "；状态："
                + _escape(batch["status"])
                + "。"
            )
    cost = result["service_cost"]
    if result.get("page_reads"):
        lines.extend(["", "## 网页读取依据", ""])
        lines.append("正文来自供应商转换，来源身份、语义和日期尚未核实；不等于确认营业或可预约。")
        for read in result["page_reads"]:
            link = quote(read["read_policy"]["requested_url"], safe=":/?&=#%+@;,")
            lines.append(f"- [请求网页]({link})；读取状态：{_escape(read['status'])}。")
            if read["page"]:
                lines.append(
                    "  来源 ID："
                    + _escape(read["page"]["source_id"])
                    + "；获取时间："
                    + _escape(read["page"]["retrieved_at"])
                    + "。"
                )
    estimated = Decimal(cost["estimated_upper"]) / 1000000
    cap = Decimal(cost["cap"]) / 1000000
    lines.extend(
        ["", f"已知服务用量的费用上界估算：{estimated:.6f} 元；上限 {cap:.2f} 元。实际账单未核对。"]
    )
    if cost["status"] == "uncertain":
        reserved = Decimal(cost["reserved_unsettled"]) / 1000000
        lines.append(f"未结算预留：{reserved:.6f} 元。")
        lines.append("部分调用费用不明，系统已停止；预留金额尚未释放。")
    return "\n".join(lines) + "\n"
