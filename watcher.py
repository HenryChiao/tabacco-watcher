import requests
import re
import json
import os
import time
import datetime
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# 引入新模块
from config import get_site_config, ADMIN_USER_ID, TELEGRAM_CHAT_ID
from notifier import TelegramNotifier

# 文件路径
STATUS_FILE = "stock_status.json"
PRODUCTS_FILE = "products.json"

class TobaccoWatcher:
    def __init__(self):
        # 初始化基础组件
        self.session = self._init_session()
        self.ua = UserAgent()
        
        # 初始化通知器
        self.notifier = TelegramNotifier(self.session)
        
        # 加载数据
        self.watch_list = self._load_products()
        self.stock_history = self._load_history()
        
        # 运行时状态
        self.start_time = datetime.datetime.now()
        self.last_scan_time = None
        self.consecutive_errors = 0
        self.error_alert_sent = False
        self.first_run = True
        
        # 看板状态
        self.dashboard_message_ids = self.stock_history.get('_dashboard_ids', [])
        self.alert_messages = self.stock_history.get('_alert_messages', {})

    def _init_session(self):
        s = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        s.mount('https://', HTTPAdapter(max_retries=retries))
        return s

    def _load_products(self):
        if os.path.exists(PRODUCTS_FILE):
            try:
                with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return []

    def _load_history(self):
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return {}

    def save_history(self):
        try:
            self.stock_history['_dashboard_ids'] = self.dashboard_message_ids
            self.stock_history['_alert_messages'] = self.alert_messages
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.stock_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存状态失败: {e}")

    def fetch_page(self, url):
        try:
            timestamp = int(time.time() * 1000)
            target = f"{url}{'&' if '?' in url else '?'} _t={timestamp}"
            headers = {"User-Agent": self.ua.random}
            
            resp = self.session.get(target, headers=headers, timeout=20)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"❌ 请求失败 [{url}]: {e}")
            return None

    def check_stock(self, card_soup, selectors):
        """解析单卡片库存"""
        # 1. 获取名称
        name_elem = card_soup.select_one(selectors['product_name'])
        if not name_elem: return None, True
        
        raw_name = name_elem.get_text(strip=True)
        name = re.sub(r'<[^>]+>', '', raw_name).strip()

        # 2. 获取按钮状态
        button = card_soup.select_one(selectors['status_button'])
        if not button: return None, None # 无效区域

        is_sold_out = False
        
        # 优先判定: 如果配置了 sold_out_text，则优先使用文字匹配逻辑
        # (这对于华盛这种按钮始终可用，只变文字的网站非常重要)
        if selectors.get('sold_out_text'):
            target_text = selectors['sold_out_text'].upper()
            
            # 清理隐藏文本，获取真实可见文字
            import copy
            btn_clone = copy.copy(button)
            for hidden in btn_clone.select('.hidden'): hidden.decompose()
            btn_text = btn_clone.get_text(strip=True).upper()
            
            if target_text in btn_text:
                is_sold_out = True
        
        # 次要判定: 如果没配置特定文字，或文字没命中，检查通用属性
        else:
            # 1. 检查 disabled 属性
            if button.has_attr('disabled'): is_sold_out = True
            
            # 2. 检查 class 是否包含 sold-out
            if not is_sold_out:
                classes = button.get('class', [])
                if any('sold-out' in c for c in classes): is_sold_out = True
            
            # 3. 检查通用售罄关键词 (仅在未配置特定文字时启用)
            if not is_sold_out:
                default_keywords = ["售罄", "SOLD OUT", "SOLDOUT", "OUT OF STOCK"]
                btn_text = button.get_text(strip=True).upper()
                if any(kw in btn_text for kw in default_keywords):
                    is_sold_out = True

        return name, is_sold_out

    def run(self):
        """主执行逻辑"""
        print("-" * 50)
        self.last_scan_time = datetime.datetime.now()
        new_restocks = []
        has_error = False
        status_changed = False
        
        for item in self.watch_list:
            url = item['url']
            # 从新配置系统获取模板
            site_name, selectors = get_site_config(url)
            
            html = self.fetch_page(url)
            if not html:
                has_error = True
                continue

            soup = BeautifulSoup(html, 'html.parser')
            cards = soup.select(selectors['product_card'])

            for card in cards:
                result = self.check_stock(card, selectors)
                if not result or result[0] is None: continue
                
                name, is_sold_out = result
                product_id = f"{name}_{url}" # 唯一标识
                
                # 状态对比
                last_record = self.stock_history.get(product_id, {})
                was_sold_out = last_record.get('is_sold_out', True)
                
                if is_sold_out != was_sold_out:
                    status_changed = True
                
                # 更新记录
                self.stock_history[product_id] = {
                    'name': name, 'url': url, 'is_sold_out': is_sold_out,
                    'site_name': site_name, # 记录中文名方便分组
                    'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                # 补货提醒
                if was_sold_out and not is_sold_out:
                    print(f"🔔 [补货] {name}")
                    new_restocks.append(self.stock_history[product_id])
                
                # 刚售罄 -> 删旧通知
                if not was_sold_out and is_sold_out:
                    print(f"❌ [售罄] {name}")
                    self._delete_alert(product_id)

        # 刷新看板
        if status_changed or self.first_run or not self.dashboard_message_ids:
            self._refresh_dashboard()
            self.first_run = False
            
        # 发送新补货通知
        if new_restocks:
            self._send_restock_alerts(new_restocks)

        # 统计摘要日志 (避免刷屏)
        total_items = len(self.stock_history) - 2 # 减去 _dashboard_ids 和 _alert_messages
        in_stock_count = sum(1 for v in self.stock_history.values() if isinstance(v, dict) and not v.get('is_sold_out', True))
        
        # 只打印简报
        print(f"📊 本轮统计: 总计 {total_items} 商品 | ✅ 有货: {in_stock_count} | ❌ 售罄: {total_items - in_stock_count}")

        self.save_history()
        self._handle_errors(has_error)
        print("-" * 50)

    def _refresh_dashboard(self):
        """刷新看板消息"""
        pages = self._generate_dashboard_content()
        
        # 多退
        while len(self.dashboard_message_ids) > len(pages):
            old_id = self.dashboard_message_ids.pop()
            self.notifier.delete_message(old_id)
            
        # 少补 & 更新
        for i, text in enumerate(pages):
            if i < len(self.dashboard_message_ids):
                msg_id = self.dashboard_message_ids[i]
                if not self.notifier.edit_message(msg_id, text):
                    # 编辑失败则重发
                    resp = self.notifier.send_message(text)
                    if resp: self.dashboard_message_ids[i] = resp['result']['message_id']
            else:
                resp = self.notifier.send_message(text)
                if resp: self.dashboard_message_ids.append(resp['result']['message_id'])

    def _generate_dashboard_content(self):
        """生成看板内容 (按站点分组 + 分片)"""
        if not self.stock_history: return ["⏳ 初始化中..."]
        
        # 过滤
        items = [v for k, v in self.stock_history.items() if not k.startswith('_')]
        if not items: return ["📭 暂无监控"]
        
        # 分组 (按 site_name)
        grouped = {}
        for item in items:
            site = item.get('site_name', '未知')
            if site not in grouped: grouped[site] = []
            grouped[site].append(item)
            
        all_msgs = []
        MAX_LEN = 3800
        
        for site, products in grouped.items():
            products.sort(key=lambda x: x['is_sold_out'])
            
            site_msgs = []
            header = f"🌐 <b>{site}</b> (更新: {datetime.datetime.now().strftime('%H:%M:%S')})\n"
            current_msg = header + "<blockquote expandable>"
            quote_open = True
            
            for p in products:
                # 仅保留商品名，去掉了超链接 <a> 标签
                # 示例: ✅ 商品名 (有货) / ❌ <s>商品名</s> (售罄)
                # 注意：为了让 Markdown/HTML 解析正常，售罄时仍保留 <s> 删除线
                product_name = p['name']
                line = f"{'✅' if not p['is_sold_out'] else '❌ <s>'} {product_name}{'</s>' if p['is_sold_out'] else ''}\n"
                
                if len(current_msg) + len(line) + 20 > MAX_LEN:
                    if quote_open: current_msg += "</blockquote>"
                    site_msgs.append(current_msg)
                    
                    current_msg = f"🌐 <b>{site} (续)</b>\n<blockquote expandable>"
                    quote_open = True
                
                current_msg += line
                
            if quote_open: current_msg += "</blockquote>"
            site_msgs.append(current_msg)
            all_msgs.extend(site_msgs)
            
        return all_msgs

    def _send_restock_alerts(self, items):
        for item in items:
            text = (
                f"🚨 <b>补货提醒!</b>\n\n"
                f"🏪 <b>{item['site_name']}</b>\n"
                f"📦 <b>{item['name']}</b>\n"
                f"🔗 <a href='{item['url']}'>点击购买</a>"
            )
            resp = self.notifier.send_message(text)
            if resp:
                pid = f"{item['name']}_{item['url']}"
                self.alert_messages[pid] = resp['result']['message_id']

    def _delete_alert(self, pid):
        if pid in self.alert_messages:
            self.notifier.delete_message(self.alert_messages[pid])
            del self.alert_messages[pid]

    def _handle_errors(self, has_error):
        if has_error:
            self.consecutive_errors += 1
            print(f"⚠️ 抓取错误 ({self.consecutive_errors}次)")
            if self.consecutive_errors >= 5 and not self.error_alert_sent:
                self.notifier.send_message(f"🚨 <b>报警</b>: 连续 5 次抓取失败，请检查服务器。", chat_id=ADMIN_USER_ID)
                self.error_alert_sent = True
        else:
            if self.consecutive_errors > 0:
                print("✅ 错误恢复")
                if self.error_alert_sent:
                    self.notifier.send_message("✅ <b>恢复</b>: 抓取已恢复正常。", chat_id=ADMIN_USER_ID)
            self.consecutive_errors = 0
            self.error_alert_sent = False

    def handle_command(self, text, chat_id):
        """处理 Telegram 指令"""
        if text == "/stock" or text.startswith("/stock@"):
            print(f"📩 收到 /stock")
            for page in self._generate_dashboard_content():
                self.notifier.send_message(page, chat_id)
        elif text == "/status" or text.startswith("/status@"):
            uptime = str(datetime.datetime.now() - self.start_time).split('.')[0]
            msg = (f"🤖 <b>状态报告</b>\n⏱ 运行时长: {uptime}\n"
                   f"📉 错误计数: {self.consecutive_errors}")
            self.notifier.send_message(msg, chat_id)

    def start_bot(self):
        """启动指令监听线程"""
        import threading
        t = threading.Thread(target=self.notifier.poll_commands, args=(self.handle_command,), daemon=True)
        t.start()