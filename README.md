# 主题雕塑史实审定发布服务

管理长征主题雕塑展的作品、创作者陈述、田野走访、档案出处、专家意见、修改回应、
多轮会签、展厅版本、图像与文字权利，并支持**任意日期的观众视图重建**与
**每条叙述的批准依据溯源**。

`contracts/work_evidence.json` 保存公开的领域样例与命令/渠道/状态词表；
样例不含真实个人资料、业务凭据或生产连接信息。

## 核心规则

1. **只追加账本**：所有变更都是 JSONL 事件（默认 `data/ledger.jsonl`，可用
   `SCULPTURE_LEDGER` 或 `--ledger` 指定），不覆盖、不删除历史。
2. **争议受限**：被标记争议的事实立即撤出一切公开渠道，只能进入受限预展。
3. **利益冲突回避**：声明与某作品有利益冲突的专家不得对该作品叙述投票；
   即使冲突在投票之后才声明，闭轮时其票也被排除。会签须至少 2 名无冲突
   专家投票，赞成多于反对才通过。
4. **公众勘误链**：公开文本修订必须以一轮审定通过的会签为依据，发布勘误时
   记录旧文→新文，逐号成链，公众可通过 `/api/errata` 查看。
5. **授权分渠道分期限**：图像与文字分别就 `web`（线上）/`offline`（线下）/
   `education`（教育）授权，各自有生效日与截止日；撤回只影响该单项授权，
   不影响其它渠道，也不损坏已固化的展厅版本快照。
6. **展厅版本**：展厅版本是不可变快照，安装/换版按日期生效；线上渠道另有
   公开窗口，撤展（关闭窗口）后旧页面不再公开传播，但任意历史日期仍可重建。
7. **按日期重放**：某日视图仅重放该日期（含）之前的事件，事后补记的争议、
   驳回、勘误、撤回不会污染历史视图。

## 运行

```bash
python3 service.py --check                      # 身份与账本完好性检查
python3 service.py --port 8000                  # 启动服务
python3 -m unittest discover -s tests -v        # 全部契约与领域规则测试
```

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务身份 |
| POST | `/api/commands` | 执行一条命令，body：`{"command": ..., "args": {...}, "date": "可选事件日期"}` |
| GET | `/api/audience?date=YYYY-MM-DD&channel=web\|offline\|education&viewer=public\|restricted` | 重建当日该渠道观众实际所见 |
| GET | `/api/preview/restricted?date=...` | 受限预展（含争议事实、授权不全内容） |
| GET | `/api/claims/<id>/provenance[?date=...]` | 叙述的证据、各轮会签（含被排除的冲突票）、勘误链与当日生效文本 |
| GET | `/api/errata` | 公众可见勘误链 |
| GET | `/api/snapshot` | 当前完整状态（运维用） |

命令清单见 `contracts/work_evidence.json` 的 `commands`，主要包括：
`register_work`、`record_claim`、`record_evidence`（artist_statement/
fieldwork/archive_source/revision_response）、`register_expert`、
`declare_conflict`、`open_review_round`、`cast_vote`、`close_review_round`、
`dispute_claim`、`issue_errata`、`register_asset`、`grant_rights`、
`revoke_rights`、`create_gallery_version`、`supersede_gallery_version`、
`open_publication`、`close_publication`。

### 示例

```bash
curl -X POST localhost:8000/api/commands -H 'Content-Type: application/json' \
  -d '{"command":"grant_rights","args":{
        "grant_id":"GR-CL7-WEB","subject_type":"claim","subject_id":"CL-7",
        "channel":"web","valid_from":"2026-09-20","valid_until":"2027-09-19"}}'

curl "localhost:8000/api/audience?date=2026-10-01&channel=web"
curl "localhost:8000/api/claims/CL-7/provenance?date=2026-10-01"
```

## 代码结构

- `review/core.py`：账本（`Ledger`）、事件重放（`replay`/`State`）与
  `ReviewService`（全部命令与查询）。
- `service.py`：HTTP 入口，保持原有 `/health` 与 `--check` 契约。
- `tests/test_contract.py`：基础契约；`tests/test_review_service.py`：
  覆盖 COI 排除、争议预展、勘误链、三渠道期限与撤回隔离、展厅换版、撤展、
  任意日期重建与溯源、磁盘重放的端到端测试。
