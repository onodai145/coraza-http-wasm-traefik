# Coraza HTTP WASM — Traefik Local Plugin

Traefik v3.6.0でフォークしたWASMをLocal Pluginとして読み込みます。公開プラグイン登録は不要です。
`.traefik.yml`の`runtime: wasm`、`type: middleware`、`wasmPath: coraza-http-wasm.wasm`を維持します。

## ローカルビルドを使う

```sh
mise install
WASM_SOURCE=../coraza-http-wasm/build/coraza-http-wasm.wasm mise exec -- go run mage.go prepareLocal
(cd build && sha256sum -c SHA256SUMS)
docker compose -p coraza-local up -d traefik
curl -i 'http://localhost:18080/anything'
curl -iG --data-urlencode 'q=<script>alert(1)</script>' 'http://localhost:18080/anything'
docker compose -p coraza-local down
```

正常通信は200、XSSは実CRSのAnomaly Scoringで403となります。SQLiも同様に検査されます。
既定ポートはTraefik 18080 / httpbin 18000。`TRAEFIK_PORT`、`HTTPBIN_PORT`で変更できます。
ローカル検証用httpbinは本番構成には含めません。

## リクエストサイズの制限

すべてのWAF対象ルートに、サービスに応じた有限の本文上限をWAFの前に設定してください。
サンプルは`request-size-limit` → `waf`の順で、Traefik bufferingの
`maxRequestBodyBytes: 13631488`（13 MiB）を指定しています。これはこのサンプルの値です。
アップロード要件と同時リクエスト数に応じて変更してください。`0`は無制限のため使用しません。

上限超過はContent-Length指定・chunked転送の両方でHTTP 413となり、DetectionOnlyでも適用します。
Corazaの検査上限`SecRequestBodyLimit`とは別の制限です。DetectionOnlyではCoraza標準の
ProcessPartialを維持し、検査上限を超えてもTraefikの上限以内なら元の本文をバックエンドへ渡します。
このWASMはhost v0.7.0の本文保持不具合を回避するため本文を再構築し、hostがその全量をメモリに保持します。
Traefikの`memRequestBodyBytes`（サンプル1 MiB）を超えたディスク退避だけでは、このhostメモリは制限できません。
本文上限に加え、運用環境のメモリ・一時ディスク容量と同時処理数を評価してください。

設定仕様: [Traefik buffering](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/buffering/)、
[Coraza SecRequestBodyLimitAction](https://www.coraza.io/docs/seclang/directives/#secrequestbodylimitaction)。

配置先は`build/`です。マニフェストとWASMを同じディレクトリに置き、
コンテナ内`/plugins-local/src/github.com/onodai145/coraza-http-wasm-traefik`へ読み取り専用マウントします。
静的設定の`experimental.localPlugins.coraza.moduleName`と、このパスを一致させます。
`build/SOURCE`と`build/SHA256SUMS`で出所とハッシュを記録します。

## フォークのReleaseを使う

WASM側の検証済みReleaseを公開してから、タグとSHA256を明示します。

```sh
VERSION=v0.4.0-onodai.1 WASM_SHA256='<公開済みWASMのSHA256>' mise exec -- go run mage.go prepareLocal
```

取得先は`onodai145/coraza-http-wasm`の`coraza-http-wasm-<VERSION>.zip`です。
`latest`への自動追従は行いません。ハッシュはレビューしたReleaseのSHA256SUMSから取得してください。
ZIPはWASM 1ファイルのみを許可し、期待ハッシュ不一致時には既存WASMを保持します。

## 検証とリリース

```sh
mise exec -- go test -tags=mage magefile.go magefile_test.go
WASM_SOURCE=../coraza-http-wasm/build/coraza-http-wasm.wasm mise exec -- go run mage.go e2eLocal
```

`e2eLocal`は従来のHTTP処理の9ケースに加え、本文上限超過413（Content-Length / chunked）と
上限ちょうどのリクエストがWAFに届くことを検証します。実CRSの18ケースはWASM本体の`mage e2e`で検証します。
CIは単体テストとCompose設定を確認し、手動workflowで選択したWASM ReleaseのLocal Plugin E2Eを実行します。

リリース手順:

1. WASM側のunit / E2E / FTW結果とSHA256をレビューする。
2. WASM側に独自タグを作成し、承認後にpush。Draft Releaseをレビューして手動公開する。
3. このリポジトリで上記の明示VERSION / SHA256により取得し、Local Plugin E2Eと実CRS通信を検証する。
4. 承認済みのマニフェスト、WASM、SOURCE、SHA256SUMSをZIPへまとめ、独自タグのReleaseに添付する。
5. 本番設定と既存WASMをバックアップし、承認後に配置してTraefikを再起動する。
6. 正常通信と攻撃遮断、DetectionOnly運用での検知ログを確認する。問題時は以前の設定とWASMへ戻して再起動する。

タグpush、Release公開、本番反映は手動承認で行います。既存の自動mainコミット・タグ付けworkflowは廃止しています。

公式のLocal Plugin設定: https://doc.traefik.io/traefik/v3.6/reference/install-configuration/experimental/plugins/

実CRSのローカル通信確認（正常、SQLi、XSS、リクエスト本文、レスポンス本文、本文保持、サイズ上限）:

```sh
docker compose -p coraza-validation up -d traefik
python3 scripts/verify_local_modes.py
docker compose -p coraza-validation down
```

`verify_local_modes.py`はローカルサンプルの設定を一時変更し、On / DetectionOnlyと
小さな検査上限（1024バイト）を検証した後、元の設定を復元します。検証中に手動で設定を変更しないでください。
終了コードが非0なら失敗です。`finally`での復元が中断された場合は設定差分を確認して復元してください。
設定を変更しない単発確認には`python3 scripts/smoke.py http://localhost:18080 On`を使います。
完全E2Eの追加SSEケースとFTWの結果には未解決事項があります。WASM側の検証記録を確認し、本番配布前に解消・評価してください。
