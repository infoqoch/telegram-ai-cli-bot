"""Development notification utility."""

import asyncio
import httpx

from src.config import get_settings
from src.network_guard import NetworkUnavailable, network_guard


async def send_dev_report(
    changes: list[str],
    files: list[str],
    test_passed: bool = True,
    extra_message: str = "",
) -> bool:
    """Send development completion report to admin."""
    settings = get_settings()

    if not settings.admin_chat_id:
        print("⚠️ ADMIN_CHAT_ID not set, skipping notification")
        return False

    changes_text = "\n".join(f"• {c}" for c in changes) if changes else "• (none)"
    files_text = "\n".join(f"• {f}" for f in files) if files else "• (none)"
    test_status = "✅ Passed" if test_passed else "❌ Failed"

    message = f"""🔧 <b>Deploy Report</b>

📝 <b>Changes:</b>
{changes_text}

📁 <b>Modified Files:</b>
{files_text}

🧪 <b>Tests:</b> {test_status}
🚀 <b>Status:</b> Deployed"""

    if extra_message:
        message += f"\n\n💬 {extra_message}"

    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    payload = {
        "chat_id": settings.admin_chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await network_guard.run_async("telegram", client.post, url, json=payload)
            return response.status_code == 200
    except NetworkUnavailable as exc:
        print(f"Telegram network unavailable: {exc}")
        return False


def notify_sync(
    changes: list[str],
    files: list[str],
    test_passed: bool = True,
    extra_message: str = "",
) -> bool:
    """Synchronous wrapper for send_dev_report."""
    return asyncio.run(send_dev_report(changes, files, test_passed, extra_message))


# CLI usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m src.notify 'change1' 'change2' -- 'file1' 'file2'")
        sys.exit(1)
    
    args = sys.argv[1:]
    
    # Split by --
    if "--" in args:
        sep_idx = args.index("--")
        changes = args[:sep_idx]
        files = args[sep_idx + 1:]
    else:
        changes = args
        files = []
    
    success = notify_sync(changes, files)
    print("✅ Report sent" if success else "❌ Report send failed")
