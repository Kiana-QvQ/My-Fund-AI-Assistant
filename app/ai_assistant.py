"""Small, provider-agnostic AI layer for planning and trading journals.

The assistant produces advice or drafts only. It does not connect to a broker
and it cannot place orders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "portfolio_policy.json"
INTEGRATIONS_PATH = ROOT / "config" / "project_integrations.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_integrations() -> dict:
    return json.loads(INTEGRATIONS_PATH.read_text(encoding="utf-8"))


def load_upstream_skill_context() -> str:
    """Load selected upstream skills without modifying their source."""
    integrations = load_integrations()
    sections = []
    for project, settings in integrations.get("enabled_projects", {}).items():
        for relative_path in settings.get("skill_files", []):
            path = ROOT / relative_path
            if not path.is_file():
                sections.append(f"[缺失] {project}: {relative_path}")
                continue
            content = path.read_text(encoding="utf-8")
            excerpt_chars = int(os.environ.get("AI_SKILL_EXCERPT_CHARS", "1800"))
            excerpt = content[:excerpt_chars]
            if len(content) > len(excerpt):
                excerpt += "\n[上游 Skill 原文已截取；以本文件规则为准，不代表完整工具执行。]"
            sections.append(f"### {project} / {relative_path}\n{excerpt}")
    return "\n\n".join(sections)


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


def load_runtime_config() -> dict:
    """Load provider settings, with environment variables taking precedence."""
    config_path = Path(
        os.environ.get("AI_CONFIG_FILE", str(CODEX_CONFIG_PATH))
    ).expanduser()
    config = {}
    if config_path.is_file():
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            config = {}

    provider_name = os.environ.get("AI_PROVIDER") or config.get("model_provider", "")
    provider = config.get("model_providers", {}).get(provider_name, {})
    provider_base_url = (
        provider.get("base_url")
        or config.get("openai_base_url")
        if provider_name.lower() == "openai"
        else provider.get("base_url")
    )
    configured_wire_api = (
        provider.get("wire_api")
        or config.get("wire_api")
        or ("responses" if provider_name.lower() == "openai" else "chat_completions")
    )
    return {
        "base_url": os.environ.get("AI_BASE_URL")
        or provider_base_url
        or "https://api.openai.com/v1",
        "model": os.environ.get("AI_MODEL") or config.get("model", ""),
        "wire_api": os.environ.get("AI_WIRE_API")
        or configured_wire_api,
    }


def build_prompt(mode: str, user_input: str) -> str:
    policy = json.dumps(load_policy(), ensure_ascii=False, indent=2)
    integrations = json.dumps(load_integrations(), ensure_ascii=False, indent=2)
    upstream_skills = load_upstream_skill_context()
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

本次实际加载的上游 Skill 内容：
{upstream_skills}

任务：
{task}

用户输入：
{user_input}

输出使用中文，明确标注“事实”“推断”“建议”“需要人工确认”。
报告末尾必须增加“实际调用的上游 Skill”一节，逐一列出本次提供上下文的文件；
不得声称调用了没有出现在上文中的工具或数据源。"""


def ask_ai(prompt: str) -> str:
    runtime = load_runtime_config()
    base_url = runtime["base_url"].rstrip("/")
    api_key = load_api_key()
    model = runtime["model"]
    wire_api = runtime["wire_api"]
    if not base_url or not api_key or not model:
        raise SystemExit(
            "未配置 AI Key 或 AI_MODEL。请设置 AI_API_KEY/OPENAI_API_KEY，"
            "或确保 ~/.codex/auth.json 和 ~/.codex/config.toml 存在。"
        )

    if wire_api == "responses":
        request_path = "/responses"
        body = {
            "model": model,
            "instructions": "你是只提供研究、规划和复盘建议的投资助理，不执行交易。",
            "input": prompt,
        }
        reasoning_effort = os.environ.get("AI_REASONING_EFFORT")
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
    else:
        request_path = "/chat/completions"
        body = {
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
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{request_path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    proxy = os.environ.get("AI_HTTPS_PROXY")
    opener = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        if proxy
        else urllib.request.build_opener()
    )
    try:
        with opener.open(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"AI 服务返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"无法连接 AI 服务：{base_url}。"
            "请检查 AI_BASE_URL、网络代理，或设置 AI_HTTPS_PROXY。"
        ) from exc
    except TimeoutError as exc:
        raise SystemExit(
            f"连接 AI 服务超时：{base_url}。"
            "请检查网络代理或更换可访问的 OpenAI-compatible 网关。"
        ) from exc

    if wire_api == "responses":
        if isinstance(result.get("output_text"), str):
            return result["output_text"]
        texts = []
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    texts.append(content.get("text", ""))
        if texts:
            return "\n".join(texts)
        raise SystemExit("Responses API 返回成功，但没有找到文本输出。")
    return result["choices"][0]["message"]["content"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
