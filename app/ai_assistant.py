"""Small, provider-agnostic AI layer for planning and trading journals.

The assistant produces advice or drafts only. It does not connect to a broker
and it cannot place orders.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "portfolio_policy.json"


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def build_prompt(mode: str, user_input: str) -> str:
    policy = json.dumps(load_policy(), ensure_ascii=False, indent=2)
    task = {
        "plan": "根据投资政策和输入，生成下一期定投/再平衡计划。必须列出依据、仓位影响、风险、待人工确认事项。",
        "journal": "把输入整理成一篇交易日记草稿，区分事实、当时判断、情绪、结果和下次改进。",
        "review": "复盘输入中的交易，检查是否违反投资政策，指出证据不足之处，不得编造行情数据。",
    }[mode]
    return f"""你是一个保守型指数投资助理。你不能替用户下单，也不能承诺收益。

投资政策：
{policy}

任务：
{task}

用户输入：
{user_input}

输出使用中文，明确标注“事实”“推断”“建议”“需要人工确认”。"""


def ask_ai(prompt: str) -> str:
    base_url = os.environ.get("AI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("AI_API_KEY", "")
    model = os.environ.get("AI_MODEL", "")
    if not base_url or not api_key or not model:
        raise SystemExit(
            "未配置 AI_BASE_URL、AI_API_KEY 或 AI_MODEL。"
            "请复制 config/.env.example 到本地环境后设置，或使用本地 OpenAI-compatible 服务。"
        )

    payload = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "你是只提供研究、规划和复盘建议的投资助理，不执行交易。",
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def main() -> None:
    parser = argparse.ArgumentParser(description="交易规划与日记 AI 助手")
    parser.add_argument("--mode", choices=("plan", "journal", "review"), required=True)
    parser.add_argument("--input", required=True, help="输入文本或输入文件路径")
    args = parser.parse_args()

    input_path = Path(args.input)
    user_input = (
        input_path.read_text(encoding="utf-8")
        if input_path.is_file()
        else args.input
    )
    print(ask_ai(build_prompt(args.mode, user_input)))


if __name__ == "__main__":
    main()

