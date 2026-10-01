"""説明資料と同じ内容の、Meiryo UI・モノトーンのExcelを作る。"""

from __future__ import annotations

import unicodedata
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.fonts import DEFAULT_FONT
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

DEFAULT_FONT.name = "Meiryo UI"
DEFAULT_FONT.size = 11

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "cognito-access-token-jmeter.xlsx"

INK = "111111"
WHITE = "FFFFFF"
HEADER = "1F1F1F"
TITLE = "1A1A1A"
ALT = "F4F4F4"
INTRO = "F7F7F7"
LINE = "666666"
SOFT = "E6E6E6"
MUTED = "3F3F3F"

ALLOWED = {
    "111111",
    "1A1A1A",
    "1F1F1F",
    "2B2B2B",
    "2F2F2F",
    "3F3F3F",
    "4D4D4D",
    "595959",
    "666666",
    "7A7A7A",
    "B0B0B0",
    "BFBFBF",
    "D9D9D9",
    "E6E6E6",
    "EEEEEE",
    "F4F4F4",
    "F5F5F5",
    "F7F7F7",
    "FFFFFF",
}


def display_units(text: str) -> int:
    total = 0
    for char in text:
        if char == "\n":
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
    return total


def wrapped_line_count(text: str, capacity: int) -> int:
    lines = 0
    for paragraph in (text or "").split("\n"):
        if paragraph == "":
            lines += 1
            continue
        lines += max(1, (display_units(paragraph) + capacity - 1) // capacity)
    return max(1, lines)


def row_height(text: str, capacity: int, size: int = 11) -> float:
    lines = wrapped_line_count(text, max(8, capacity))
    return float(min(409, max(18, lines * (size + 7) + 8)))


def fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def font(size: int = 11, bold: bool = False, color: str = INK) -> Font:
    return Font(name="Meiryo UI", size=size, bold=bold, color=color)


THIN = Border(
    left=Side(style="thin", color=LINE),
    right=Side(style="thin", color=LINE),
    top=Side(style="thin", color=LINE),
    bottom=Side(style="thin", color=LINE),
)
WRAP = Alignment(wrap_text=True, vertical="top", horizontal="left")
WRAP_CENTER = Alignment(wrap_text=True, vertical="center", horizontal="left")


def apply_page(worksheet, title: str) -> None:
    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.paperSize = worksheet.PAPERSIZE_A4
    worksheet.page_setup.fitToWidth = 1
    worksheet.page_setup.fitToHeight = 0
    worksheet.page_setup.horizontalCentered = True
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True
    worksheet.page_margins = PageMargins(left=0.5, right=0.5, top=0.7, bottom=0.6, header=0.3, footer=0.3)
    worksheet.oddHeader.left.text = "CognitoのアクセストークンとJMeter"
    worksheet.oddHeader.right.text = title
    worksheet.oddFooter.left.text = "2026-10-01"
    worksheet.oddFooter.right.text = "&P / &N"
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.zoomScale = 110
    worksheet.sheet_view.view = "pageBreakPreview"
    worksheet.sheet_view.view = "normal"
    worksheet.print_options.horizontalCentered = True
    worksheet.sheet_properties.tabColor = "4D4D4D"
    worksheet.page_setup.scale = 100


def write_title(worksheet, title: str, columns: int) -> None:
    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    cell = worksheet.cell(1, 1, title)
    cell.font = font(16, True, WHITE)
    cell.fill = fill(TITLE)
    cell.alignment = WRAP_CENTER
    for column in range(1, columns + 1):
        other = worksheet.cell(1, column, title if column == 1 else None)
        other.font = font(16, True, WHITE)
        other.fill = fill(TITLE)
        other.alignment = WRAP_CENTER
        other.border = THIN
    worksheet.row_dimensions[1].height = 32
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = None


def write_paragraph(worksheet, row: int, text: str, columns: int, widths: list[float]) -> int:
    worksheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=columns)
    capacity = int(sum(widths[:columns]) * 0.92)
    cell = worksheet.cell(row, 1, text)
    cell.font = font(11, False, INK)
    cell.fill = fill(INTRO)
    cell.alignment = WRAP
    cell.border = THIN
    for column in range(2, columns + 1):
        extra = worksheet.cell(row, column, None)
        extra.font = font(11, False, INK)
        extra.fill = fill(INTRO)
        extra.alignment = WRAP
        extra.border = THIN
    worksheet.row_dimensions[row].height = row_height(text, capacity, 11)
    return row + 1


def write_table(worksheet, row: int, headers: list[str], rows: list[list[str]], widths: list[float]) -> int:
    header_row = row
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row, column, header)
        cell.font = font(11, True, WHITE)
        cell.fill = fill(HEADER)
        cell.alignment = WRAP
        cell.border = THIN
    header_height = 18
    for column, header in enumerate(headers, 1):
        header_height = max(header_height, row_height(header, int(widths[column - 1] * 0.9), 11))
    worksheet.row_dimensions[row].height = min(48, header_height)
    row += 1
    for index, values in enumerate(rows):
        height = 18
        band = ALT if index % 2 else WHITE
        for column, value in enumerate(values, 1):
            cell = worksheet.cell(row, column, value)
            cell.font = font(10, False, INK)
            cell.fill = fill(band)
            cell.alignment = WRAP
            cell.border = THIN
            height = max(height, row_height(value, int(widths[column - 1] * 0.9), 10))
        worksheet.row_dimensions[row].height = height
        row += 1
    last_column = get_column_letter(len(headers))
    worksheet.auto_filter.ref = f"A{header_row}:{last_column}{row - 1}"
    worksheet.print_title_rows = "1:1"
    worksheet.freeze_panes = f"A{header_row + 1}"
    return row


def add_sheet(workbook: Workbook, name: str, title: str, paragraphs: list[str], headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    worksheet = workbook.active if workbook.active.title == "Sheet" and len(workbook.worksheets) == 1 and workbook.active.max_row == 1 and workbook.active["A1"].value is None else workbook.create_sheet()
    worksheet.title = name
    columns = max(len(headers), 2)
    use_widths = list(widths)
    while len(use_widths) < columns:
        use_widths.append(24)
    for index, width in enumerate(use_widths[:columns], 1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    apply_page(worksheet, name)
    write_title(worksheet, title, columns)
    row = 2
    for paragraph in paragraphs:
        row = write_paragraph(worksheet, row, paragraph, columns, use_widths)
    if headers:
        write_table(worksheet, row, headers, rows, use_widths)
    worksheet.oddHeader.left.font = "Meiryo UI"
    worksheet.oddHeader.right.font = "Meiryo UI"
    worksheet.oddFooter.left.font = "Meiryo UI"
    worksheet.oddFooter.right.font = "Meiryo UI"


def build() -> Workbook:
    workbook = Workbook()
    normal = workbook._named_styles["Normal"]
    normal.font = font(11, False, INK)
    sheets = workbook_sheets()
    first = True
    for spec in sheets:
        if first:
            first = False
        add_sheet(workbook, spec["name"], spec["title"], spec["paragraphs"], spec["headers"], spec["rows"], spec["widths"])
    workbook.properties.title = "CognitoのアクセストークンをJMeterで使い回す"
    workbook.properties.creator = "JMeter Cognito guide"
    workbook.properties.subject = "性能試験専用クライアントとトークンの使い回し"
    return workbook


def workbook_sheets() -> list[dict]:
    return [
        {
            "name": "表紙",
            "title": "CognitoのアクセストークンをJMeterで使い回す",
            "paragraphs": [
                "2026年10月1日時点の確認です。性能試験では、本番アプリのクライアントはそのままにして、性能試験専用のアプリクライアントを新しく作ります。アクセストークンは1回受け取り、有効期限の少し前まで同じトークンをAPI Gatewayへ渡します。",
                "取り出しはJSON抽出です。初期方式はドメイン不要のGetClientTokenです。スレッドの初期値は1本、1回、ランプ1秒です。呼び出し先は http://localhost:8080 、X-API-KEYは XXXXXXXXXXXX です。料金とクォータは公式ページで再確認してください。",
            ],
            "headers": ["読む順", "章", "この章で分かること"],
            "rows": [
                ["1", "結論", "専用クライアントを作り、期限まで使い回す"],
                ["2", "たとえ", "遠足の通行証、黒板、受付"],
                ["3", "歴史", "パスワードからGetClientTokenまでの流れ"],
                ["4", "登場人物", "プール、クライアント、トークン、JMeterの役割"],
                ["5", "クライアントは新しく作る", "既存の人向け窓口へフローを足せない理由"],
                ["6", "毎回取るか使い回すか", "回数、料金、クォータの比較"],
                ["7", "動作原理", "GetClientTokenとoauth2/tokenの中身"],
                ["8", "動作イメージ", "1リクエストが通るまでの順番"],
                ["9", "使い回しの実装", "setUp、ロック、ヘッダ、jtl"],
                ["10", "正規表現とJSON", "欄の名前でトークンを取る理由"],
                ["11", "有効期限", "トークン、スキュー、オーソライザーキャッシュ"],
                ["12", "JMeter要素対応", "OpenAPIからシナリオへの対応"],
                ["13", "初期値一覧", "プロパティと生成コマンド"],
                ["14", "失敗しやすい点", "止まりやすい設定"],
                ["15", "用語", "短い定義"],
                ["16", "ツールの使い方", "WindowsとRHELのコマンド"],
                ["17", "出典", "確認した公式ページ"],
            ],
            "widths": [14, 32, 78],
        },
        {
            "name": "結論",
            "title": "最初にやること",
            "paragraphs": [
                "専用のアプリクライアントを新しく作り、そのIDとシークレットでアクセストークンを受け取ります。有効期限の60秒前まで同じトークンを使い、APIの各リクエストは Authorization: Bearer で黒板の値を読みます。",
                "リクエストのたびにトークンを取りに行くのは、トークン発行そのものを測る試験だけにします。通常のAPI性能試験では、受付の時間をAPIのサンプル時間へ混ぜません。",
            ],
            "headers": ["項目", "採用する形", "理由"],
            "rows": [
                ["クライアント", "性能試験専用を新規作成", "ALLOW_CLIENT_TOKEN_AUTHは、そのクライアントの唯一の認証フロー"],
                ["本番クライアント", "変更しない", "人のサインインと試験用シークレットを分ける"],
                ["トークンの回数", "期限の手前まで使い回す", "毎リクエスト取得はクォータと料金を先に使う"],
                ["取り出し", "JSON PostProcessor", "欄名が決まっている。正規表現は隣の文字列を拾い得る"],
                ["初期方式", "get_client_token", "2026-08-31のGetClientToken。ドメイン不要"],
                ["切り替え", "oauth2_token", "ユーザープールドメインの /oauth2/token を使うとき"],
                ["スレッド", "1 / 1回 / ランプ1秒", "1リクエストの通り道を確認する初期値"],
                ["呼び出し先", "http://localhost:8080", "実行時に API_HOST と API_PORT で上書き"],
                ["X-API-KEY", "XXXXXXXXXXXX", "オーソライザーとは別の固定ヘッダ"],
                ["結果", "results/jmeter-results.jtl と jmeter.log", "jtlはXML。GUIのView Results Treeで本文を見られる"],
            ],
            "widths": [24, 42, 78],
        },
        {
            "name": "たとえ",
            "title": "遠足で見る全体",
            "paragraphs": [
                "教室がAPI、入口の受付がCognito、教室の前の先生がAPI Gatewayのオーソライザーです。生徒のパスポートは人のサインインです。性能試験の機械は生徒ではないので、機械専用の通行証をもらいます。",
                "通行証を毎回折りに並ぶと、受付が混み、教室の測定になりません。一度もらった通行証は時刻の1分前までポケットではなく、校庭の黒板に書いて使います。ポケットのメモはスレッドが終わると捨てられる変数、黒板はプロセス全体から見えるプロパティです。",
            ],
            "headers": ["遠足", "仕組み", "試験での名前"],
            "rows": [
                ["学校", "トークンを発行する場所", "ユーザープール"],
                ["受付窓口", "IDとシークレットを持つ窓口", "アプリクライアント"],
                ["入室できる教室の名前", "通行証に書く権限", "カスタムスコープ"],
                ["機械の通行証", "署名付きJWT", "アクセストークン"],
                ["看板の無い専用窓口", "ドメイン不要のAPI", "GetClientToken"],
                ["看板のある窓口", "OAuthのclient credentials", "/oauth2/token"],
                ["開園前に1回並ぶ係", "本スレッドの前の1回", "setUp Thread Group"],
                ["校庭の黒板", "JVMの中で共有", "props の ACCESS_TOKEN"],
                ["ポケットのメモ", "そのスレッドだけ", "vars"],
                ["窓口の前の一列", "最初の1人だけが取り直す", "Critical Section Controller"],
                ["はさみで切る", "似た文字列も拾う", "正規表現抽出"],
                ["欄の名前で取る", "AccessTokenだけを取る", "JSON抽出"],
                ["別の紙の合言葉", "認可とは別ヘッダ", "X-API-KEY"],
            ],
            "widths": [32, 42, 50],
        },
        {
            "name": "歴史",
            "title": "なぜこの形になったか",
            "paragraphs": [
                "パスワードを相手へ渡す形から、限定した許可だけを渡す形へ移り、その許可を機械どうしでも発行できるようになった流れです。試験で使い回すのは、受付を毎回通ると測定と費用が受付側で頭打ちになるからです。",
            ],
            "headers": ["年", "出来事", "いまの試験との関係"],
            "rows": [
                ["1961", "CTSSでコルバトたちがパスワードを使い始めた", "秘密を、見せる相手と置き場所で分ける"],
                ["1998", "マッツォッキがApache JMeterを始めた", "HTTPシナリオ試験の土台"],
                ["2006-2007", "クック、メッシーナ、ハルフ、レコーダンたちがOAuthを始めた", "パスワードそのものを渡さない流れ"],
                ["2010", "OAuth 1.0がRFC 5849になった", "署名付きの委任"],
                ["2012-10", "OAuth 2.0 RFC 6749とBearer RFC 6750。client credentialsは4.4節", "機械がIDとシークレットで通行証をもらう標準"],
                ["2012", "ハマーがOAuth 2.0から離れた", "使う付与タイプをこちらで明示する必要がある"],
                ["2014-07ごろ", "Cognitoアイデンティティプールがプレビュー", "最初のCognitoは人のユーザープールそのものではない"],
                ["2015-05", "JWTがRFC 7519になった", "署名付きJSONを通行証にできる"],
                ["2015-07", "API Gatewayが一般提供", "トークンを見てAPIへ通す扉"],
                ["2016ごろ", "Cognitoユーザープール", "アプリクライアントの単位"],
                ["2020ごろ", "ユーザープールのclient credentials", "機械向けアクセストークン"],
                ["2023-12", "アクセストークンのカスタムクレーム", "スコープ以外を載せる選択肢"],
                ["2024 re:Invent", "Lite / Essentials / Plusとマネージドログイン", "人の画面と機械のトークンは別"],
                ["2024-05-09", "M2Mトークン料金の開始", "成功したトークン応答で課金"],
                ["2025-11-03", "アプリクライアントごとの月額を廃止", "窓口の個数ではなく成功回数で課金"],
                ["2026-08-31", "GetClientTokenを追加", "ドメインなしでM2Mトークンを受け取れる"],
            ],
            "widths": [18, 62, 52],
        },
        {
            "name": "登場人物",
            "title": "誰が何を持つか",
            "paragraphs": [
                "M2Mのアクセストークンでは、subはクライアントIDです。ユーザー名、グループ、リフレッシュトークンはありません。APIがログインした人の名前を読むなら、試験ユーザーを人数分作り、CSVとOnce Only Controllerで人のトークンを取ります。この生成シナリオは機械の通行証です。",
            ],
            "headers": ["名前", "持っているもの", "試験での扱い"],
            "rows": [
                ["ユーザープール", "発行者 iss とJWKS", "リージョンとプールIDで検証先が決まる"],
                ["専用アプリクライアント", "client id と secret", "your-perf- の初期値のままではsetUpが止める"],
                ["リソースサーバー", "カスタムスコープの定義", "トークンに載せる権限の名前"],
                ["GetClientToken", "ClientAuthenticationResult", "UserPoolIdは送らない。SigV4も不要"],
                ["トークンエンドポイント", "access_token と expires_in", "client_secret_post。Basicは使わない"],
                ["JMeter setUp", "最初の1回の取得", "変数は消えるのでpropsへ書く"],
                ["本スレッド", "API呼び出し", "期限が残っていれば取得を飛ばす"],
                ["API Gateway", "署名とスコープの確認", "結果キャッシュはおよそ300秒で、黒板とは別"],
                ["View Results Tree", "XMLのjtl", "本文を見られる。トークンも残るので秘密として扱う"],
            ],
            "widths": [28, 40, 66],
        },
        {
            "name": "クライアントは新しく作る",
            "title": "専用の窓口を作る",
            "paragraphs": [
                "ALLOW_CLIENT_TOKEN_AUTHは、クライアントシークレットが必要で、そのアプリクライアントの唯一の認証フローです。人のサインインが有効な既存クライアントへは足せません。client credentialsも、認可コードや暗黙的フローと同じクライアントでは有効にしません。",
                "この表のAWS CLIは形です。この環境から実在のユーザープールへは実行していません。ExplicitAuthFlowsはALLOW_CLIENT_TOKEN_AUTHだけにします。",
            ],
            "headers": ["分けるもの", "専用クライアントにすると"],
            "rows": [
                ["シークレットの所在", "本番アプリの秘密を試験PCとjtlへ出さない"],
                ["スコープ", "試験に必要なカスタムスコープだけを許可する"],
                ["ローテーション", "本番リリースと別にシークレットを回せる"],
                ["回数の可視性", "そのクライアントのトークン要求だけを見られる"],
                ["後片付け", "試験が終わったらそのクライアントだけを無効にする"],
                ["GetClientToken", "generate-secret と ALLOW_CLIENT_TOKEN_AUTH のみ。スコープはリソースサーバーで定義"],
                ["ドメイン方式も使うとき", "OAuthフローは client_credentials のみ。AllowedOAuthFlowsUserPoolClient を有効"],
                ["有効期間", "5分から1日。既定1時間。本番APIが想定する長さに合わせる"],
                ["IDの形", "1から128文字の [\\w+]+ 。シークレットは24から64文字。スコープは最大50"],
            ],
            "widths": [32, 100],
        },
        {
            "name": "毎回取るか使い回すか",
            "title": "三つの取り方",
            "paragraphs": [
                "公式料金ページの例は、2026-10-01確認のUS East (N. Virginia)です。10クライアントが各500回成功すると 0.00225 USD × 5,000 = 11.25 USD/月です。複数リージョンへ複製した例は1回 0.002925 USDです。アプリクライアントの個数の月額は2025-11-03に外れています。",
                "1秒1,000リクエストを1時間、毎回トークンを取ると 3,600,000回です。同じ単価で 8,100 USDです。1回だけ受け取って使い回すと 0.00225 USDです。見積もり前に料金ページを開いてください。UserAuthenticationの既定は120リクエスト/秒です。GetClientTokenの公開表上の分類名は、この確認では特定できていません。",
            ],
            "headers": ["やり方", "起きること", "選ぶとき"],
            "rows": [
                ["1回取り、期限まで使う", "トークン時間はAPIサンプルの外。期限の手前だけ取り直す", "通常のAPI性能試験。この資料の推奨"],
                ["リクエストごとに取る", "トークン要求がAPIと同じ回数。受付のクォータと料金が先に尽きた", "トークン発行そのものを測るときだけ"],
                ["正規表現で切り出す", "欄の位置が変わると別の文字列をBearerに付ける", "JSONの欄名があるので選ばない"],
                ["人のトークンをCSVで取る", "ユーザー名が入る。M2MのsubはクライアントID", "APIがログインユーザーを読むとき"],
            ],
            "widths": [32, 62, 40],
        },
        {
            "name": "動作原理",
            "title": "トークンが手元に残るまで",
            "paragraphs": [
                "GetClientTokenは POST https://cognito-idp.{region}.amazonaws.com/ です。Content-Typeは application/x-amz-json-1.1 、X-Amz-Targetは AWSCognitoIdentityProviderService.GetClientToken です。本文は ClientId、Secret、必要なら Scopes です。",
                "成功応答は ClientAuthenticationResult の AccessToken、ExpiresIn、TokenTypeです。抽出パスは $.ClientAuthenticationResult.AccessToken と ExpiresIn、マッチ番号は1です。issは https://cognito-idp.{region}.amazonaws.com/{userPoolId} のままです。",
                "oauth2_tokenは POST https://{domain}/oauth2/token で、本文は grant_type=client_credentials と client_id、client_secret です。スコープが空なら scope は送りません。抽出は $.access_token と $.expires_in です。",
            ],
            "headers": ["段階", "処理"],
            "rows": [
                ["ホスト", "COGNITO_GET_CLIENT_TOKEN_HOSTが空なら cognito-idp.{region}.amazonaws.com をGroovyが作る"],
                ["保存", "propsの ACCESS_TOKEN と TOKEN_EXPIRES_AT_MS。期限は今+(ExpiresIn-スキュー)秒"],
                ["参照", "Authorization: Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}"],
                ["再判定", "TOKEN_NEEDS_REFRESH。判定サンプルは setIgnore で一覧から隠す"],
                ["条件", "If Controllerは置換後の文字列が true かを見る。useExpressionは有効、evaluateAllは無効"],
                ["方式分岐", "${__groovy(\"get_client_token\".equals(vars.get(\"COGNITO_TOKEN_MODE\")))}"],
                ["ロック", "lockNameは cognito-access-token。ロックの中でもう一度期限を見て、まだ必要なら1スレッドだけ取得"],
                ["失敗", "設定不足や抽出失敗は setStopTest。応答コード200のアサーションは assume_success を有効にしない"],
                ["秘密", "ログはトークン長、期限、スキュー、クライアントID末尾4文字、解決したホスト。全文は出さない"],
            ],
            "widths": [18, 116],
        },
        {
            "name": "動作イメージ",
            "title": "1回のAPI呼び出しの順番",
            "paragraphs": [
                "ペット一覧を1スレッドで1回呼ぶときの順番です。スレッドが100本でも、黒板の期限が残っていれば取り直しは走りません。期限の直前に重なると、ロックに入った最初の1本だけがCognitoへ行きます。",
            ],
            "headers": ["順", "誰が", "何をする"],
            "rows": [
                ["1", "setUp", "クライアントIDとシークレットが your-perf- のままなら計画を止める"],
                ["2", "Groovy", "方式に合わせてホストと本文を作る。ログにはホストだけ"],
                ["3", "HTTP", "GetClientTokenまたは /oauth2/token を呼び、応答コード200を確認"],
                ["4", "JSON", "アクセストークンと秒数をマッチ番号1で取る"],
                ["5", "PostProcessor", "シークレットを *** にし、黒板へトークンと期限を書く"],
                ["6", "本スレッド", "期限を見る。受け取った直後なので取り直しは飛ばす"],
                ["7", "Transaction", "OpenAPIの先頭タグごとにまとめる"],
                ["8", "HTTP", "localhost:8080へ、そのサンプラー自身のヘッダで送る"],
                ["9", "結果", "results/jmeter-results.jtl と results/jmeter.log"],
            ],
            "widths": [10, 22, 102],
        },
        {
            "name": "使い回しの実装",
            "title": "生成されるテスト計画",
            "paragraphs": [
                "JMeter 5.6.3、Java 17推奨です。Header Managerは同名ヘッダを上書きせず追加するので、AuthorizationとX-API-KEYは各APIサンプラーの子です。HTTP Request Defaultsのドメインは空で、タイムアウトとHttpClient4だけを持ちます。",
                "jtlは results/jmeter-results.jtl をXMLで保存します。実行スクリプトは -l を渡さず、古いjtlを消してから -j で results/jmeter.log を指定します。GUIではファイル名を選んでEnterで読みます。大きな試験の前にView Results Treeを無効にします。",
            ],
            "headers": ["要素", "設定"],
            "rows": [
                ["Test Plan", "jmeter属性 5.6.3。functional_modeは無効。tearDown_on_shutdownは有効"],
                ["setUp", "1スレッド、loopは文字列の1、ramp 1秒。検証のあと方式ごとのIf"],
                ["トークンHTTP", "HttpClient4、接続10秒、応答30秒、keep-alive、リダイレクト追従"],
                ["JSON抽出", "マッチ番号1。方式ごとにパスが違う"],
                ["応答アサーション", "コレクション名は Asserion.test_strings。test_type 8は応答コードが等しいこと"],
                ["本Thread Group", "1 / 1 / 1。on_sample_errorはcontinue。same_user_on_next_iterationは有効"],
                ["期限If", "条件 ${TOKEN_NEEDS_REFRESH}"],
                ["Critical Section", "cognito-access-token。中で再判定し、まだ必要なら取得"],
                ["Transaction", "タグごと。parentは有効、includeTimersは無効"],
                ["Cookie Manager", "標準、HC4、反復ごとに消さない"],
                ["Cache Manager", "配置するが無効"],
                ["DNS Cache Manager", "有効"],
                ["Once Only", "人のトークンをスレッドごとに1回取るとき。M2Mの使い回しでは使わない"],
            ],
            "widths": [28, 106],
        },
        {
            "name": "正規表現とJSON",
            "title": "欄の名前で取り出す",
            "paragraphs": [
                "トークンは長く、Base64に似た文字が他の欄にも出ます。正規表現は隣の欄をBearerに付けることがあります。JSON PostProcessorは指定したパスだけを取ります。",
                "GetClientTokenの欄名とOAuthの欄名は違います。方式ごとにパスを分けています。取り損なったときの既定値は、空のままAPIへ進まず、保存処理が計画を止めます。",
            ],
            "headers": ["方式", "トークンのパス", "秒数のパス"],
            "rows": [
                ["get_client_token", "$.ClientAuthenticationResult.AccessToken", "$.ClientAuthenticationResult.ExpiresIn"],
                ["oauth2_token", "$.access_token", "$.expires_in"],
                ["アサーション", "Asserion.test_strings / test_type 8 / Assertion.response_code", "200。assume_successは無効"],
            ],
            "widths": [28, 62, 44],
        },
        {
            "name": "有効期限",
            "title": "別々の時計",
            "paragraphs": [
                "トークンの期限、取り直しの余裕、API Gatewayのキャッシュ、人のログインクッキーは別の時計です。スキューの初期値は60秒です。有効期間がスキュー以下のときは、スキューを有効期間の1秒前まで下げます。",
            ],
            "headers": ["時計", "長さ", "誰が持つか"],
            "rows": [
                ["アクセストークン", "5分から1日。既定1時間。応答のExpiresIn", "Cognitoが発行し、JMeterが黒板に覚える"],
                ["スキュー", "初期値60秒。TOKEN_REFRESH_SKEW_SECONDS", "JMeter。期限の手前で取り直す"],
                ["オーソライザーキャッシュ", "およそ300秒がよくある設定", "API Gateway。失効直後の拒否を測るときは別に見る"],
                ["マネージドログイン", "セッションクッキー1時間", "人の画面。M2Mでは使わない"],
                ["リフレッシュトークン", "M2Mの通行証には付かない", "人のサインイン"],
            ],
            "widths": [28, 48, 58],
        },
        {
            "name": "JMeter要素対応",
            "title": "OpenAPIからシナリオへ",
            "paragraphs": [
                "OpenAPI 3.0、3.1、Swagger 2.0を読みます。RHELはPython 3.9のシェル、WindowsはPowerShell 5.1単独です。同じシードでも二つの乱数実装のバイトは一致しません。各エンジンの中では同じシードなら同じデータです。",
                "callbackとwebhookは出しません。patternの先読みと後方参照は再現しません。maxLengthが無い文字列は形を保ったまま上限64文字です。UUIDは切らず、v4の形を保ちます。",
            ],
            "headers": ["OpenAPI", "シナリオでの扱い"],
            "rows": [
                ["operation", "HTTP Request。タグ、パス、メソッド順。重複名は2件目から -2"],
                ["先頭タグ", "Transaction Controller"],
                ["deprecated", "名前の先頭に [deprecated]。--skip-deprecated で除外"],
                ["serversのパス", "--base-path auto のときベースパス。ホストはlocalhostのまま"],
                ["path", "セグメントごとにエンコードして埋め込む"],
                ["query", "フォームの空白は + 。それ以外はHTTPArgumentのエンコード"],
                ["必須ヘッダ", "そのサンプラーのHeader Manager。Authorization、X-API-KEY、Acceptは重複させない"],
                ["JSON body", "例、既定、enum、範囲、patternから初期値。constが優先"],
                ["multipartファイル", "testdata/*.bin。Content-Typeは手で付けない"],
                ["readOnly", "送らない"],
                ["nullable", "必須でなければおおよそ15%でnull"],
                ["$ref 3.0", "兄弟キーは無視"],
                ["$ref 3.1", "兄弟キーを重ねる"],
                ["allOf", "プロパティは浅い上書き。requiredは和。min/maxは締まるほう"],
                ["oneOf / anyOf", "一つを乱数で選ぶ"],
                ["callback / webhook", "シナリオへ出さない"],
            ],
            "widths": [28, 106],
        },
        {
            "name": "初期値一覧",
            "title": "プロパティとコマンド",
            "paragraphs": [
                "ユーザー定義変数は ${__P(名前,初期値)} です。同名の環境変数があると、実行スクリプトが一時プロパティファイルで上書きし、ログには名前だけを出します。日本語は \\uXXXX にします。実行後に一時ファイルは消します。",
            ],
            "headers": ["名前", "初期値", "意味"],
            "rows": [
                ["COGNITO_TOKEN_MODE", "get_client_token", "oauth2_tokenへ切り替えられる"],
                ["COGNITO_REGION", "ap-northeast-1", "GetClientTokenのホスト"],
                ["COGNITO_CLIENT_ID", "your-perf-client-id", "このままではsetUpが止める"],
                ["COGNITO_CLIENT_SECRET", "your-perf-client-secret", "このままではsetUpが止める"],
                ["COGNITO_SCOPES", "空", "空なら送らない。空白かカンマ区切り"],
                ["COGNITO_DOMAIN", "your-domain.auth.ap-northeast-1.amazoncognito.com", "ホスト名だけ。https://は付けない"],
                ["COGNITO_GET_CLIENT_TOKEN_HOST", "空", "空ならリージョンから組み立てる"],
                ["COGNITO_GET_CLIENT_TOKEN_PROTOCOL", "https", "モックではhttpにできる"],
                ["COGNITO_GET_CLIENT_TOKEN_PORT", "443", "モックのポートにできる"],
                ["COGNITO_OAUTH_PROTOCOL", "https", "oauth方式"],
                ["COGNITO_OAUTH_PORT", "443", "oauth方式"],
                ["TOKEN_REFRESH_SKEW_SECONDS", "60", "期限の何秒前に取り直すか"],
                ["API_PROTOCOL / API_HOST / API_PORT", "http / localhost / 8080", "--protocol --host --port"],
                ["X_API_KEY", "XXXXXXXXXXXX", "--api-key"],
                ["CONNECT_TIMEOUT_MS", "10000", "接続"],
                ["RESPONSE_TIMEOUT_MS", "30000", "応答"],
                ["--base-path", "auto", "auto、none、または /v1"],
                ["--seed", "未指定ならランダム", "データを再現する整数"],
                ["--required-only", "オフ", "必須項目だけ"],
                ["--skip-deprecated", "オフ", "deprecatedを除外"],
                ["--methods", "全部", "例 get,post"],
                ["--token-mode", "get_client_token", "oauth2_tokenも選べる"],
                ["終了コード", "0 / 2 / 3 / 4", "成功、引数、ファイルまたは解析、対象APIなし"],
            ],
            "widths": [42, 52, 40],
        },
        {
            "name": "失敗しやすい点",
            "title": "止まりやすい設定",
            "paragraphs": [
                "生成シナリオは、プレースホルダのままではAPIを呼びません。jtlの応答データにはアクセストークンが残ります。",
            ],
            "headers": ["起きやすいこと", "試験ではこうする"],
            "rows": [
                ["本番シークレットを試験へ入れる", "専用クライアントの値を入れる"],
                ["your-perf- のまま実行する", "setUpが計画を止める。実値を環境変数で渡す"],
                ["人のAPIにM2Mトークンを渡す", "subはクライアントID。人の試験はCSVとOnce Only"],
                ["スコープ名が三箇所で違う", "リソースサーバー、クライアント、API Gatewayを同じ名前にする"],
                ["ドメインに https:// を付ける", "ホスト名だけを COGNITO_DOMAIN へ入れる"],
                ["GetClientTokenなのにドメイン作成で止まる", "初期方式はドメイン不要"],
                ["毎リクエストでトークンを取る", "期限まで使い回す。発行試験のときだけ毎回取る"],
                ["Header Managerをグループ直下に置く", "各サンプラーの子に置く。同名は追加される"],
                ["正規表現でJWTを切る", "方式ごとのJSONパスを使う"],
                ["入れ子の ${} やカンマ入り __P を条件に書く", "用意したGroovy式と TOKEN_NEEDS_REFRESH を使う"],
                ["古いjtlを残して再実行する", "実行スクリプトが消してから始める。-lは渡さない"],
                ["大きな試験でView Results Treeを有効のまま", "1件確認のあと無効にする"],
                ["jtlをチャットへ貼る", "アクセストークンが応答データに入っている"],
                ["キャッシュとトークン期限を同じ時計だと思う", "オーソライザーはおよそ300秒で別設定"],
                ["callbackがシナリオに出ると思う", "生成器はcallbackとwebhookをスキップする"],
                ["patternの先読みがそのままデータになる", "対応している書き方だけを再現する"],
            ],
            "widths": [52, 82],
        },
        {
            "name": "用語",
            "title": "短い定義",
            "paragraphs": [
                "たとえの言葉と、設定画面の言葉を並べています。",
            ],
            "headers": ["言葉", "意味"],
            "rows": [
                ["アクセストークン", "APIへ入るための通行証。Bearerヘッダに載せる"],
                ["JWT", "署名付きJSON。ヘッダ、ペイロード、署名の3部分"],
                ["client credentials", "機械がIDとシークレットでトークンをもらう付与タイプ。RFC 6749の4.4節"],
                ["M2M", "人を介さないサービス間の呼び出し"],
                ["アプリクライアント", "ユーザープールの中の一つの窓口"],
                ["スコープ", "通行証に書く、入ってよい場所"],
                ["スキュー", "期限より早く取り直す余裕秒"],
                ["vars", "そのスレッドだけのメモ。setUpの終了で消える"],
                ["props", "そのJMeterプロセスの黒板"],
                ["jtl", "サンプル結果。このシナリオではXML"],
                ["setUp", "本スレッドの前に走るスレッドグループ"],
                ["Critical Section", "同じロック名の処理を一列にするコントローラ"],
            ],
            "widths": [28, 106],
        },
        {
            "name": "ツールの使い方",
            "title": "生成して実行する",
            "paragraphs": [
                "Windowsは powershell -NoProfile -ExecutionPolicy Bypass -File で起動します。Restrictedの実行ポリシーでも、この起動ならスクリプトを読めます。RHELでpython3が無いときの案内は sudo dnf install -y python3 です。",
                "JMeterは JMETER_HOME の bin、またはPATHの jmeter（Windowsは jmeter.bat）です。結果はシナリオと同じディレクトリの results です。",
            ],
            "headers": ["場所", "コマンド"],
            "rows": [
                ["Windows生成", "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\generate-jmx.ps1 -i .\\examples\\petstore.openapi.yaml -o .\\examples\\petstore.jmx --seed 11"],
                ["Windows実行", "環境変数 COGNITO_CLIENT_ID と COGNITO_CLIENT_SECRET を入れて run-jmx.ps1 に jmx を渡す"],
                ["RHEL生成", "./scripts/generate-jmx.sh -i examples/petstore.openapi.yaml -o examples/petstore.jmx --seed 11"],
                ["RHEL実行", "同じ環境変数をexportして ./scripts/run-jmx.sh examples/petstore.jmx"],
                ["ドメイン方式", "--token-mode oauth2_token または COGNITO_TOKEN_MODE=oauth2_token と COGNITO_DOMAIN"],
                ["確認用定義", "examples/petstore.openapi.yaml。ベースパス /v1。ホストはlocalhost:8080。webhookは出ない"],
                ["Excel再生成", "python .\\scripts\\build_guide_xlsx.py"],
                ["単体テスト", "python -m unittest tests.test_generator tests.test_powershell_generator -v"],
            ],
            "widths": [20, 114],
        },
        {
            "name": "出典",
            "title": "2026-10-01に確認したページ",
            "paragraphs": [
                "URLは文字列です。リンクの書式は付けていません。料金の数値は、その日の公式料金ページの例です。",
            ],
            "headers": ["内容", "URL"],
            "rows": [
                ["Cognito料金。M2Mは成功したトークン要求。例は11.25 USD", "https://aws.amazon.com/cognito/pricing/"],
                ["2025-11-03 アプリクライアント月額の廃止", "https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-cognito-removes-machine-machine-app-client-price-dimension/"],
                ["2024-05-09 M2M段階料金の開始", "https://aws.amazon.com/about-aws/whats-new/2024/05/amazon-cognito-tiered-pricing-m2m-usage/"],
                ["2026-08-31 GetClientToken", "https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-cognito-get-client-token/"],
                ["トークンエンドポイント。ALLOW_CLIENT_TOKEN_AUTHは唯一の認証フロー", "https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html"],
                ["アプリクライアントとclient credentialsの条件", "https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html"],
                ["OAuth付与タイプの組み合わせ", "https://docs.aws.amazon.com/cognito/latest/developerguide/federation-endpoints-oauth-grants.html"],
                ["RFC 6749 client credentials 4.4", "https://www.rfc-editor.org/rfc/rfc6749"],
                ["RFC 6750 Bearer", "https://www.rfc-editor.org/rfc/rfc6750"],
                ["RFC 7519 JWT", "https://www.rfc-editor.org/rfc/rfc7519"],
                ["JMeter 5.6.3の変更履歴 2024-01-09", "https://jmeter.apache.org/changes.html"],
            ],
            "widths": [62, 72],
        },
    ]


def assert_workbook(path: Path) -> None:
    from openpyxl import load_workbook

    loaded = load_workbook(path)
    expected = [spec["name"] for spec in workbook_sheets()]
    if loaded.sheetnames != expected:
        raise SystemExit(f"sheets {loaded.sheetnames}")
    for name in loaded.sheetnames:
        worksheet = loaded[name]
        if worksheet.freeze_panes is None:
            raise SystemExit(f"freeze missing {name}")
        if worksheet.page_setup.fitToWidth != 1:
            raise SystemExit(f"fit missing {name}")
        if int(worksheet.page_setup.paperSize) != 9:
            raise SystemExit(f"paper {name} {worksheet.page_setup.paperSize}")
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                name_of_font = cell.font.name
                if name_of_font != "Meiryo UI":
                    raise SystemExit(f"font {name} {cell.coordinate} {name_of_font}")
                color = cell.font.color.rgb if cell.font.color is not None and cell.font.color.rgb is not None else "FF111111"
                rgb = str(color)[-6:].upper()
                if rgb not in ALLOWED:
                    raise SystemExit(f"font color {name} {cell.coordinate} {rgb}")
                if cell.fill.fgColor is not None and cell.fill.fgColor.rgb not in {None, "00000000"}:
                    fill_rgb = str(cell.fill.fgColor.rgb)[-6:].upper()
                    if fill_rgb not in ALLOWED:
                        raise SystemExit(f"fill {name} {cell.coordinate} {fill_rgb}")
                if cell.hyperlink is not None:
                    raise SystemExit(f"hyperlink {name} {cell.coordinate}")
    normal_font = loaded._named_styles["Normal"].font
    if normal_font.name != "Meiryo UI":
        raise SystemExit(f"normal font {normal_font.name}")


def force_meiryo(path: Path) -> None:
    temporary = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename in {"xl/styles.xml", "xl/theme/theme1.xml"}:
                text = data.decode("utf-8")
                text = text.replace("Calibri", "Meiryo UI")
                data = text.encode("utf-8")
            target.writestr(info, data)
    temporary.replace(path)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    workbook = build()
    workbook.save(OUTPUT)
    force_meiryo(OUTPUT)
    assert_workbook(OUTPUT)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
