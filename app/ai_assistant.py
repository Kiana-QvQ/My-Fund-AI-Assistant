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
INTEGRATIONS_PATH = ROOT / "config" / "project_integrations.json"


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_integrations() -> dict:
    return json.loads(INTEGRATIONS_PATH.read_text(encoding="utf-8"))


def load_api_key() -> str:
    """Read a key at runtime without copying it into project files."""
    key = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key

    auth_file = Path(
        os.environ.get("AI_AUTH_FILE", "~/.codex/auth.json")
    ).expanduser()
    if auth_file.is_file():
        try:
            auth = json.loads(auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(auth.get("OPENAI_API_KEY", ""))
    return ""


def build_prompt(mode: str, user_input: str) -> str:
    policy = json.dumps(load_policy(), ensure_ascii=False, indent=2)
    integrations = json.dumps(load_integrations(), ensure_ascii=False, indent=2)
    task = {
        "plan": "根据投资政策和输入，生成下一期定投/再平衡计划。必须列出依据、仓位影响、风险、待人工确认事项。",
        "journal": "把输入整理成一篇交易日记草稿，区分事实、当时判断、情绪、结果和下次改进。",
        "review": "复盘输入中的交易，检查是否违反投资政策，指出证据不足之处，不得编造行情数据。",
    }[mode]
    return f"""你是一个保守型指数投资助理。你不能替用户下单，也不能承诺收益。

投资政策：
{policy}

项目能力边界：
{integrations}

任务：
{task}

用户输入：
{user_input}

输出使用中文，明确标注“事实”“推断”“建议”“需要人工确认”。"""


def ask_ai(prompt: str) -> str:
    base_url = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = load_api_key()
    model = os.environ.get("AI_MODEL", "")
    if not base_url or not api_key or not model:
        raise SystemExit(
            "未配置 AI Key 或 AI_MODEL。请设置 AI_API_KEY/OPENAI_API_KEY，"
            "或确保 ~/.codex/auth.json 存在；再设置 AI_MODEL。"
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
    parser.add_argument("--output", help="将结果保存到指定 Markdown 文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用 AI")
    args = parser.parse_args()

    input_path = Path(args.input)
    user_input = (
        input_path.read_text(encoding="utf-8")
        if input_path.is_file()
        else args.input
    )
    prompt = build_prompt(args.mode, user_input)
    if args.dry_run:
        print(prompt)
        return

    result = ask_ai(prompt)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.rstrip() + "\n", encoding="utf-8")
        print(f"已保存报告：{output_path}")
    else:
        print(result)


if __name__ == "__main__":
    main()
