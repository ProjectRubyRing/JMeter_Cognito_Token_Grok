# JMeterでCognitoのアクセストークンを使い回す

性能試験では、本番アプリのクライアントはそのままにして、性能試験専用のアプリクライアントを新しく作ります。アクセストークンは1回受け取り、有効期限の少し前まで同じトークンをAPI Gatewayへ渡します。

詳しい説明は [docs/cognito-access-token-jmeter.md](docs/cognito-access-token-jmeter.md) と、同じ内容の [docs/cognito-access-token-jmeter.xlsx](docs/cognito-access-token-jmeter.xlsx) です。ExcelはMeiryo UIのモノトーンです。

## シナリオを作る

OpenAPI 3.0、OpenAPI 3.1、Swagger 2.0から、JMeter 5.6.3の `.jmx` を作ります。スレッドは1、反復は1、ランプは1秒です。呼び出し先の初期値は `http://localhost:8080`、`X-API-KEY` の初期値は `XXXXXXXXXXXX` です。トークン方式の初期値は、ドメイン不要の `GetClientToken` です。

Windows 11（Windows PowerShell 5.1。Pythonは不要）:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate-jmx.ps1 -i .\examples\petstore.openapi.yaml -o .\examples\petstore.jmx --seed 11
```

RHEL 9.8（python3）:

```bash
./scripts/generate-jmx.sh -i examples/petstore.openapi.yaml -o examples/petstore.jmx --seed 11
```

終了コードは、成功が0、引数の誤りが2、ファイル無しまたは解析失敗が3、対象のAPIが無いときが4です。

## シナリオを実行する

`COGNITO_CLIENT_ID` と `COGNITO_CLIENT_SECRET` に、専用クライアントの値を入れます。初期値の `your-perf-` のままでは、APIを呼ぶ前に止まります。実行スクリプトは、同名の環境変数を一時プロパティファイル経由で渡し、ログには名前だけを出します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-jmx.ps1 .\examples\petstore.jmx
```

```bash
./scripts/run-jmx.sh examples/petstore.jmx
```

結果はシナリオと同じディレクトリの `results/jmeter-results.jtl` と `results/jmeter.log` です。jtlはXMLで、JMeter GUIのView Results Treeから本文を見られます。jtlにはアクセストークンが残ります。大きな負荷の前にView Results Treeを無効にしてください。

JMeter 5.6.3は `JMETER_HOME` またはPATHから探します。Java 17を推奨します。

## テスト

```powershell
python -m unittest tests.test_generator tests.test_powershell_generator -v
```

PowerShellのテストはWindowsでだけ実行します。Excelの再生成は `python .\scripts\build_guide_xlsx.py` です。
