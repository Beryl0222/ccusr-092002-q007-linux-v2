# 主题雕塑史实审定

管理主题雕塑的史料依据、创作陈述、专家审定、展陈版本和图像权利。

`contracts/work_evidence.json` 保存公开的领域样例，约定作品、证据主张与证据条目的编号关系；样例不含真实个人资料、业务凭据或生产连接信息。

## 领域规则

- **会签表决**：叙述（主张）经多轮会签审定；与作品存在利益冲突的专家不得参加对应表决，法定人数只按无冲突专家计算。
- **争议事实**：未获审定的叙述只能进入受限预展，不能进入展厅版本或正式发布。
- **勘误链**：公开说明被修订后，旧版本保留在公众可见的勘误链中，修订版需重新会签才能换版。
- **权利期限**：图像与文字的线上、线下、教育使用期限分别授予、分别判断；撤回某项权利不影响其他仍然有效的权利与展厅版本。
- **重建追溯**：多轮会签、展厅换版和正式发布完成后，可按任意日期重建当时观众实际看到的内容，并追溯每条叙述的批准依据（会签轮次、表决记录、证据编号）。

## 运行

执行 `python3 service.py --check` 可检查服务身份，运行 `python3 -m unittest discover -s tests -v` 可核对基础契约与领域规则。服务启动后，`/health` 返回项目标识。

## 接口概览

- `POST /works` `/assets` `/evidence` `/experts` `/claims` — 登记作品、素材、证据、专家与叙述
- `POST /claims/{id}/revisions` — 修订公开说明（自动留勘误链）
- `POST /claims/{id}/signoffs`、`POST /signoffs/{id}/votes`、`POST /signoffs/{id}/close` — 会签流程
- `POST /licenses`、`POST /licenses/{id}/revoke` — 权利授予与撤回
- `POST /versions` — 展厅换版；`POST /previews` — 受限预展；`POST /releases` — 正式发布
- `GET /reconstruct?channel=offline|online|education|restricted_preview&date=YYYY-MM-DD` — 按日期重建观众所见
- `GET /claims/{id}/errata` — 公众可见的勘误链；`GET /claims/{id}/provenance` — 批准依据追溯
