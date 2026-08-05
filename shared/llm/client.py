"""LLM 统一调用客户端"""

import json
import httpx

from shared.utils import config

DEEPSEEK_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"  # V3, 性价比最高
REASONING_MODEL = "deepseek-reasoner"  # R1, 复杂推理时使用


class LLMClient:
    """DeepSeek API 客户端"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_BASE,
        default_model: str = DEFAULT_MODEL,
    ):
        self.api_key = api_key or config.DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY 未设置。请在 .env 文件中配置。\n"
                "获取: https://platform.deepseek.com → API Keys"
            )
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.1,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> str:
        """发送对话请求，返回模型回复文本"""
        import time as _time
        from shared.utils.logger import get_current_logger

        logger = get_current_logger()
        t0 = _time.time()
        model_name = model or self.default_model
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        # prompt 摘要（system 前 300 字符 + user 前 300 字符）
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            logger.llm_call(
                model_name, prompt_chars=prompt_chars, response=content,
                duration_ms=(_time.time() - t0) * 1000,
                temperature=temperature, max_tokens=max_tokens, json_mode=json_mode,
            )
            return content
        except Exception as e:
            logger.llm_call(
                model_name, prompt_chars=prompt_chars, response="",
                error=str(e)[:300], duration_ms=(_time.time() - t0) * 1000,
            )
            raise

    def extract_json(
        self,
        system_prompt: str,
        user_input: str,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> dict:
        """发送请求并解析 JSON 返回"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        response = self.chat(messages, model=model,
                            temperature=temperature, json_mode=True,
                            max_tokens=max_tokens)
        return json.loads(response)

    def health_check(self) -> bool:
        try:
            self.chat([{"role": "user", "content": "Hi"}], max_tokens=10)
            return True
        except Exception:
            return False
