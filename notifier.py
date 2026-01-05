import requests
import time
import datetime
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_USER_ID

class TelegramNotifier:
    def __init__(self, session=None):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.session = session or requests.Session()
        self.api_base = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, text, chat_id=None):
        """发送新消息"""
        if not self.token: return None
        target_id = chat_id or self.chat_id
        if not target_id: return None

        try:
            url = f"{self.api_base}/sendMessage"
            payload = {
                "chat_id": target_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            resp = self.session.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 发送消息失败: {e}")
            return None

    def edit_message(self, message_id, text, chat_id=None):
        """编辑消息"""
        if not self.token: return False
        target_id = chat_id or self.chat_id

        try:
            url = f"{self.api_base}/editMessageText"
            payload = {
                "chat_id": target_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            resp = self.session.post(url, json=payload, timeout=10)
            
            # 忽略 "内容未变" 的错误
            if resp.status_code == 400 and "message is not modified" in resp.text:
                return True
                
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ 编辑消息失败: {e}")
            return False

    def delete_message(self, message_id, chat_id=None):
        """删除消息"""
        if not self.token: return False
        target_id = chat_id or self.chat_id

        try:
            url = f"{self.api_base}/deleteMessage"
            payload = {"chat_id": target_id, "message_id": message_id}
            resp = self.session.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ 删除消息失败: {e}")
            return False

    def poll_commands(self, callback_handler):
        """
        监听指令 (阻塞式，建议在独立线程运行)
        :param callback_handler: 当收到指令时调用的函数，签名为 func(text, chat_id)
        """
        if not self.token:
            print("⚠️ 未配置 Bot Token，指令监听未启动")
            return

        print("🤖 Telegram 机器人监听中...")
        offset = 0
        url = f"{self.api_base}/getUpdates"

        while True:
            try:
                resp = self.session.get(url, params={"offset": offset + 1, "timeout": 60}, timeout=70)
                if resp.status_code == 200:
                    result = resp.json().get("result", [])
                    for update in result:
                        offset = update["update_id"]
                        message = update.get("message") or update.get("channel_post")
                        
                        if message and "text" in message:
                            text = message["text"].strip()
                            chat_id = message["chat"]["id"]
                            # 回调主程序处理逻辑
                            callback_handler(text, chat_id)
            except Exception as e:
                print(f"⚠️ Telegram 监听异常: {e}")
                time.sleep(5)
            time.sleep(1)