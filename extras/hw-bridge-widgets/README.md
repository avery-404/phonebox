# 自訂 HTML 小工具範例：硬體橋接 + 歌詞／音樂／GPU／風扇頁 / Example custom HTML widgets with a hardware bridge

## 中文
一組在實機（Xperia 1 II 21:9、Sony XZ Premium 16:9）上日常使用的自訂 HTML 小工具，以及把資料推進 PhoneBox 的橋接程式。僅作範例參考，不是正式小工具。

- `hw_bridge.py`：讀取 MSI Afterburner／RTSS 共享記憶體、FanControl（gRPC）、LRCLIB 同步歌詞與目前播放封面，每秒推送到 `/api/push/hw`、`lyr`、`art`。只讀取，不控制任何硬體。
- `widgets/nowplaying.html`：封面取色動態背景、可點擊跳轉的進度條與歌詞、無播放時自動清空。
- `widgets/lyrics.html`：逐字高亮同步歌詞，歌詞提前量可依播放器分開設定。
- `widgets/gpu.html`、`fans.html`、`perf.html`、`game.html`、`keys.html`：GPU、風扇、效能、遊戲與快捷鍵頁。
- `widgets/common.html`：共用樣式；螢幕長寬比小於 2（16:9）時自動加上 `.lcd`。

需要 Windows、MSI Afterburner（含 RTSS）；風扇頁另需 FanControl。註解皆為繁體中文。

## English
Custom HTML widgets used daily on real devices (Xperia 1 II 21:9, Sony XZ Premium 16:9) plus the bridge script that pushes data into PhoneBox. Reference examples only, not official widgets.

- `hw_bridge.py`: reads MSI Afterburner/RTSS shared memory, FanControl (gRPC), LRCLIB synced lyrics and the current cover, and pushes them to `/api/push/hw`, `lyr` and `art` every second. Read-only; it controls no hardware.
- `widgets/nowplaying.html`: cover-colour animated background, a seekable progress bar and click-to-seek lyrics, clears itself when nothing plays.
- `widgets/lyrics.html`: word-by-word synced lyrics with a per-player lead setting.
- `widgets/gpu.html`, `fans.html`, `perf.html`, `game.html`, `keys.html`: GPU, fans, performance, game and shortcut pages.
- `widgets/common.html`: shared styles; adds `.lcd` automatically when the aspect ratio is below 2 (16:9).

Requires Windows and MSI Afterburner (with RTSS); the fans page also needs FanControl. Comments are in Traditional Chinese.
