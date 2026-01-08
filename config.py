import os
from dotenv import load_dotenv

load_dotenv()

# ================= 站点模板定义 =================

# 模板 1: Tobacco Lifestyle (列表页模式)
TEMPLATE_TOBACCO = {
    "type": "list",
    "product_card": "div.product-card-wrapper", # 去掉 li 前缀，更通用
    "product_name": "h3.card__heading a",
    "status_button": "form button[name='add']", # 稍微放宽按钮选择器
    "in_stock_text": "添加到购物车"
}

# 模板 2: 华盛烟丝 (列表页模式)
TEMPLATE_HUASHENG = {
    "type": "list", # 列表页
    "product_card": "div.product-wrapper",
    "product_name": "h3.wd-entities-title a",
    "status_button": "div.wd-add-btn a",
    "in_stock_text": "加入购物车" # 正向匹配
}

# 模板 4: 花沢 (ribenyan.com)
TEMPLATE_RIBENYAN = {
    "type": "list",
    "product_card": "div.d-flex.py-2.border-bottom",
    "product_name": "div.col-sm-8 p.mb-1",
    "status_button": "div.col-sm-4 a.btn", # 宽泛选择器，匹配有货(success)和无货(secondary)按钮
    "in_stock_text": "加购物车",
    "sold_out_class": "btn-secondary"
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
    },
    "ribenyan.com": {
        "name": "花沢",
        "template": TEMPLATE_RIBENYAN
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