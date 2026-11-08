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
    if not plan:
        lines.extend(
            ["本次未生成可用草案。", "", "原因：" + _escape(result.get("issue") or "unknown")]
        )
    else:
        if plan["status"] == "invalid_plan":
            lines.extend(["校验发现冲突，以下安排不能作为可执行行程。", ""])
        day = None
        for activity in plan["activities"]:
            if activity["day"] != day:
                day = activity["day"]
                lines.extend([f"## 第 {day} 天", ""])
            interval = _time(activity["start_minute"]) + "–" + _time(activity["end_minute"])
            lines.append(f"- {interval}：{_escape(activity['place_name'])}。")
            if activity["address"]:
                lines.append("  地址：" + _escape(activity["address"]) + "。")
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
