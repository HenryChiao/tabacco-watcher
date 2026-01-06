import requests
import re
import json
import os
import time
import random
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
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
        self.lock = threading.Lock() # 线程安全锁
        
        # 2. 加载持久化数据
        self.history_file_exists = os.path.exists(STATUS_FILE)
        self.watch_list = self._load_products()
        self.stock_history = self._load_history()
        
        # 3. 清理僵尸数据 (逻辑内存泄漏修复)
        self._cleanup_stale_data()

        # 4. 初始化运行时状态
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

    def _cleanup_stale_data(self):
        """清理不再监控的商品历史数据 (防止无限膨胀)"""
        if not self.watch_list: return
        
        # 1. 获取当前所有有效的监控 URL 集合
        valid_urls = set(item['url'] for item in self.watch_list)
        
        # 2. 找出需要删除的 key
        keys_to_remove = []
        for pid, record in self.stock_history.items():
            # 跳过元数据 (以 _ 开头)
            if pid.startswith('_'): continue
            
            # 检查记录中的 url 是否仍在监控列表中
            # 注意：record 必须包含 url 字段
            record_url = record.get('url')
            if record_url and record_url not in valid_urls:
                keys_to_remove.append(pid)
                
        # 3. 执行删除
        if keys_to_remove:
            print(f"🧹 [清理] 移除 {len(keys_to_remove)} 个不再监控的商品历史记录")
            for pid in keys_to_remove:
                del self.stock_history[pid]
                # 同时尝试清理可能残留的报警 ID
                if pid in self.alert_messages:
                    del self.alert_messages[pid]
            
            # 立即保存一次，更新文件
            self.save_history()

    def save_history(self):
        with self.lock:
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
            
            resp = self.session.get(target, headers=headers, timeout=10)
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

    def _get_product_id(self, name, url):
        """统一生成商品唯一 ID"""
        return f"{name}_{url}"

    def _delete_alert(self, pid):
        # 注意：此处不再加锁，由调用方保证或无所谓（Telegram操作本身是线程安全的，dict操作需要注意）
        # 为了安全，dict的操作还是应该在锁内，或者使用 self.lock
        # 但 _delete_alert 通常在 _handle_product_update 内部调用，那里已经有锁了
        # 为了防止死锁，这里不加锁，假设调用方已处理好逻辑
        if pid in self.alert_messages:
            self.notifier.delete_message(self.alert_messages[pid])
            del self.alert_messages[pid]

    def _handle_product_update(self, product_id, name, url, site_name, is_sold_out):
        """
        统一处理商品状态更新、历史记录、计数器和通知逻辑
        返回: (should_notify, status_changed, record)
        """
        with self.lock:
            # 检查是否为新商品
            is_new_product = product_id not in self.stock_history
            
            last_record = self.stock_history.get(product_id, {})
            was_sold_out = last_record.get('is_sold_out', True)
            in_stock_counter = last_record.get('in_stock_counter', 0)
            
            # 状态改变 或 新商品加入，都视为变更，需要刷新看板
            status_changed = (is_sold_out != was_sold_out) or is_new_product
            should_notify = False
            
            # --- 状态核心逻辑 ---
            if is_sold_out:
                # 情况1: 售罄
                in_stock_counter = 0 # 重置计数
                if not was_sold_out:
                    print(f"❌ [售罄] {name}")
                    self._delete_alert(product_id)
            else:
                # 情况2: 有货
                if was_sold_out:
                    # 刚补货
                    in_stock_counter = 0 # 重置计数
                    if self.first_run and not self.history_file_exists:
                        print(f"✅ [初始化] 发现有货: {name} (静默)")
                    else:
                        print(f"🔔 [补货] {name}")
                        should_notify = True
                else:
                    # 持续有货
                    in_stock_counter += 1
                    # 60次检查都有货，则删除通知
                    if in_stock_counter >= 60:
                        # 仅在刚满60次时执行一次删除，避免重复调用 API
                        if in_stock_counter == 60:
                            print(f"🗑️ [超时] {name} 持续有货 {in_stock_counter} 次，自动移除通知")
                            self._delete_alert(product_id)
            
            # 更新记录
            record = {
                'name': name,
                'url': url,
                'is_sold_out': is_sold_out,
                'site_name': site_name,
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'in_stock_counter': in_stock_counter
            }
            self.stock_history[product_id] = record
            
            return should_notify, status_changed, record

    def _process_product_batch(self, site_name, products_iter):
        """
        统一处理一批商品数据的状态更新循环
        :param site_name: 站点名称
        :param products_iter: 一个可迭代对象(list or generator)，每项为 (name, url, is_sold_out)
        :return: (local_restocks, local_changed)
        """
        local_restocks = []
        local_changed = False
        
        for name, url, is_sold_out in products_iter:
            product_id = self._get_product_id(name, url)
            
            # 调用统一处理逻辑
            should_notify, changed, record = self._handle_product_update(
                product_id, name, url, site_name, is_sold_out
            )
            
            if changed: local_changed = True
            if should_notify: local_restocks.append(record)
            
        return local_restocks, local_changed

    def _scan_api_pipeuncle(self, item):
        """[策略] 茄营 (PipeUncle) API 专用扫描逻辑"""
        # API 模式不需要 sleep，并发控制由 run 方法的线程池处理
        time.sleep(random.uniform(0.1, 0.5))
        
        api_url = item['url']
        site_name, _ = get_site_config(api_url) # 从 Config 获取统一名称，不再硬编码
        
        # [URL转换]
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
        
        local_restocks = []
        local_changed = False
        
        try:
            resp = self.session.get(api_url, headers=headers, timeout=10)
            resp.raise_for_status()
            json_resp = resp.json()
            
            if 'code' in json_resp and json_resp['code'] == 200 and 'data' in json_resp:
                encrypted_text = json_resp['data']
                if not encrypted_text: return False, [], False

                decrypted_text = self._decrypt_pipeuncle_data(encrypted_text)
                if not decrypted_text: return False, [], False
                
                data = json.loads(decrypted_text)
                
                # 构造生成器供 batch 处理使用
                def product_generator():
                    for product in data.get('lists', []):
                        name = product.get('name', '未知商品')
                        has_stock = product.get('inventoryStatus', False)
                        # API模式下所有商品共用同一个 web_url (列表页)
                        yield name, web_url, not has_stock
                
                local_restocks, local_changed = self._process_product_batch(site_name, product_generator())
                                
            return False, local_restocks, local_changed
            
        except Exception as e:
            print(f"❌ PipeUncle API 请求失败: {e}")
            return True, [], False

    def _check_stock_html(self, card_soup, selectors):
        """[工具] 解析 HTML 单商品库存"""
        name_elem = card_soup.select_one(selectors['product_name'])
        if not name_elem: return None, True
        
        raw_name = name_elem.get_text(strip=True)
        name = re.sub(r'<[^>]+>', '', raw_name).strip()

        button = card_soup.select_one(selectors['status_button'])
        if not button: return None, None

        # 获取按钮文本 (预处理)
        import copy
        btn_clone = copy.copy(button)
        for hidden in btn_clone.select('.hidden'): hidden.decompose()
        btn_text = btn_clone.get_text(strip=True).upper()

        # 策略 0: 正向匹配 (优先) - 如果配置了明确的有货关键词
        if selectors.get('in_stock_text'):
            target_text = selectors['in_stock_text'].upper()
            # 默认设为售罄，只有匹配到有货关键词才算有货
            is_sold_out = True
            if target_text in btn_text:
                is_sold_out = False

        # 策略 A: 特定售罄文字 (反向匹配)
        elif selectors.get('sold_out_text'):
            is_sold_out = False # 默认有货
            target_text = selectors['sold_out_text'].upper()
            if target_text in btn_text:
                is_sold_out = True
        
        # 策略 B: 通用属性 (反向匹配)
        else:
            is_sold_out = False # 默认有货
            if button.has_attr('disabled'): is_sold_out = True
            if not is_sold_out:
                classes = button.get('class', [])
                if any('sold-out' in c for c in classes): is_sold_out = True
            if not is_sold_out:
                default_keywords = ["售罄", "SOLD OUT", "SOLDOUT", "OUT OF STOCK"]
                if any(kw in btn_text for kw in default_keywords):
                    is_sold_out = True

        return name, is_sold_out

    def _scan_html_site(self, item):
        """[策略] 通用 HTML 站点扫描逻辑"""
        time.sleep(random.uniform(0.1, 0.5))

        url = item['url']
        site_name, selectors = get_site_config(url)
        
        html = self.fetch_page(url)
        if not html:
            return True, [], False

        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select(selectors['product_card'])
        
        found_count = 0
        def product_generator():
            nonlocal found_count
            for card in cards:
                result = self._check_stock_html(card, selectors)
                if result and result[0] is not None:
                    name, is_sold_out = result
                    found_count += 1
                    yield name, url, is_sold_out

        local_restocks, local_changed = self._process_product_batch(site_name, product_generator())
        
        if found_count == 0:
            if len(cards) > 0:
                print(f"⚠️ [{site_name}] 警告: 找到了 {len(cards)} 个卡片但无法提取商品信息，请检查内部选择器")
            else:
                print(f"⚠️ [{site_name}] 警告: 未找到任何商品卡片，请检查 product_card 选择器")
                
        return False, local_restocks, local_changed

    def _scan_site(self, item):
        """[调度] 核心调度器：根据 URL 分发到不同的扫描策略"""
        # 1. 策略路由
        if "pipeuncle.com/api/" in item['url']:
            return self._scan_api_pipeuncle(item)
        
        # 2. 默认策略 (HTML 通用解析)
        return self._scan_html_site(item)

    def _scan_domain_group(self, domain, items):
        """针对特定域名的并行扫描任务"""
        print(f"🚀 [并发] 正在扫描: {domain} ({len(items)} 任务)")
        
        domain_restocks = []
        domain_error = False
        domain_changed = False
        
        # 每个网站单独的类别并发 10 扫描
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self._scan_site, item) for item in items]
            
            for future in as_completed(futures):
                try:
                    has_error, restocks, changed = future.result()
                    if has_error: domain_error = True
                    if changed: domain_changed = True
                    if restocks: domain_restocks.extend(restocks)
                except Exception as e:
                    print(f"⚠️ {domain} 线程异常: {e}")
                    domain_error = True

        # 即时反馈
        if domain_changed or (self.first_run and not self.history_file_exists):
            self._refresh_dashboard()
            
        if domain_restocks:
            print(f"⚡ [即时推送] {domain} 发现 {len(domain_restocks)} 个补货")
            self._send_restock_alerts(domain_restocks)
            
        return domain_error, domain_changed

    def run(self):
        """核心调度逻辑 (全站同步并发)"""
        print("-" * 50)
        # [热更新] 每一轮都重新加载商品列表，无需重启程序
        self.watch_list = self._load_products()
        
        self.last_scan_time = datetime.datetime.now()
        
        # 1. 对监控列表按域名进行分组
        domain_groups = {}
        for item in self.watch_list:
            domain = urlparse(item['url']).netloc
            if domain not in domain_groups:
                domain_groups[domain] = []
            domain_groups[domain].append(item)

        domains = list(domain_groups.keys())
        any_error = False
        
        print(f"🔄 启动全站并发扫描: {', '.join(domains)}")

        # 2. 顶级并发：每个域名一个线程，同时开始
        with ThreadPoolExecutor(max_workers=len(domains) + 1) as main_executor:
            futures = []
            for domain, items in domain_groups.items():
                futures.append(main_executor.submit(self._scan_domain_group, domain, items))
            
            # 等待所有域名完成
            for future in as_completed(futures):
                try:
                    d_error, d_changed = future.result()
                    if d_error: any_error = True
                except Exception as e:
                    print(f"⚠️ 域名扫描总控异常: {e}")
                    any_error = True

        self.first_run = False
            
        # 3. 输出统计日志
        total_items = sum(1 for k in self.stock_history if not k.startswith('_'))
        in_stock_count = sum(1 for v in self.stock_history.values() if isinstance(v, dict) and not v.get('is_sold_out', True))
        print(f"📊 本轮统计: 总计 {total_items} 商品 | ✅ 有货: {in_stock_count} | ❌ 售罄: {total_items - in_stock_count}")

        # 4. 持久化与错误处理
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
        """生成看板内容"""
        # 加锁读取，避免生成过程中数据变动导致不一致
        with self.lock:
            items = [v for k, v in self.stock_history.items() if not k.startswith('_')]
        
        if not items: return ["📭 暂无监控"]
        
        grouped = {}
        for item in items:
            site = item.get('site_name', '未知')
            if site not in grouped: grouped[site] = []
            grouped[site].append(item)
            
        all_msgs = []
        MAX_LEN = 3800
        
        for site, products in grouped.items():
            products.sort(key=lambda x: x['is_sold_out'])
            
            total_count = len(products)
            in_stock = sum(1 for p in products if not p['is_sold_out'])
            out_stock = total_count - in_stock
            
            site_msgs = []
            page_num = 1
            
            # 基础标题
            base_header = (
                f"🌐 <b>{site}</b> (更新: {datetime.datetime.now().strftime('%H:%M:%S')})\n"
                f"📊 <b>统计:</b> ✅ {in_stock} 有货 | ❌ {out_stock} 售罄"
            )
            
            current_msg = f"{base_header}\n<blockquote expandable>"
            quote_open = True
            
            for p in products:
                product_name = p['name']
                line = f"{'✅' if not p['is_sold_out'] else '❌ <s>'} {product_name}{'</s>' if p['is_sold_out'] else ''}\n"
                
                if len(current_msg) + len(line) + 20 > MAX_LEN:
                    if quote_open: current_msg += "</blockquote>"
                    site_msgs.append(current_msg)
                    
                    page_num += 1
                    current_msg = f"🌐 <b>{site} - {page_num}</b>\n<blockquote expandable>"
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
                # 使用统一 ID 生成逻辑
                pid = self._get_product_id(item['name'], item['url'])
                with self.lock:
                    self.alert_messages[pid] = resp['result']['message_id']

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
        t = threading.Thread(target=self.notifier.poll_commands, args=(self.handle_command,), daemon=True)
        t.start()