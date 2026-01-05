# 站点模板 1: Tobacco Lifestyle 专用选择器
SELECTOR_TOBACCO_LIFESTYLE = {
    # 商品卡片的容器
    "product_card": "div.card__content",
    
    # 商品名称选择器 (相对于 product_card)
    "product_name": "h3.card__heading a",
    
    # 售罄状态检测区域 (相对于 product_card)
    "status_button": "button[name='add']",
    
    # 判定为售罄的关键字
    "sold_out_text": "售罄"
}

# 站点模板 2: 示例其他网站 (预留)
# SELECTOR_OTHER_SITE = { ... }

# 监控配置列表
WATCH_LIST = [
    {
        "name": "Ark Royal",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/ark-royal",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "XXX",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/xxx",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Black Spider",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/black-spider",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Cerrito",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/cerrito",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Mac Baren",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/mac-baren",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Spade",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/spade",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "SOL",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/sol",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "The Turner",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/the-turner",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Three Dogs",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/three-dogs",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    },
    {
        "name": "Wild Bison",
        "url": "https://tobaccolifestyle.com/zh/collections/%E6%BB%9A%E5%8A%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84-1/wild-bison",
        "selectors": SELECTOR_TOBACCO_LIFESTYLE
    }
]

# 请求头，模拟浏览器防止被简单拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
}

import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ---------------------------------------------------
# Telegram 通知配置 (从环境变量读取)
# ---------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN:
    print("⚠️ 警告: 未在 .env 文件中找到 TELEGRAM_BOT_TOKEN")

# 轮询间隔 (秒)
# 建议不要太短，以免触发反爬虫或被封IP。建议 600 (10分钟) 或 1800 (30分钟)
CHECK_INTERVAL = 60