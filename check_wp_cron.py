import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

LOG_FILE = "logs/cron_checks.json"

def get_eat_time():
    """Get current time in East African Time (UTC+3)."""
    eat_tz = timezone(timedelta(hours=3))
    return datetime.now(eat_tz)

def get_utc_now():
    """Get current UTC time for precise hour/min checks (mirrors YAML logic)."""
    return datetime.now(timezone.utc)

def format_timestamp():
    """Format timestamp in EAT."""
    return get_eat_time().strftime("%Y-%m-%d %I:%M:%S %p EAT")

def ensure_log_dir():
    """Create logs directory if it doesn't exist."""
    os.makedirs("logs", exist_ok=True)

def load_logs():
    """Load existing logs from file."""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading logs: {e}")
            return []
    return []

def save_log(log_entry):
    """Append a log entry to the log file."""
    ensure_log_dir()
    logs = load_logs()
    logs.append(log_entry)
    with open(LOG_FILE, 'w') as f:
        json.dump(logs, f, indent=2)

def send_telegram_message(message):
    """Send a message to Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("Missing Telegram secrets.")
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",  # Changed from "HTML" to "Markdown" for better compatibility
        "disable_web_page_preview": True
    }
    
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            print("Telegram message sent successfully.")
        else:
            print(f"Telegram API returned {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def is_last_run_window():
    """Detect if current time is in 6:00–6:30 PM EAT window (15:00–15:30 UTC), same as YAML."""
    utc_now = get_utc_now()
    current_hour = utc_now.hour
    current_min = utc_now.minute
    if current_hour == 15 and current_min <= 30:
        print(f"Detected last run window: UTC {current_hour}:{current_min:02d} (EAT 18:{current_min:02d})")
        return True
    return False

def send_daily_summary():
    """Send a summary of all checks for the day. (Does not clear logs; handled by workflow.)"""
    logs = load_logs()
    
    if not logs:
        print("No logs to summarize.")
        return
    
    # Check for missed runs (assuming 10 expected per day)
    expected_today = 10
    if len(logs) < expected_today / 2:  # e.g., <5 logs
        missed_note = "\n⚠️ *Missed daytime checks detected—check GitHub Actions.*"
    else:
        missed_note = ""
    
    site = os.getenv("WP_SITE_URL", "Unknown Site")
    site_name = site.replace("https://", "").replace("http://", "")
    
    total = len(logs)
    successful = sum(1 for log in logs if log["status"] == "success")
    failed = total - successful
    
    uptime = (successful / total * 100) if total > 0 else 0
    
    current_time = get_eat_time().strftime("%I:%M %p")
    current_date = get_eat_time().strftime("%B %d, %Y")
    
    message = f"""📊 *Daily WP-Cron Report*{missed_note}

🌐 Site: `{site_name}`
📅 Date: {current_date}
⏰ Report Time: {current_time} EAT

*Summary:*
✅ Successful Checks: {successful}/{total}
❌ Failed Checks: {failed}/{total}
📈 Uptime: {uptime:.1f}%

"""
    
    if successful > 0:
        message += "*Recent Health Checks:*\n"
        success_logs = [log for log in logs if log["status"] == "success"][-3:]
        for log in success_logs:
            time_str = log['timestamp'].split(' ')[1]
            delay = log.get('delay_seconds', 'N/A')
            message += f"• {time_str} - Delay: {delay}s ✓\n"
    
    if failed > 0:
        message += f"\n⚠️ *Note:* {failed} failure(s) detected today\n(Instant alerts were sent)"
    else:
        message += f"\n🎉 *Perfect!* No issues detected today"
    
    send_telegram_message(message)
    # Note: Logs cleared by workflow after push, not here.

def check_wp_cron(is_last_run=False):
    """Perform the WP Cron health check and log result."""
    site = os.getenv("WP_SITE_URL")
    
    if not site:
        print("Missing WP_SITE_URL secret.")
        sys.exit(1)
    
    url = f"{site}/wp-json/site-monitor/v1/cron-status"
    timestamp = format_timestamp()
    site_name = site.replace("https://", "").replace("http://", "")
    
    try:
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            log_entry = {"timestamp": timestamp, "status": "failed", "error": f"HTTP {response.status_code}"}
            save_log(log_entry)
            message = f"🚨 *WP-Cron Check Failed*\n🌐 Site: `{site_name}`\n❌ Error: Unable to reach site\n📡 HTTP Status: {response.status_code}\n⏰ Time: {timestamp}\n"
            send_telegram_message(message)
            if not is_last_run:
                sys.exit(1)
            return  # Proceed to summary on last run
        
        data = response.json()
        status = data.get("status", "")
        delay = data.get("delay_seconds", 999999)
        next_event = data.get("next_event", "N/A")
        
        if status != "ok" or delay > 3600:
            log_entry = {"timestamp": timestamp, "status": "failed", "cron_status": status, "delay_seconds": delay, "next_event": next_event}
            save_log(log_entry)
            send_telegram_message(f"⚠️ *WP-Cron Issue*\n🌐 {site_name}\nStatus: {status}\nDelay: {delay}s\n⏰ {timestamp}")
            if not is_last_run:
                sys.exit(1)
            return  # Proceed to summary on last run
        else:
            log_entry = {"timestamp": timestamp, "status": "success", "cron_status": status, "delay_seconds": delay, "next_event": next_event}
            save_log(log_entry)
            print(f"✅ WP-Cron healthy for {site_name} - Delay: {delay}s (logged)")
            
    except Exception as e:
        log_entry = {"timestamp": timestamp, "status": "failed", "error": str(e)}
        save_log(log_entry)
        send_telegram_message(f"🚨 *WP-Cron Error*\n🌐 {site_name}\n❌ {str(e)}\n⏰ {timestamp}")
        if not is_last_run:
            sys.exit(1)
        return  # Proceed to summary on last run

if __name__ == "__main__":
    # Primary: Use env var if set; fallback to time window detection (same as YAML)
    env_last_run = os.getenv("IS_LAST_RUN", "false").lower() == "true"
    time_last_run = is_last_run_window()
    is_last_run = env_last_run or time_last_run
    
    if is_last_run and not env_last_run:
        print("Using time-based detection for last run (env var not set).")
    
    # Always perform the check and log the entry first
    check_wp_cron(is_last_run)
    
    # Then, if last run, send the daily summary
    if is_last_run:
        print("Sending daily summary...")
        send_daily_summary()
