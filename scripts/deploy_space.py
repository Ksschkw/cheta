#!/usr/bin/env python3
"""Create the Hugging Face Docker Space and set its runtime secrets.

The Space is the always-on host. It gives a public HTTPS URL, which is what the
Telegram webhook mode needs and what the web widget needs to be reachable from a
phone. Polling from a laptop works, but only while that laptop is awake.

    python scripts/deploy_space.py                 # create and configure
    python scripts/deploy_space.py --dry-run       # show what would be set

Requires HF_TOKEN in .env or the environment, with write scope. Only names of
secrets are printed, never values.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.config import load_environment  # noqa: E402

SPACE_NAME = "ranti"

# Everything the running container needs. HF_TOKEN is deliberately excluded: the
# deployed app never calls the Hugging Face API, so it does not need the token,
# and not shipping it keeps the blast radius small if the Space is compromised.
SECRET_KEYS = (
    "MEMWAL_PRIVATE_KEY",
    "MEMWAL_ACCOUNT_ID",
    "MEMWAL_SERVER_URL",
    "MEMWAL_NAMESPACE_PREFIX",
    "MEMWAL_TIMEOUT_SECONDS",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Create and configure the Hugging Face Space.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = parser.parse_args()

    env = load_environment()
    token = (env.get("HF_TOKEN") or "").strip()
    if not token:
        print("[FAIL] HF_TOKEN is empty. Create a Write token at https://huggingface.co/settings/tokens")
        return 1

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("[FAIL] huggingface_hub is not installed. Run: pip install 'huggingface_hub[cli]'")
        return 1

    api = HfApi(token=token)
    try:
        account = api.whoami()
    except Exception as error:  # noqa: BLE001 - reporting layer
        print(f"[FAIL] HF_TOKEN was rejected: {type(error).__name__}: {error}")
        return 1

    user = account.get("name") or account.get("user")
    if not user:
        print("[FAIL] could not determine the account name from HF_TOKEN")
        return 1

    repo_id = f"{user}/{SPACE_NAME}"
    # Hugging Face lowercases the subdomain regardless of the account casing.
    base_url = f"https://{user.lower()}-{SPACE_NAME}.hf.space"

    print("Hugging Face Space deployment")
    print(f"  account : {user}")
    print(f"  space   : {repo_id}")
    print(f"  url     : {base_url}")
    print()

    if args.dry_run:
        print("[OK] dry run: would create the Space and set these secrets:")
        for key in SECRET_KEYS:
            present = "set" if (env.get(key) or "").strip() else "MISSING"
            print(f"    {key:<26} {present}")
        print(f"    {'PUBLIC_BASE_URL':<26} = {base_url}")
        return 0

    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            exist_ok=True,
            private=False,
        )
        print("[OK] Space exists")
    except Exception as error:  # noqa: BLE001 - reporting layer
        if "402" in str(error) or "Payment Required" in str(error):
            print("[FAIL] Hugging Face refused: hosting a Docker Space on free cpu-basic")
            print("       now requires a PRO subscription. Static Spaces are still free,")
            print("       but a Static Space cannot run this Python service.")
            print("       Free alternative: a Render web service, which needs no card.")
            print("       See deployment/README.md.")
            return 1
        print(f"[FAIL] could not create the Space: {type(error).__name__}: {error}")
        return 1

    missing: list[str] = []
    for key in SECRET_KEYS:
        value = (env.get(key) or "").strip()
        if not value:
            missing.append(key)
            print(f"[WARN] {key} is empty in .env and was not set")
            continue
        try:
            api.add_space_secret(repo_id=repo_id, key=key, value=value)
            print(f"[OK] secret {key}")
        except Exception as error:  # noqa: BLE001 - reporting layer
            print(f"[FAIL] could not set {key}: {type(error).__name__}: {error}")
            return 1

    # The webhook URL is derived from PUBLIC_BASE_URL, so it must be the Space.
    api.add_space_secret(repo_id=repo_id, key="PUBLIC_BASE_URL", value=base_url)
    print("[OK] secret PUBLIC_BASE_URL")

    print()
    if missing:
        print(f"[WARN] {len(missing)} secret(s) were missing and the Space will degrade:")
        for key in missing:
            print(f"       {key}")
    print("[OK] Space configured. Next:")
    print("     1. git push the repository to the Space remote")
    print(f"        git remote add space https://huggingface.co/spaces/{repo_id}")
    print(f"     2. wait for the build at https://huggingface.co/spaces/{repo_id}")
    print("     3. register the Telegram webhook:")
    print("        python scripts/register_telegram_webhook.py")
    print("     4. stop any local poller, so webhook and polling do not compete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
