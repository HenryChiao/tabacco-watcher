import requests
import re
import json
import os
import time
import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from config import HEADERS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 状态记录文件路径
STATUS_FILE = "stock_status.json"

class TobaccoWatcher:
    def __init__(self, config_list):
        self.watch_list = config_list
        self.stock_history = self.load_history()
        self.telegram_offset = 0  # 用于记录 Telegram 消息读取位置
        
        # 初始化网络会话，配置重试策略
        self.session = requests.Session()
        retries = Retry(
            total=3,                # 最大重试次数
            backoff_factor=1,       # 重试间隔 (1s, 2s, 4s...)
            status_forcelist=[500, 502, 503, 504] # 针对这些状态码进行重试
        )
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        self.session.headers.update(HEADERS)

    def load_history(self):
        """加载历史库存状态"""
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 简单的兼容性检查：如果旧数据是 bool 类型，重置它
                    if data and isinstance(list(data.values())[0], bool):
                        print("检测到旧版数据格式，将自动升级...")
                        return {}
                    return data
            except:
                return {}
        return {}

    def save_history(self):
        """保存当前库存状态到文件"""
        try:
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.stock_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存状态失败: {e}")

    def fetch_page(self, url):
        """获取网页源代码 (带重试)"""
        try:
            # print(f"正在请求: {url}") # 减少刷屏，仅调试用
            # 使用配置好重试策略的 session 发送请求
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"❌ 请求失败 [{url}]: {e}")
            return None

    def check_stock(self, card_soup, selectors):
        """
        检查单个商品的库存状态
        返回: (商品名称, 是否售罄)
        """
        # 1. 获取商品名称
        name_elem = card_soup.select_one(selectors['product_name'])
        if not name_elem:
            return None, True
        
        # 获取文本并清洗：去除可能存在的HTML标签（如 <tc>）和多余空白
        raw_name = name_elem.get_text(strip=True)
        # 使用正则彻底移除任何 <...> 格式的内容，以防万一
        product_name = re.sub(r'<[^>]+>', '', raw_name).strip()

        # 2. 获取库存状态
        # 极简方案：直接检查购买按钮是否被禁用 (disabled)
        button = card_soup.select_one(selectors['status_button'])
        
        if not button:
            # 没有购买按钮 = 无效卡片，跳过
            return None, None

        # 只要按钮有 disabled 属性，就视为售罄；否则视为有货。
        is_sold_out = button.has_attr('disabled')

        return product_name, is_sold_out

    def run(self):
        """执行监控任务"""
        print("-" * 50)
        
        results = []

        for item in self.watch_list:
            html = self.fetch_page(item['url'])
            if not html:
                continue

            soup = BeautifulSoup(html, 'html.parser')
            cards = soup.select(item['selectors']['product_card'])
            
            # 简洁输出找到的数量
            # print(f"[{item['name']}] 扫描到 {len(cards)} 个商品...")

            for card in cards:
                # check_stock 现在可能返回 (None, None) 表示无效卡片
                result = self.check_stock(card, item['selectors'])
                if not result or result[0] is None:
                    continue
                
                name, is_sold_out = result
                
                if name:
                    # 生成唯一ID (防止不同页面有同名商品)
                    product_id = f"{name}_{item['url']}"
                    
                    # 检查历史状态
                    # 兼容旧代码：如果历史记录不存在，或者格式不对，默认为售罄
                    last_record = self.stock_history.get(product_id)
                    if isinstance(last_record, dict):
                        was_sold_out = last_record.get('is_sold_out', True)
                    else:
                        was_sold_out = True
                    
                    # 核心通知逻辑：只有当 [上次没货] 且 [现在有货] 时，才通知
                    should_notify = was_sold_out and (not is_sold_out)
                    
                    # 更新历史记录 (存入更详细的信息以便Bot查询)
                    self.stock_history[product_id] = {
                        'name': name,
                        'url': item['url'],
                        'is_sold_out': is_sold_out,
                        'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }

                    # 简洁的单行输出
                    if is_sold_out:
                        print(f"❌ [售罄] {name}")
                    else:
                        # 如果触发了通知条件，加上一个铃铛图标 🔔
                        if should_notify:
                            print(f"🔔 [新补货!] {name} (已触发通知)")
                            self.send_notification(name, item['url'])
                        else:
                            print(f"✅ [有货] {name} (已通知过)")

        # 扫描完一轮后，保存状态
        self.save_history()
        print("-" * 50)
        return results

    def send_notification(self, product_name, url):
        """发送通知"""
        print(f"\n>>> 发送通知: {product_name} 现在可购买! <<<\n")
        
        # 构造消息内容
        message = (
            f"🚨 <b>补货提醒!</b>\n\n"
            f"📦 <b>{product_name}</b>\n"
            f"✅ 现在有货!\n\n"
            f"🔗 <a href='{url}'>点击购买</a>"
        )
        
        self.send_telegram_message(message)

    def send_telegram_message(self, text, chat_id=None):
        """推送到 Telegram"""
        if not TELEGRAM_BOT_TOKEN:
            return

        # 如果未指定 chat_id，使用配置文件的默认 ID
        target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
        if not target_chat_id:
            return

        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        try:
            resp = self.session.post(api_url, json=payload, timeout=10)
            resp.raise_for_status()
            # print("📩 Telegram 通知已发送")
        except Exception as e:
            print(f"⚠️ Telegram 推送失败: {e}")

    def get_stock_report(self):
        """生成当前库存报告"""
        if not self.stock_history:
            return "📭 暂无库存数据，请等待第一次扫描完成。"
        
        in_stock_items = []
        
        for pid, info in self.stock_history.items():
            if not info.get('is_sold_out', True):
                in_stock_items.append(info)
        
        if not in_stock_items:
            return "❌ <b>当前所有监控商品均已售罄。</b>"
            
        report = f"📊 <b>当前库存清单 ({len(in_stock_items)})</b>\n\n"
        for item in in_stock_items:
            report += f"✅ <b>{item['name']}</b>\n🔗 <a href='{item['url']}'>点击购买</a>\n\n"
            
        report += f"<i>最后更新: {datetime.datetime.now().strftime('%H:%M')}</i>"
        return report

    def poll_telegram_commands(self):
        """监听 Telegram 指令 (运行在独立线程)"""
        if not TELEGRAM_BOT_TOKEN:
            print("⚠️ 未配置 Bot Token，指令监听未启动")
            return

        print("🤖 Telegram 机器人监听中 (发送 /stock 查询库存)...")
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        
        while True:
            try:
                # 使用 long polling (timeout=60)
                params = {"offset": self.telegram_offset + 1, "timeout": 60}
                resp = self.session.get(api_url, params=params, timeout=70)
                
                if resp.status_code == 200:
                    result = resp.json().get("result", [])
                    for update in result:
                        self.telegram_offset = update["update_id"]
                        
                        # 处理消息
                        if "message" in update and "text" in update["message"]:
                            text = update["message"]["text"].strip()
                            chat_id = update["message"]["chat"]["id"]
                            
                            if text == "/stock":
                                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📩 收到 /stock 指令")
                                report = self.get_stock_report()
                                self.send_telegram_message(report, chat_id)
            
            except Exception as e:
                print(f"⚠️ Telegram 监听异常 (自动重试): {e}")
                time.sleep(5)
            
            # 避免死循环跑太快
            time.sleep(1)