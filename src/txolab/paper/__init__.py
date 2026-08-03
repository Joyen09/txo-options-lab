"""Phase 6 paper trade（**僅模擬，永不下單**）。

設計上比鐵律 1 要求的更嚴：本套件**完全不含任何下單程式碼**——沒有 place_order、
沒有券商交易 API 呼叫、連 `simulation=True` 的下單路徑都不存在。模擬成交由本地
帳本以行情價自行撮合。要把它變成真單，不是改一個旗標，而是得重寫整個模組；
這正是我們要的阻力。

進出場規則直接呼叫 `backtest.engine` 的 close_reason / entry_gates_ok / size_lots，
與回測共用同一份實作——paper trade 與回測不可能因為「各寫一份」而悄悄分岔。
"""
