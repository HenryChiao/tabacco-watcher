from config import CHECK_INTERVAL
from watcher import TobaccoWatcher
import time
import datetime
import threading

def main():
    print("Tobacco Watcher 启动...")
    # 初始化爬虫 (现在会自动读取 products.json)
    watcher = TobaccoWatcher()
    
    print(f"监控目标数: {len(watcher.watch_list)}")
    print(f"轮询间隔: {CHECK_INTERVAL} 秒")
    print("-" * 50)
    
    # 启动 Telegram 指令监听线程 (现在已封装在 watcher 内部)
    watcher.start_bot()
    
    # 死循环长期运行 (主线程负责扫描)
    while True:
        try:
            # 打印当前时间
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] 开始新一轮扫描...")
            
            watcher.run()
            
            print(f"[{now}] 扫描结束，休眠 {CHECK_INTERVAL} 秒...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n程序已停止 (用户中断)")
            break # 用户手动中断时才退出循环
        except Exception as e:
            # 捕获所有其他异常，防止程序崩溃退出
            print(f"\n⚠️ 发生未捕获异常: {e}")
            print(f"程序将在 {CHECK_INTERVAL} 秒后尝试重连...")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()