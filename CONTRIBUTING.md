# 團隊協作規範

## 分支用途

`main` 必須隨時維持可展示狀態。不要把未完成的功能或秘密直接推到 `main`。

初始工作分支如下：

| 分支 | 負責範圍 |
| --- | --- |
| `feature/frontend` | UI、檔案上傳、Agent 執行進度 |
| `feature/backend` | HTTP API、資料儲存、錯誤處理 |
| `feature/agent` | Gemini 串接、提示詞、Function Calling |
| `feature/data` | 測試資料、文件解析、引用 |
| `feature/demo` | 部署、整合測試、Demo 腳本 |

如果兩人需要同時修改同一工作分支，請再開較小的 `feature/<名稱>`、`fix/<名稱>` 或 `docs/<名稱>` 分支。

## 開始工作

```powershell
git fetch origin
git switch feature/agent
git pull --ff-only
```

開始寫程式前，先在 GitHub Issue 或團隊看板記錄任務和負責人。每項任務應控制在數小時內可以合併。

## Commit 與 Push

只加入與這次任務有關的檔案：

```powershell
git status
git add <檔案>
git commit -m "feat: add Gemini provider"
git push
```

Commit 開頭使用 `feat:`、`fix:`、`docs:`、`test:`、`refactor:` 或 `chore:`。

## Pull Request

工作成果可供 Demo 使用時，開 Pull Request 合併到 `main`。影響多人或核心流程的變更，請至少找一位組員實際測試。討論處理完後使用 Squash merge，讓 `main` 的紀錄保持清楚。

合併前：

- 確認專案可以在本機啟動。
- 實際操作這次修改的使用者流程。
- 確認沒有包含 API key、`.env`、憑證或個人測試資料。
- 將最新 `main` 合併進功能分支並處理衝突。

合併後：

```powershell
git switch main
git pull --ff-only
```

## 環境變數

將 `.env.example` 複製成 `.env`。每位組員自行保管 `.env`；只有變數名稱和安全的預設值能放在 `.env.example`。

Gemini key 只能由後端使用。前端呼叫團隊後端，不得取得 `GEMINI_API_KEY`。

部署時，把秘密存進部署平台或 Google Cloud 的秘密設定。不要透過 GitHub Issue、Pull Request、聊天訊息或截圖傳遞秘密。

## 衝突處理

Pull Request 的建立者負責處理該分支的衝突。未與組員協調前，不要 force-push 或重寫別人的分支。
