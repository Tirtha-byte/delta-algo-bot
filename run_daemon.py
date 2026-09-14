"""
BEAST v2 - Resilient Production Daemon Supervisor
Automatically monitors and restarts the trading engine on unexpected exits,
logs crashes, and sends push notifications to Telegram.
"""
import sys
import time
import subprocess
import os
from dotenv import load_dotenv

load_dotenv(override=True)

def notify_telegram(text: str):
    try:
        from agents.telegram_notifier import telegram_notifier
        telegram_notifier.send_message(text)
    except Exception:
        pass

def run_supervisor():
    print("=" * 72)
    print("  BEAST v2 — PRODUCTION AUTO-RESTART SUPERVISOR")
    print("=" * 72)
    notify_telegram("🛡️ <b>BEAST v2 Supervisor Online</b>\nEngine running with 24/7 auto-recovery.")

    while True:
        try:
            print("[Supervisor] Spawning BEAST v2 trading engine process...")
            process = subprocess.Popen([sys.executable, "run.py"])
            exit_code = process.wait()
            print(f"[Supervisor] Process terminated with exit code: {exit_code}")
            notify_telegram(f"⚠️ <b>BEAST v2 Alert</b>\nEngine exited (code {exit_code}). Automatically restarting in 5 seconds...")
        except KeyboardInterrupt:
            print("[Supervisor] Manual operator shutdown received.")
            notify_telegram("🛑 <b>BEAST v2 Alert</b>\nOperator manually terminated engine supervisor.")
            break
        except Exception as e:
            print(f"[Supervisor] Critical Exception: {e}")
            notify_telegram(f"🚨 <b>BEAST v2 Supervisor Exception</b>\n{str(e)[:150]}")
        time.sleep(5)

if __name__ == "__main__":
    run_supervisor()
