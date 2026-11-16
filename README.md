# ゴジラミュート

『シャドウバース ワールズビヨンド』の Windows 版を OBS で配信する際に収益剥奪を回避するための非公式ツールです。
OBS のゲーム映像を画像照合し、ゴジラを検知すると指定した音声ソースをミュートします。盤面でゴジラが見つからない状態が3秒続くと、自動復帰が有効な場合に音声を戻します。
なお検知はゴジラのみとなります。キングギドラは対象外です。
公式が配信者モードの改良アップデートを行った時点でこのツールは非公開となります。

[Windows 版をダウンロード](https://github.com/yukkesaba/godzilla-mute/releases/tag/v0.4.0)

## 使い方

1. ZIP をすべて展開し、GodzillaMuteGUI.exe を開きます。
2. OBS の「ツール → WebSocketサーバー設定」でサーバーを有効にし、ツールにポート番号とパスワードを入力します。パスワードはWebSocketサーバー設定内の「接続情報を表示」からコピー出来ます。既定のポートは 4455 です。
3. 「ソースを取得」を押し、加工前の16:9のゲーム映像と、シャドバだけを拾う音声ソースを選びます。デスクトップ音声はミュートの対象外ですので、アプリケーション音声キャプチャでシャドバの音声のソースを作ってください。
4. 「監視を開始」を押します。実際の音声切り替えと3秒後の自動復帰は既定でオンです。

「録画でテスト」は OBS に接続せず、検知と復帰の時刻を表示します。曲の開始・終了は映像から直接判断できないため、配信前にローカル録画で動作を確認してください。
入力した WebSocket パスワードは保存せず、接続先はローカルの OBS です。

## ソースから起動・ビルド

Windows 64-bit と Python 3.12 を使います。ソースのあるフォルダーで PowerShell を開き、リリースの ZIP をそのフォルダーに置いて実行します。検知用 PNG はリポジトリには含めていません。v0.4.0 の配布 ZIP から3枚をコピーすると、当時の画像照合で動かせます。

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Expand-Archive -LiteralPath .\GodzillaMute-GUI-Windows-v4.zip -DestinationPath .\release-files
Copy-Item .\release-files\GodzillaMuteGUI\_internal\reveal.png, .\release-files\GodzillaMuteGUI\_internal\board.png, .\release-files\GodzillaMuteGUI\_internal\hud.png -Destination .
.\.venv\Scripts\python.exe .\gui.py
~~~

実行ファイルを組み直すには、同じフォルダーで以下を実行します。

~~~powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3
.\.venv\Scripts\python.exe -m PyInstaller GodzillaMuteGUI.spec
~~~

ビルド結果は dist/GodzillaMuteGUI に出力されます。GUI は gui.py、画像照合と OBS WebSocket との通信は godzilla_mute.py にあります。
