import requests
import re
import json
import os
import time
import random
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64

# 本地模块
from config import get_site_config, ADMIN_USER_ID, TELEGRAM_CHAT_ID
from notifier import TelegramNotifier

# 常量定义
STATUS_FILE = "stock_status.json"
PRODUCTS_FILE = "products.json"

class TobaccoWatcher:
    def __init__(self):
        # 1. 初始化网络与工具
        self.session = self._init_session()
        self.ua = UserAgent()
        self.notifier = TelegramNotifier(self.session)
        
        # 2. 加载持久化数据
        self.history_file_exists = os.path.exists(STATUS_FILE)
        self.watch_list = self._load_products()
        self.stock_history = self._load_history()
        
        # 3. 初始化运行时状态
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

    def _decrypt_pipeuncle_data(self, encrypted_text):
        """解密茄营 API 数据"""
        try:
            key = b"0f5ef28c56b64e67"
            encrypted_bytes = base64.b64decode(encrypted_text)
            cipher = AES.new(key, AES.MODE_ECB)
            decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
            return decrypted_bytes.decode('utf-8')
        except Exception as e:
            print(f"解密失败: {e}")
            return None

    def _scan_api_pipeuncle(self, item):
        """处理茄营 (PipeUncle) API 请求与解密"""
        time.sleep(random.uniform(1, 3))
        api_url = item['url']
        site_name = "茄营"
        
        # [URL转换] 尝试从 API URL 解析 categoryId 以构建前端可访问的 URL
        # API: .../category-list?categoryId=146... -> Front: .../detail/class?id=146
        try:
            parsed = urlparse(api_url)
            qs = parse_qs(parsed.query)
            cat_id = qs.get('categoryId', [''])[0]
            web_url = f"https://www.pipeuncle.com/detail/class?id={cat_id}" if cat_id else "https://www.pipeuncle.com/"
        except:
            web_url = api_url

        headers = {
            "User-Agent": self.ua.random,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.pipeuncle.com/"
        }
        
        try:
            resp = self.session.get(api_url, headers=headers, timeout=20)
            resp.raise_for_status()
            json_resp = resp.json()
            
            local_restocks = []
            local_changed = False
            
            # 验证响应结构: code=200 且存在 data 字段
            if 'code' in json_resp and json_resp['code'] == 200 and 'data' in json_resp:
                encrypted_text = json_resp['data']
                if not encrypted_text: return False, [], False

                # 解密数据
                decrypted_text = self._decrypt_pipeuncle_data(encrypted_text)
                if not decrypted_text: return False, [], False
                
                # 解析商品列表
                data = json.loads(decrypted_text)
                for product in data.get('lists', []):
                    name = product.get('name', '未知商品')
                    has_stock = product.get('inventoryStatus', False) # true=有货
                    is_sold_out = not has_stock
                    
                    # [去重策略] 使用 商品名+站点名 作为唯一 ID (移除 URL 依赖)
                    # 目的: 避免不同链接包含相同商品时重复报警/重复展示
                    # 注意: 这会覆盖旧的 ID 格式 (name_url)，如果需要兼容旧数据，旧数据会自动失效
                    product_id = f"{name}_茄营"
                    
                    # --- 核心状态更新逻辑 (复用) ---
                    last_record = self.stock_history.get(product_id, {})
                    was_sold_out = last_record.get('is_sold_out', True)
                    
                    if is_sold_out != was_sold_out:
                        local_changed = True
                    
                    self.stock_history[product_id] = {
                        'name': name,
                        'url': web_url, # 存储前端链接
                        'is_sold_out': is_sold_out,
                        'site_name': site_name,
                        'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    # 补货通知
                    if was_sold_out and not is_sold_out:
                        if self.first_run and not self.history_file_exists:
                            print(f"✅ [初始化] 发现有货: {name} (静默)")
                        else:
                            print(f"🔔 [补货] {name}")
                            local_restocks.append(self.stock_history[product_id])
                    
                    # 售罄处理
                    if not was_sold_out and is_sold_out:
                        print(f"❌ [售罄] {name}")
                        self._delete_alert(product_id)
                                
            return False, local_restocks, local_changed
            
        except Exception as e:
            print(f"❌ PipeUncle API 请求失败: {e}")
            return True, [], False

    def check_stock(self, card_soup, selectors):
        """解析常规站点的单商品库存 (HTML 模式)"""
        # 1. 获取商品名称
        name_elem = card_soup.select_one(selectors['product_name'])
        if not name_elem: return None, True
        
        raw_name = name_elem.get_text(strip=True)
        name = re.sub(r'<[^>]+>', '', raw_name).strip()

        # 2. 获取状态区域 (按钮/文字)
        button = card_soup.select_one(selectors['status_button'])
        if not button: return None, None # 无效区域，跳过

        is_sold_out = False
        
        # 策略 A: 优先匹配特定售罄文字 (配置 sold_out_text 时)
        if selectors.get('sold_out_text'):
            target_text = selectors['sold_out_text'].upper()
            
            # 提取可见文本 (移除 .hidden 元素)
            import copy
            btn_clone = copy.copy(button)
            for hidden in btn_clone.select('.hidden'): hidden.decompose()
            btn_text = btn_clone.get_text(strip=True).upper()
            
            if target_text in btn_text:
                is_sold_out = True
        
        # 策略 B: 通用属性检查 (未配置特定文字时)
        else:
            # B1. 检查 disabled 属性
            if button.has_attr('disabled'): is_sold_out = True
            
            # B2. 检查 class 是否包含 sold-out
            if not is_sold_out:
                classes = button.get('class', [])
                if any('sold-out' in c for c in classes): is_sold_out = True
            
            # B3. 检查通用关键词
            if not is_sold_out:
                default_keywords = ["售罄", "SOLD OUT", "SOLDOUT", "OUT OF STOCK"]
                btn_text = button.get_text(strip=True).upper()
                if any(kw in btn_text for kw in default_keywords):
                    is_sold_out = True

        return name, is_sold_out

    def _scan_site(self, item):
        """执行单个站点的扫描任务 (运行于独立线程)"""
        # 0. 特殊处理: 茄营 (PipeUncle) API 模式
        # 用户确认全是 API 链接，因此直接匹配 /api/ 即可
        if "pipeuncle.com/api/" in item['url']:
            return self._scan_api_pipeuncle(item)

        # [安全策略] 随机延迟 1-3 秒，错峰请求，避免高并发触发防火墙
        time.sleep(random.uniform(1, 3))

        url = item['url']
        site_name, selectors = get_site_config(url)
        
        html = self.fetch_page(url)
        if not html:
            return True, [], False  # 返回: has_error, restocks, status_changed

        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select(selectors['product_card'])
        
        local_restocks = []
        local_changed = False
        
        for card in cards:
            # 1. 解析商品状态
            result = self.check_stock(card, selectors)
            if not result or result[0] is None: continue
            
            name, is_sold_out = result
            product_id = f"{name}_{url}"
            
            # 2. 对比历史状态
            last_record = self.stock_history.get(product_id, {})
            was_sold_out = last_record.get('is_sold_out', True)
            
            if is_sold_out != was_sold_out:
                local_changed = True
            
            # 3. 更新内存记录 (线程安全：不同线程处理不同 url，不会冲突)
            self.stock_history[product_id] = {
                'name': name, 'url': url, 'is_sold_out': is_sold_out,
                'site_name': site_name,
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # 4. 处理补货逻辑
            if was_sold_out and not is_sold_out:
                # 首次运行且无历史记录时，视为初始化，静默处理
                if self.first_run and not self.history_file_exists:
                    print(f"✅ [初始化] 发现有货: {name} (静默)")
                else:
                    print(f"🔔 [补货] {name}")
                    local_restocks.append(self.stock_history[product_id])
            
            # 5. 处理售罄逻辑
            if not was_sold_out and is_sold_out:
                print(f"❌ [售罄] {name}")
                self._delete_alert(product_id)

        return False, local_restocks, local_changed

    def run(self):
        """核心调度逻辑 (并发模式 - 按域名分批)"""
        print("-" * 50)
        self.last_scan_time = datetime.datetime.now()
        
        # 1. 对监控列表按域名进行分组
        domain_groups = {}
        for item in self.watch_list:
            domain = urlparse(item['url']).netloc
            if domain not in domain_groups:
                domain_groups[domain] = []
            domain_groups[domain].append(item)

        # 确保华盛 (huashengyansi) 相关的组排在最后
        sorted_domains = sorted(domain_groups.keys(), key=lambda d: 1 if 'huashengyansi' in d else 0)

        all_new_restocks = []
        any_error = False
        any_status_changed = False
        
        # 2. 按域名批次执行扫描
        for domain in sorted_domains:
            domain_items = domain_groups[domain]
            domain_restocks = []
            domain_status_changed = False
            
            print(f"🚀 开始扫描域名: {domain} ({len(domain_items)} 个任务)...")
            
            # 针对当前域名组使用线程池并发
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(self._scan_site, item) for item in domain_items]
                
                for future in as_completed(futures):
                    try:
                        has_error, restocks, changed = future.result()
                        if has_error: any_error = True
                        if changed: 
                            domain_status_changed = True
                            any_status_changed = True
                        if restocks: 
                            domain_restocks.extend(restocks)
                            all_new_restocks.extend(restocks)
                    except Exception as e:
                        print(f"⚠️ 线程执行异常: {e}")
                        any_error = True

            # 3. [即时反馈] 如果该域名有状态变更，立即刷新看板
            # (注意顺序：先刷新看板，再发补货通知，这样用户看到补货通知时看板已经是新的了)
            if domain_status_changed or (self.first_run and domain == sorted_domains[0]): # 第一次运行时至少刷一次
                 self._refresh_dashboard()

            # 4. [即时反馈] 如果该域名有补货，立即发送通知，无需等待所有域名跑完
            if domain_restocks:
                print(f"⚡ [即时推送] {domain} 发现 {len(domain_restocks)} 个补货，立即发送通知...")
                self._send_restock_alerts(domain_restocks)

        self.first_run = False
            
        # 5. 输出统计日志
        total_items = len(self.stock_history) - 2
        in_stock_count = sum(1 for v in self.stock_history.values() if isinstance(v, dict) and not v.get('is_sold_out', True))
        print(f"📊 本轮统计: 总计 {total_items} 商品 | ✅ 有货: {in_stock_count} | ❌ 售罄: {total_items - in_stock_count}")

        # 6. 持久化与错误处理
        self.save_history()
        self._handle_errors(any_error)
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
            
            # 计算当前站点的库存统计
            total_count = len(products)
            in_stock = sum(1 for p in products if not p['is_sold_out'])
            out_stock = total_count - in_stock
            
            site_msgs = []
            # 标题带上统计数据 (例如: 20有货 / 80售罄)
            header = (
                f"🌐 <b>{site}</b> (更新: {datetime.datetime.now().strftime('%H:%M:%S')})\n"
                f"📊 <b>统计:</b> ✅ {in_stock} 有货 | ❌ {out_stock} 售罄\n"
            )
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