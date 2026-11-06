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

`e2eLocal`は従来のHTTP処理の9ケースを検証します。実CRSの18ケースはWASM本体の`mage e2e`で検証します。
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

実CRSのローカル通信確認（正常、SQLi、XSS、リクエスト本文、レスポンス本文）:

```sh
python3 scripts/smoke.py http://localhost:18080
```

`SecRuleEngine DetectionOnly`で検証する場合は第2引数に`DetectionOnly`を指定します。
完全E2Eの追加SSEケースとFTWの結果には未解決事項があります。WASM側の検証記録を確認し、本番配布前に解消・評価してください。
