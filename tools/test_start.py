from core.bot_app import run_entrypoint

print("--- TEST START ---")
try:
    run_entrypoint()
except Exception as e:
    print(f"CRASH: {e}")
print("--- TEST END ---")
