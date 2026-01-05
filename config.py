import os
from dotenv import load_dotenv

load_dotenv()

# ================= 站点模板定义 =================

# 模板 1: Tobacco Lifestyle (列表页模式)
TEMPLATE_TOBACCO = {
    "type": "list",
    "product_card": "div.card__content",
    "product_name": "h3.card__heading a",
    "status_button": "button[name='add']",
    "sold_out_text": "售罄"
}

# 模板 2: 华盛烟丝 (列表页模式)
TEMPLATE_HUASHENG = {
    "type": "list", # 列表页
    "product_card": "div.product-wrapper",
    "product_name": "h3.wd-entities-title a",
    "status_button": "div.wd-add-btn a",
    "sold_out_text": "阅读更多"
}

# 模板 3: 默认通用
TEMPLATE_DEFAULT = {
    "type": "unknown",
    "product_card": "body", 
    "product_name": "h1",
    "status_button": "button",
    "sold_out_text": "Sold Out"
}

# ================= 站点注册表 (核心配置) =================

# 域名 -> (显示名称, 使用的模板)
SITE_CONFIGS = {
    "tobaccolifestyle.com": {
        "name": "烟草生活方式",
        "template": TEMPLATE_TOBACCO
    },
    "huashengyansi.cv": {
        "name": "华盛",
        "template": TEMPLATE_HUASHENG
    },
    "pipeuncle.com": {
        "name": "茄营",
        "template": TEMPLATE_DEFAULT  # API 模式不使用 CSS 选择器模板
    }
}

# ================= 工具函数 =================

def get_site_config(url):
    """根据 URL 获取站点配置 (名称, 模板)"""
    for domain, config in SITE_CONFIGS.items():
        if domain in url:
            return config["name"], config["template"]
    return "未知站点", TEMPLATE_DEFAULT

# ================= 系统配置 =================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

if not TELEGRAM_BOT_TOKEN:
    print("⚠️ 警告: 未在 .env 文件中找到 TELEGRAM_BOT_TOKEN")

CHECK_INTERVAL = 60