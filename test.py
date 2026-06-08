import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Load .env from the project directory (same logic as main.py)
PROJECT_DIR = Path(__file__).parent
ENV_PATH = PROJECT_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
api_base = os.getenv("DEEPSEEK_API_BASE", "").strip()

if not api_key:
    raise RuntimeError(
        "Missing DEEPSEEK_API_KEY. Add it to .env in the project root "
        "or export it in your terminal before running test.py."
    )

try:
    client = OpenAI(api_key=api_key, api_base=api_base) if api_base else OpenAI(
        api_key=api_key)
except TypeError:
    # Older openai client versions may not accept api_base in constructor
    if api_base:
        os.environ["OPENAI_API_BASE"] = api_base
    client = OpenAI(api_key=api_key)

resp = client.chat.completions.create(
    model=os.getenv("DEEPSEEK_MODEL_NAME", "gpt-4o-mini"),
    messages=[{"role": "user", "content": "Say hello"}],
    temperature=0.2,
)

print("OK", resp.choices[0].message if hasattr(
    resp.choices[0], "message") else resp)
