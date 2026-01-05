from config import WATCH_LIST, CHECK_INTERVAL
from watcher import TobaccoWatcher
import time
import datetime
import threading

def main():
    print("Tobacco Watcher 启动...")
    print(f"监控目标数: {len(WATCH_LIST)}")
    print(f"轮询间隔: {CHECK_INTERVAL} 秒")
    print("-" * 50)
    
    # 初始化爬虫
    watcher = TobaccoWatcher(WATCH_LIST)
    
    # 启动 Telegram 指令监听线程 (后台运行)
    # daemon=True 表示当主程序退出时，这个线程也会自动退出
    bot_thread = threading.Thread(target=watcher.poll_telegram_commands, daemon=True)
    bot_thread.start()
    
    # 死循环长期运行 (主线程负责扫描)
    try:
        while True:
            # 打印当前时间
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] 开始新一轮扫描...")
            
            watcher.run()
            
            print(f"[{now}] 扫描结束，休眠 {CHECK_INTERVAL} 秒...")
            time.sleep(CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        print("\n程序已停止 (用户中断)")
    except Exception as e:
        print(f"\n发生未捕获异常: {e}")

if __name__ == "__main__":
    main()