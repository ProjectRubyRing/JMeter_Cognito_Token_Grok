# CognitoのアクセストークンをJMeterで使い回す

2026年10月1日時点の確認に基づく説明です。料金とサービスクォータは変わるので、試験の前に公式ページをもう一度見てください。

## 最初にやること

性能試験では、本番アプリのクライアントはそのままにして、性能試験専用のアプリクライアントを新しく作ります。そのクライアントのIDとシークレットでアクセストークンを1回受け取り、有効期限の少し前まで同じトークンをAPI Gatewayへ渡します。リクエストのたびにトークンを取りに行きません。

トークンの取り出しは、正規表現ではなくJSON抽出にします。生成されるシナリオの初期方式は、ドメイン不要の `GetClientToken` です。ユーザープールのドメインにある `/oauth2/token` を使うときは、プロパティ `COGNITO_TOKEN_MODE` を `oauth2_token` に変えます。

スレッドは最初、1本、1回、ランプ1秒です。1リクエストの通り道を確認してから、本試験のスレッド数へ上げます。呼び出し先の初期値は `http://localhost:8080` です。ヘッダ `X-API-KEY` の初期値は `XXXXXXXXXXXX` です。

## この資料の読み方

各章は同じ順番で読みます。

1. たとえ。遠足に置き換えて、何が起きているかを見ます。
2. 仕組み。実際の名前と、データの動きを見ます。
3. 歴史。なぜその仕組みが必要になったかを見ます。
4. 試験ではこうする。このリポジトリのシナリオで、どこをどう設定するかを見ます。

小学生向けのたとえと、試験担当者向けの設定は、同じ章の中に両方あります。たとえで全体をつかんでから、表の値を使ってください。

## 遠足のたとえ

学校が遠足に行きます。建物の中には、教室がたくさんあります。教室がAPIです。入口の受付がAmazon Cognitoです。教室の前に立って、名札を見る先生がAPI Gatewayのオーソライザーです。

生徒一人ひとりのパスポートは、人のサインインで受け取るトークンです。名前、組、顔写真に当たる情報が入っています。性能試験の機械は生徒ではありません。機械にはパスポートを渡しません。受付が渡すのは、その機械専用の通行証です。通行証には「どの機械か」と「どの教室に入ってよいか」が書いてあります。生徒の名前や組は書いてありません。

通行証を毎回折りに受付へ並ぶと、受付が混み、お金もかかり、教室へ着く前に列で止まります。一度もらった通行証は、書いてある時刻までポケットに入れて使います。時刻の1分前になったら、もう一度だけもらい直します。

ポケットのメモは、JMeterのスレッド変数 `vars` です。そのスレッドが終わると捨てられます。校庭の黒板は、JMeterのプロパティ `props` です。同じJMeterの中のスレッドから見えます。通行証は黒板に書いて、各教室の前で黒板を読みます。

通行証の文字をはさみで適当に切り取るのが、正規表現抽出です。紙の決まった欄から「通行証」の値だけを取るのが、JSON抽出です。欄の名前が決まっているので、JSON抽出を使います。

## 登場人物

| 名前 | 遠足では | 試験では |
| --- | --- | --- |
| ユーザープール | 学校そのもの | トークンを発行するCognito user pool |
| アプリクライアント | 受付窓口 | client id と client secret を持つ窓口 |
| リソースサーバーとカスタムスコープ | 入ってよい教室の名前 | アクセストークンに載る権限の名前 |
| アクセストークン | 通行証 | JWT。API Gatewayへ `Authorization: Bearer` で渡す |
| GetClientToken | ドメインの看板が無い専用窓口 | 2026年8月31日に追加されたAPI。UserPoolIdは送らない |
| `/oauth2/token` | ドメインの看板がある窓口 | OAuth 2.0 の client credentials |
| JMeterの setUp | 開園前に1回並ぶ係 | 設定を確認し、最初のトークンを黒板へ書く |
| Critical Section Controller | 窓口の前の一列 | 期限が近いとき、最初の1スレッドだけが取り直す |
| API Gateway | 教室の前の先生 | トークンの署名とスコープを見て、APIへ通す |
| X-API-KEY | 別の紙の合言葉 | オーソライザーとは別の固定ヘッダ |

M2Mのアクセストークンでは、`sub` はクライアントIDです。ユーザー名、グループ、リフレッシュトークンはありません。APIが「ログインした人の名前」を読むなら、機械の通行証では足りません。その場合は、試験用のユーザーを人数分作り、CSVと Once Only Controller で人のトークンを取ります。このリポジトリの生成シナリオは、機械の通行証を使う形です。

## 歴史

| 年 | 何が起きたか | いまの試験との関係 |
| --- | --- | --- |
| 1961 | CTSSで、フェルナンド・コルバトたちがパスワードを使い始めた | 共有の秘密を、見せる相手と置き場所で分ける考えの始まり |
| 1998 | ステファノ・マッツォッキが、Apache JServ向けにApache JMeterを始めた | のちのHTTPシナリオ試験の土台 |
| 2006から2007 | ブレイン・クック、クリス・メッシーナ、ラリー・ハルフ、デイビッド・レコーダンたちがOAuthを始めた | パスワードを相手のアプリへ渡さず、限定した許可を渡す流れ |
| 2010 | OAuth 1.0がRFC 5849になった | 署名付きの委任。いまのBearerより手順が多い |
| 2012年10月 | OAuth 2.0がRFC 6749、BearerがRFC 6750になった。client credentialsは4.4節 | 機械がIDとシークレットでアクセストークンをもらう形が標準になった |
| 2012 | エラーン・ハマーがOAuth 2.0から離れた | 仕様が広いので、使う側が「どの付与タイプか」を明示する必要がある |
| 2014年7月ごろ | Cognitoがアイデンティティプールとしてプレビューされた | 最初のCognitoは、人のユーザープールそのものではない |
| 2015年5月 | JWTがRFC 7519になった | アクセストークンを、署名付きのJSONとして渡せる |
| 2015年7月 | API Gatewayが一般提供された | トークンを見てAPIへ通す扉がAWS側にできた |
| 2016年ごろ | Cognitoユーザープールが使えるようになった | サインインとアプリクライアントの単位ができた |
| 2020年ごろ | ユーザープールでclient credentialsが使えるようになった | 人のプールから、機械向けのアクセストークンを出せるようになった |
| 2023年12月 | アクセストークンへカスタムクレームを足せるようになった | スコープ以外の値をトークンへ載せる選択肢 |
| 2024年のre:Invent | 機能プラン Lite / Essentials / Plus と、マネージドログイン | 人のログイン画面と、機械のトークンは別料金・別機能 |
| 2024年5月9日 | M2Mトークンの料金が始まった | 成功したトークン応答の回数で課金する考えが入った |
| 2025年11月3日 | アプリクライアントごとの月額をやめた | 登録した窓口の個数ではなく、成功したトークン要求で課金される |
| 2026年8月31日 | GetClientTokenが追加された | ドメイン、証明書、DNSなしでM2Mトークンを受け取れる |

## クライアントは新しく作る

本番アプリのクライアントには、人のサインイン用のフローが入っています。`ALLOW_CLIENT_TOKEN_AUTH` は、そのアプリクライアントの唯一の認証フローにする、とトークン発行のドキュメントに書かれています。クライアントシークレットも必要です。人のサインインが有効な既存クライアントへ、このフローを足すことはできません。

ドメイン付きの `/oauth2/token` でも同じ分離があります。client credentialsは、クライアントシークレットがあり、認可コードや暗黙的フローを許可していないアプリクライアントで有効にします。

専用クライアントにすると、次が分かれます。

- 本番アプリのシークレットを、試験用のPCや結果ファイルへ出さずに済む。
- 試験用スコープだけを許可できる。
- シークレットのローテーションを、本番リリースと別にできる。
- トークン要求の回数を、そのクライアントだけで見られる。
- 試験が終わったら、そのクライアントだけを無効にできる。

作り方の骨子は次のとおりです。値は自分のプールの実値に置き換えます。このコマンドは、この環境ではAWSへ実行していません。

GetClientToken専用の窓口:

```text
aws cognito-idp create-user-pool-client \
  --user-pool-id <pool-id> \
  --client-name perf-m2m \
  --generate-secret \
  --explicit-auth-flows ALLOW_CLIENT_TOKEN_AUTH \
  --allowed-o-auth-scopes <resource-server>/<scope>
```

`ExplicitAuthFlows` は `ALLOW_CLIENT_TOKEN_AUTH` だけにします。カスタムスコープは、先にリソースサーバーへ定義し、クライアントに許可します。スコープは最大50個です。クライアントIDは1文字から128文字の `[\w+]+` です。シークレットの長さは24文字から64文字です。

ドメインのトークンエンドポイントも使う窓口では、許可するOAuthフローを `client_credentials` だけにし、`AllowedOAuthFlowsUserPoolClient` を有効にします。人のログイン用フローは、そのクライアントへ付けません。両方の取り方が必要なときも、本番の人向けクライアントとは別にします。

アクセストークンの有効期間は、アプリクライアントで5分から1日の範囲です。既定は1時間です。試験の有効期間は、本番APIが想定している長さに合わせます。短くしすぎると取り直しが増え、長くしすぎると失効の試験になりません。

EssentialsまたはPlusでは、Pre token generationのLambdaでスコープを足せることがあります。Liteの範囲だけで足りるなら、リソースサーバーのカスタムスコープで足ります。

## 毎回取るか、使い回すか

三つのやり方を、同じ負荷で比べます。

| やり方 | 何が起きるか | いつ選ぶか |
| --- | --- | --- |
| 1回取り、JSONで取り出して、期限まで使う | トークン要求は期限の手前でだけ起きる。APIのサンプル時間にトークン取得が入らない | この資料の推奨。通常の性能試験 |
| リクエストのたびに取る | トークン要求がAPIと同じ回数になる。受付のクォータと料金を先に使い切る | トークン発行そのものを測る試験だけ |
| 正規表現で応答本文から切り出して渡す | 欄の位置が変わると、別の文字列をBearerに付ける | JSONの欄名が決まっているので選ばない |

公式料金ページの例は、2026年10月1日に確認したUS East (N. Virginia) の単価です。アプリクライアント10個が各500回成功すると、月5,000回で 0.00225 USD × 5,000 = 11.25 USDです。アプリクライアントの個数そのものの月額は、2025年11月3日に外れています。複数リージョンへ複製したクライアントの例は、1回あたり 0.002925 USDです。

毎リクエストで取った場合の大きさは、同じ単価で計算できます。1秒1,000リクエストを1時間続けると、トークン要求は 1,000 × 3,600 = 3,600,000 回です。0.00225 USDを掛けると 8,100 USDです。使い回して1回だけ成功させれば、例の単価では 0.00225 USDです。リージョンと月間の段差で単価は変わるので、見積もり前に https://aws.amazon.com/cognito/pricing/ を開いてください。

回数の上限も分かれます。ユーザープールの `UserAuthentication` の既定は、アカウントのリージョンあたり120リクエスト/秒で、OAuthのトークンエンドポイントと `InitiateAuth` が含まれます。ユーザーごとの読み取りには、別の10リクエスト/秒の目安があり、`InitiateAuth` はそこに数えます。`GetClientToken` が公開クォータ表のどの分類に入るかは、2026年10月1日の確認では表の名前として特定できませんでした。試験前にService Quotasで分類を確認してください。分類がどちらでも、毎リクエストで取りに行くと受付側が先に詰まります。使い回しは、その詰まりをAPIの測定から外します。

API Gatewayのオーソライザー結果キャッシュは、およそ300秒残ることがあります。これはJMeterが黒板に書くトークンの期限とは別です。キャッシュが残っているあいだ、API Gatewayは同じトークンを再検証しないことがあります。トークンを失効させた直後の拒否を測る試験では、キャッシュのTTLを別に見てください。

マネージドログインのセッションクッキーは1時間です。M2Mの通行証とは別物です。

## 動作原理

### GetClientToken

初期方式です。ドメインは要りません。JMeterは次をPOSTします。

```text
POST https://cognito-idp.{region}.amazonaws.com/
Content-Type: application/x-amz-json-1.1
X-Amz-Target: AWSCognitoIdentityProviderService.GetClientToken
```

本文はJSONです。

```json
{"ClientId":"<perf-client-id>","Secret":"<perf-client-secret>","Scopes":["resource/scope"]}
```

`Scopes` は、プロパティ `COGNITO_SCOPES` が空のときは送りません。`UserPoolId` は送りません。プールはクライアントIDから決まります。IAMのSigV4署名は要りません。ホストを空のままにすると、Groovyが `cognito-idp.{COGNITO_REGION}.amazonaws.com` を組み立てます。PrivateLinkや手元のモックでは、`COGNITO_GET_CLIENT_TOKEN_HOST` にホスト名だけを入れます。

成功すると、応答は次の形です。

```json
{"ClientAuthenticationResult":{"AccessToken":"<jwt>","ExpiresIn":3600,"TokenType":"Bearer"}}
```

JSON抽出は、`$.ClientAuthenticationResult.AccessToken` と `$.ClientAuthenticationResult.ExpiresIn` を、マッチ番号1で取ります。発行者 `iss` は `https://cognito-idp.{region}.amazonaws.com/{userPoolId}` のままです。JWKSの場所も、ドメインの有無では変わりません。

### oauth2/token

`COGNITO_TOKEN_MODE` が `oauth2_token` のときだけ動きます。

```text
POST https://{domain}/oauth2/token
Content-Type: application/x-www-form-urlencoded
```

本文は `grant_type=client_credentials&client_id=...&client_secret=...` です。スコープが空でなければ `scope` を足します。値はURLエンコードします。`client_secret_basic` のAuthorizationヘッダは使いません。シークレットは本文に置き、結果へ残る前に `***` へ置き換えます。

抽出パスは `$.access_token` と `$.expires_in` です。

### 黒板への保存

setUpスレッドの変数は、setUpが終わると消えます。取り出したトークンは `props.put("ACCESS_TOKEN", token)` で、JVMの中のプロパティへ置きます。期限は `TOKEN_EXPIRES_AT_MS` です。計算は「今 + (ExpiresIn − スキュー) 秒」です。スキューの初期値は60秒です。ExpiresInがスキュー以下のときは、スキューをExpiresInより1秒短くします。

APIの各リクエストは、ヘッダで次を読みます。

```text
Authorization: Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}
```

`__P` はプロパティを読みます。値が無いときの表示は `TOKEN_NOT_FOUND` です。その文字列のままAPIへは行きません。各APIサンプラーの前段で、トークンが空または `TOKEN_NOT_FOUND` ならアサーションが失敗します。

### 期限の再確認

本スレッドは、APIの前にGroovyで期限を見ます。この判定サンプルは `setIgnore()` で結果一覧から隠します。期限前なら `TOKEN_NEEDS_REFRESH` は `false` で、トークン要求を飛ばします。期限が来ているか、トークンが無いときだけ `true` です。

If Controllerの条件は、置換後の文字列が `true` と等しいかを見ます。`useExpression` は有効、`evaluateAll` は無効です。条件は `${TOKEN_NEEDS_REFRESH}` です。方式の分岐は次です。

```text
${__groovy("get_client_token".equals(vars.get("COGNITO_TOKEN_MODE")))}
```

Groovy文字列の中に追加の引用符をネストせず、カンマも無いので、関数の引数分割を避けられます。

### 1スレッドだけが取り直す

期限が来ると、名前 `cognito-access-token` のCritical Section Controllerで一列になります。列に入ったあと、もう一度期限を見ます。先のスレッドが取り終えていれば、後のスレッドは取りに行きません。ロックの外で毎回待たせないので、期限が十分残っているAPIリクエストはロックに入りません。

トークン要求の時間は、APIのTransaction Controllerの外です。APIのサンプル時間に、受付の時間が混ざりません。setUpのサンプルは、jtlには残ります。

### API Gatewayでの通り方

REST APIの `COGNITO_USER_POOLS` オーソライザーは、ユーザープールのアクセストークンを検証できます。HTTP APIのJWTオーソライザーは、カスタムスコープの要求もできます。JMeterはトークンを発行せず、Cognitoが署名したJWTをBearerで渡します。APIが期待するスコープと、専用クライアントに許可したスコープを同じ名前にします。

## 動作イメージ

1本のスレッドで、ペット一覧を1回呼ぶときの動きです。

1. setUpが、クライアントIDとシークレットが `your-perf-` のままでないかを見ます。プレースホルダのままだと、テストを止めてAPIを呼びません。
2. 方式が `get_client_token` なら、リージョンからホストを作り、JSON本文を変数へ置きます。ログにはホスト名だけを出します。シークレットもトークン全文も出しません。
3. HTTPでGetClientTokenを呼び、応答コードが200であることを確認します。
4. JSON抽出でアクセストークンと秒数を取り出します。
5. 本文やサンプラーデータにシークレットがあれば `***` にします。トークンの文字数、期限、スキュー、クライアントIDの末尾4文字だけをログへ出します。
6. 黒板 `ACCESS_TOKEN` と期限を更新します。
7. 本スレッドが期限を見ます。今受け取ったばかりなので、取り直しは走りません。
8. `pets` などのタグごとにTransaction Controllerが開きます。
9. HTTPリクエストが `http://localhost:8080` へ行きます。ヘッダは、そのサンプラー自身が持ちます。`Authorization`、`X-API-KEY`、OpenAPIで必須のヘッダ、応答の内容種別に合わせた `Accept` です。
10. 結果は `results/jmeter-results.jtl` にXMLで残り、ログは `results/jmeter.log` に残ります。

スレッドを100本にしても、黒板の期限が残っていれば、100本とも手順7で取り直しを飛ばします。期限の直前に重なると、手順のロックで1本だけがCognitoへ行き、残りの99本はその結果を読みます。

## 使い回しの実装

生成されるテスト計画の骨格は次です。

```text
Test Plan  jmeter="5.6.3"
  setUp - Cognito access token          1 thread, 1 loop, ramp 1s
    JSR223 Sampler                      設定の検証。失敗なら setStopTest
    If Controller                       get_client_token のとき
      HTTP Request                      POST GetClientToken
        Header Manager
        JSON PostProcessor              AccessToken, ExpiresIn
        Response Assertion              応答コード 200
        JSR223 PostProcessor            propsへ保存し、シークレットを消す
    If Controller                       oauth2_token のとき
      HTTP Request                      POST /oauth2/token
        同じ種類の子要素
  Thread Group                          1 thread, 1 loop, ramp 1s
    JSR223 Sampler                      期限判定。setIgnore
    If Controller                       ${TOKEN_NEEDS_REFRESH}
      Critical Section Controller       lockName cognito-access-token
        JSR223 Sampler                  ロック内でもう一度判定
        If Controller                   まだ必要なら
          上と同じ2つの方式IfとHTTP
    Transaction Controller              OpenAPIの先頭タグごと
      HTTP Request                      API
        Header Manager                  Authorization と X-API-KEY
        JSR223 Assertion                トークンが黒板にあること
  HTTP Request Defaults                 ホストは空。タイムアウトとHttpClient4だけ
  Cookie Manager                        標準、HC4、反復ごとに消さない
  Cache Manager                         置くが無効
  DNS Cache Manager                     有効
  View Results Tree                     results/jmeter-results.jtl をXMLで保存
```

Header Managerは、同じ名前のヘッダを上書きせず追加します。だから `Authorization` と `X-API-KEY` はスレッドグループの直下に置かず、各APIサンプラーの子にします。

HTTP Request Defaultsのドメインは空です。CognitoとAPIでホストが違うので、各サンプラーがプロトコル、ホスト、ポートを自分で持ちます。接続タイムアウトの初期値は10,000ミリ秒、応答タイムアウトは30,000ミリ秒です。実装はHttpClient4です。

エラー時のスレッド動作は `continue` です。トークン取得そのものの失敗は、Groovyが `setStopTest(true)` で計画を止めます。4xxは成功扱いにする前に失敗になるので、トークン応答の200アサーションは、失敗を成功へ書き換えません。

Once Only Controllerは、人のトークンをスレッドごとに1回取るときの置き場所です。M2Mの使い回しでは使いません。人の試験へ切り替えるときは、CSVに試験ユーザーを並べ、Once OnlyをThread Groupの直下に置きます。

View Results Treeは、GUIで本文を見るためのリスナーです。ファイル名は `results/jmeter-results.jtl` です。`xml`、応答データ、サンプラーデータ、要求ヘッダ、応答ヘッダ、URL、エンコーディングを保存します。アサーション結果の保存は2です。別のSimple Data Writerは置きません。実行スクリプトは、同じファイルを `-l` では渡しません。JMeterはjtlを追記するので、実行前に古いjtlを消します。大きな負荷試験の前に、GUIでView Results Treeを無効にしてください。XMLの全本文は、スレッドが多いとディスクとメモリを使います。

GUIで結果を開くときは、View Results Treeのファイル名を `results/jmeter-results.jtl` に選び、Enterで読み込みます。jtlの応答データにはアクセストークンが残ります。jtlとログは秘密として扱ってください。ログにはトークン全文とシークレットを出さない実装です。サンプラーデータにシークレットが出た場合は `***` に置き換えます。

JMeter 5.6.3は2024年1月9日のリリースで、Java 8以上、推奨はJava 17です。未リリースの新しいマスターが要求するJava 17未満不可には合わせていません。Nashornは使いません。条件はGroovyです。

## 正規表現とJSON

正規表現は、応答のどこかに似た文字列があると、隣の欄を切り取ることがあります。トークンは長く、Base64に似た文字が他の欄にも出ます。JSON PostProcessorは、パスで指定した欄だけを、マッチ番号1で取ります。

GetClientTokenの欄と、OAuthの欄は名前が違います。方式ごとに抽出パスを分けてあるので、片方の応答をもう片方のパスでは読みません。抽出の既定値は、取り損なったことが分かる文字列です。空のまま次のAPIへは進まず、保存処理がテストを止めます。

アサーションのコレクション名は、JMeter 5.6.3の綴り `Asserion.test_strings` に合わせています。`test_type` の8は等しいことの比較で、比較対象は応答コードです。

## 有効期限

| 時計 | 長さ | 誰が持つか |
| --- | --- | --- |
| アクセストークン | 5分から1日。既定1時間。ExpiresInで応答に入る | Cognitoが発行し、JMeterがスキュー付きで黒板に覚える |
| 取り直しのスキュー | 初期値60秒。`TOKEN_REFRESH_SKEW_SECONDS` | JMeter。期限の手前で取り直す |
| オーソライザーの結果キャッシュ | およそ300秒がよくある設定 | API Gateway。JMeterの黒板とは別 |
| マネージドログインのクッキー | 1時間 | 人のログイン画面。M2Mでは使わない |
| リフレッシュトークン | M2Mの通行証には付かない | 人のサインイン用 |

スキューを60秒にしておくと、期限ぎりぎりにAPIへ着いて、途中で切れる取りこぼしを減らせます。本番の有効期間が5分なら、スキューはそれより短くします。生成ロジックは、スキューが有効期間以上のとき、有効期間の1秒前まで下げます。

## JMeter要素とOpenAPIの対応

生成器はOpenAPI 3.0、OpenAPI 3.1、Swagger 2.0を読みます。RHEL 9.8ではPython 3.9で動くシェルが、Windows 11ではWindows PowerShell 5.1だけが動きます。PowerShell版はPythonを呼びません。同じシードでも、Pythonの乱数と.NETの乱数はバイト単位では一致しません。それぞれのエンジンの中では、同じシードなら同じデータを再生成します。

| OpenAPI | シナリオ |
| --- | --- |
| `paths` のoperation | HTTP Request。名前はメソッド、パス、operationId |
| 先頭のタグ | Transaction Controller。タグ、パス、メソッド順に並べる |
| `deprecated: true` | 名前の先頭に `[deprecated]`。`--skip-deprecated` で除外 |
| `servers` のURLパス | `--base-path auto` のときベースパス。ホストはlocalhostのまま |
| pathパラメータ | セグメントごとにエンコードしてパスへ埋め込む |
| query | フォームのときは `+`、それ以外はHTTPArgumentのエンコード |
| 必須ヘッダ | そのサンプラーのHeader Manager。Authorization、X-API-KEY、Acceptは重複させない |
| `requestBody` のJSON | raw body。例、既定値、enum、範囲、patternから初期値を作る |
| multipartのファイル欄 | `testdata/*.bin` をjmxの隣に書く。Content-Typeは手で付けない |
| `readOnly` | 送らない |
| `nullable` | 必須でなければ、おおよそ15%でnull |
| OpenAPI 3.0の `$ref` | 兄弟キーは無視する |
| OpenAPI 3.1の `$ref` | 兄弟キーを重ねる |
| `allOf` | プロパティは浅い上書き。requiredは和。min/maxは締まるほう |
| `oneOf` と `anyOf` | どれか一つを乱数で選ぶ |
| callbackとwebhook | シナリオへは出さない |

データのルールの要点です。`const` が優先です。enumはその中から選びます。整数は、min、max、exclusiveMinimum、multipleOfの中で0から1000を基本にします。`maxLength` が無い文字列は、formatの形を切らず、上限64文字で切ります。UUIDはこの上限で切らないので、36文字のv4の形を保ちます。patternは、文字、`\d` `\w` `\s`、文字クラス、量指定、グループ、選択を扱います。先読みと後方参照では、そのpatternを諦めて別の文字へ落とします。深さは6までです。YAMLの真偽値は `true` と `false` だけです。引用の無い `yes` は文字列のままです。

操作の並びは、タグ、生のパス、メソッド順です。メソッド順は get、post、put、patch、delete、head、options、trace です。同じ表示名が重なると、2件目から operationId に `-2` を付けます。

## 初期値

ユーザー定義変数は `${__P(名前,初期値)}` です。実行時に同名の環境変数があると、実行スクリプトが一時プロパティファイルで上書きします。ログには名前だけを出し、値は出しません。JMeterの `-J` は値をログへ書くため、コマンドラインへは載せていません。プロパティファイルはISO-8859-1で読まれるので、日本語や制御文字は `\uXXXX` にします。実行が終わると一時ファイルは消します。

| プロパティ | 初期値 | 意味 |
| --- | --- | --- |
| `COGNITO_TOKEN_MODE` | `get_client_token` | `oauth2_token` へ切り替えられる |
| `COGNITO_REGION` | `ap-northeast-1` | GetClientTokenのホストに使う |
| `COGNITO_CLIENT_ID` | `your-perf-client-id` | このままではsetUpがテストを止める |
| `COGNITO_CLIENT_SECRET` | `your-perf-client-secret` | このままではsetUpがテストを止める |
| `COGNITO_SCOPES` | 空 | 空白かカンマ区切り。空なら送らない |
| `COGNITO_DOMAIN` | `your-domain.auth.ap-northeast-1.amazoncognito.com` | oauth方式のホスト。`https://` は付けない |
| `COGNITO_GET_CLIENT_TOKEN_HOST` | 空 | 空ならリージョンから組み立てる |
| `COGNITO_GET_CLIENT_TOKEN_PROTOCOL` | `https` | モックでは `http` にできる |
| `COGNITO_GET_CLIENT_TOKEN_PORT` | `443` | モックのポートにできる |
| `COGNITO_OAUTH_PROTOCOL` | `https` | oauth方式のプロトコル |
| `COGNITO_OAUTH_PORT` | `443` | oauth方式のポート |
| `TOKEN_REFRESH_SKEW_SECONDS` | `60` | 期限の何秒前に取り直すか |
| `API_PROTOCOL` | `http` | `--protocol` |
| `API_HOST` | `localhost` | `--host` |
| `API_PORT` | `8080` | `--port` |
| `X_API_KEY` | `XXXXXXXXXXXX` | `--api-key` |
| `CONNECT_TIMEOUT_MS` | `10000` | 接続タイムアウト |
| `RESPONSE_TIMEOUT_MS` | `30000` | 応答タイムアウト |

生成コマンドの主な引数です。

| 引数 | 初期値 | 意味 |
| --- | --- | --- |
| `-i`, `--openapi` | 必須 | OpenAPI 3またはSwagger 2のYAML/JSON |
| `-o`, `--output` | 必須 | 出力する `.jmx` |
| `--host` | `localhost` | APIホスト |
| `--port` | `8080` | APIポート |
| `--protocol` | `http` | `http` または `https` |
| `--api-key` | `XXXXXXXXXXXX` | X-API-KEY |
| `--base-path` | `auto` | `auto`、`none`、または `/v1` |
| `--server-index` | `0` | ベースパスに使うserversの番号 |
| `--seed` | ランダム | テストデータを再現する整数 |
| `--required-only` | オフ | 必須項目だけ送る |
| `--skip-deprecated` | オフ | deprecatedを除外 |
| `--methods` | 全部 | 例 `get,post` |
| `--token-mode` | `get_client_token` | `oauth2_token` も選べる |

終了コードは、成功0、引数の誤り2、ファイルが無い・解析できない3、条件に合うoperationが無い4です。シェルでpython3が無いときは127です。

## 失敗しやすい点

- 本番クライアントのシークレットを、試験のプロパティへ入れる。専用クライアントの値を入れます。
- IDとシークレットを `your-perf-` の初期値のまま実行する。setUpが計画を止めます。
- 人のAPIなのにM2Mトークンを渡す。`sub` はクライアントIDで、ユーザー名は入りません。
- スコープ名が、リソースサーバー、クライアント、API Gatewayで揃っていない。
- ドメインのホストに `https://` を付けてしまう。ホスト名だけを入れます。
- GetClientTokenなのにドメイン必須だと思って、ドメイン作成で止まる。初期方式はドメイン不要です。
- 毎リクエストでトークンを取る。クォータと料金がAPIより先に尽きます。
- Header Managerをスレッドグループ直下に置く。同名ヘッダが重複して追加されます。
- 正規表現でJWTを切る。欄名のJSON抽出を使います。
- `${A${B}}` や、カンマを含む入れ子の `__P` を条件に書く。方式判定は用意したGroovy式を使います。
- jtlを手で残したまま再実行する。実行スクリプトは消してから始めます。`-l` は渡しません。
- 大きな試験でView Results Treeを有効のままにする。確認が終わったら無効にします。
- jtlをチャットやチケットへ貼る。アクセストークンが応答データに入っています。
- オーソライザーのキャッシュと、トークンの期限を同じ時計だと思う。別々に設定します。
- callbackやwebhookがシナリオに出ると思う。生成器はそれらをスキップします。
- patternの先読みがそのままテストデータになると思う。対応している書き方だけを再現します。

## 用語

| 言葉 | 意味 |
| --- | --- |
| アクセストークン | APIへ入るための通行証。Bearerヘッダに載せる |
| JWT | 署名付きのJSON。ヘッダ、ペイロード、署名の3部分 |
| client credentials | 機械がIDとシークレットでトークンをもらうOAuthの付与タイプ |
| M2M | 人を介さないサービス間の呼び出し |
| アプリクライアント | ユーザープールの中の一つの窓口 |
| スコープ | 通行証に書く「入ってよい場所」 |
| スキュー | 期限より早く取り直すための余裕秒 |
| vars | そのスレッドだけのメモ |
| props | そのJMeterプロセスの黒板 |
| jtl | サンプル結果。このシナリオではXML |
| setUp | 本スレッドの前に走るスレッドグループ |

## ツールの使い方

Windows 11では、PowerShell 5.1で次を実行します。実行ポリシーがRestrictedでも、この起動ならファイルを読めます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate-jmx.ps1 -i .\examples\petstore.openapi.yaml -o .\examples\petstore.jmx --seed 11
$env:COGNITO_CLIENT_ID = "<perf-client-id>"
$env:COGNITO_CLIENT_SECRET = "<perf-client-secret>"
$env:COGNITO_REGION = "ap-northeast-1"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-jmx.ps1 .\examples\petstore.jmx
```

RHEL 9.8では、python3が入っている状態で次を実行します。

```bash
chmod +x ./scripts/generate-jmx.sh ./scripts/run-jmx.sh
./scripts/generate-jmx.sh -i examples/petstore.openapi.yaml -o examples/petstore.jmx --seed 11
export COGNITO_CLIENT_ID="<perf-client-id>"
export COGNITO_CLIENT_SECRET="<perf-client-secret>"
export COGNITO_REGION="ap-northeast-1"
./scripts/run-jmx.sh examples/petstore.jmx
```

python3が無いときのシェルの案内は `sudo dnf install -y python3` です。JMeterは `JMETER_HOME` の `bin`、またはPATHの `jmeter`（Windowsは `jmeter.bat`）を使います。結果はシナリオと同じディレクトリの `results/jmeter-results.jtl` と `results/jmeter.log` です。

ドメイン方式へ切り替えるときは、生成時の `--token-mode oauth2_token` または実行時の `COGNITO_TOKEN_MODE=oauth2_token` と、`COGNITO_DOMAIN` を使います。

同梱の `examples/petstore.openapi.yaml` は動作確認用です。サーバーURLのホストは無視し、ベースパス `/v1` と `localhost:8080` を組み合わせます。webhookのoperationはシナリオへ出ません。

Excel版は `docs/cognito-access-token-jmeter.xlsx` です。フォントはMeiryo UI、色は白から黒の階調です。再生成は次です。

```powershell
python .\scripts\build_guide_xlsx.py
```

## 残っている限界

- callbackとwebhookは生成しません。
- patternは、先読みと後方参照に対応しません。
- `GetClientToken` のクォータ分類は、公開表の名前だけでは確定できません。
- 料金の例は2026年10月1日の公式ページのUS Eastの例です。契約とリージョンで変わります。
- jtlの応答データにはアクセストークンが残ります。
- 大きな負荷の前にView Results Treeを無効にします。
- API Gatewayのオーソライザーキャッシュは、JMeterの使い回しとは別です。
- M2Mトークンにユーザー名とグループはありません。
- 二つの生成器の乱数バイトは、同じシードでも一致しません。それぞれの中では再現します。
- この資料のAWS CLIは形の説明であり、実在のユーザープールへは実行していません。

## 出典

確認日は2026年10月1日です。

- Amazon Cognitoの料金。アプリクライアント月額は取らず、成功したM2Mトークン要求で課金する例が載っている。https://aws.amazon.com/cognito/pricing/
- 2025年11月3日。M2Mのアプリクライアント課金を外した発表。https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-cognito-removes-machine-machine-app-client-price-dimension/
- 2024年5月9日。M2Mの段階料金の発表。https://aws.amazon.com/about-aws/whats-new/2024/05/amazon-cognito-tiered-pricing-m2m-usage/
- 2026年8月31日。GetClientTokenの発表。ドメインなしでM2Mトークンを発行できる。https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-cognito-get-client-token/
- トークンエンドポイント。`ALLOW_CLIENT_TOKEN_AUTH` はシークレットが必要で、そのクライアントの唯一の認証フローである、と書かれている。https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html
- アプリクライアント。client credentialsは、シークレットがあり、認可コードや暗黙的フローを許可しないクライアントで有効にする、と書かれている。https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html
- OAuth 2.0の付与タイプ。client credentialsは認可コードや暗黙的フローと同じクライアントでは有効にできない、と書かれている。https://docs.aws.amazon.com/cognito/latest/developerguide/federation-endpoints-oauth-grants.html
- OAuth 2.0 RFC 6749。client credentialsは4.4節。https://www.rfc-editor.org/rfc/rfc6749
- Bearerトークン RFC 6750。https://www.rfc-editor.org/rfc/rfc6750
- JWT RFC 7519。https://www.rfc-editor.org/rfc/rfc7519
- Apache JMeter 5.6.3。2024年1月9日。https://jmeter.apache.org/changes.html
