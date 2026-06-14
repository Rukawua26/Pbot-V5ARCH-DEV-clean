"""Lee comandos desde archivo IPC y los rutea al command_router del bot."""

import json
import os

CMD_FILE = "/dev/shm/sniper_cmd/command.json"
ALLOWED_DASHBOARD_COMMANDS = frozenset({"/pause", "/resume", "/panic", "/recover_halt"})


def _check_ipc_file_safe(path: str) -> bool:
    try:
        st = os.stat(path)
        if st.st_uid != os.getuid():
            return False
        if st.st_mode & 0o077:
            return False
        return True
    except (OSError, FileNotFoundError):
        return False


def consume_command_file(bot):
    if not os.path.exists(CMD_FILE):
        return
    if not _check_ipc_file_safe(CMD_FILE):
        bot.log(f"⚠️ CMD file unsafe (owner/mode), removing: {CMD_FILE}")
        try:
            os.remove(CMD_FILE)
        except OSError as cleanup_error:
            bot.log(f"⚠️ CMD unsafe cleanup error: {cleanup_error}")
        return
    try:
        with open(CMD_FILE, encoding="utf-8") as f:
            data = json.load(f)
        os.remove(CMD_FILE)
        commands = data.get("commands", []) if isinstance(data, dict) else []
        if not isinstance(commands, list):
            bot.log("⚠️ CMD consume ignored invalid command list")
            return
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            action = cmd.get("action", "")
            if not isinstance(action, str):
                continue
            action = action.strip()
            if not action:
                continue
            if action not in ALLOWED_DASHBOARD_COMMANDS:
                bot.log(f"⚠️ Dashboard command rejected: {action[:64]}")
                continue
            bot.log(f"📨 Dashboard command: {action}")
            bot.handle_command(action)
    except Exception as e:
        bot.log(f"⚠️ CMD consume error: {e}")
        try:
            os.remove(CMD_FILE)
        except OSError as cleanup_error:
            bot.log(f"⚠️ CMD cleanup error: {cleanup_error}")
