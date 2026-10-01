"""审定发布服务的领域核心。

设计要点：
- 所有状态变更都是只追加（append-only）事件，落盘为 JSONL；任意日期的观众视图
  都通过重放事件重建，不覆盖、不删除历史记录。
- 叙述（claim）的批准依据可逐级追溯：证据（档案/田野/创作者陈述）→ 多轮会签
  （含利益冲突排除记录）→ 审定决定 → 勘误链。
- 图像与文字的使用授权按渠道（web 线上 / offline 线下 / education 教育）分别
  判定有效期限；撤回只影响被撤回的那一项授权，展厅版本快照不受影响。
- 争议事实不得进入公开视图，只能进入受限预展。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any, Optional

SERVICE_ID = "sculpture-evidence-review"

CHANNELS = ("web", "offline", "education")
VIEWER_PUBLIC = "public"
VIEWER_RESTRICTED = "restricted"

CLAIM_DRAFT = "draft"
CLAIM_DISPUTED = "disputed"
CLAIM_APPROVED = "approved"
CLAIM_REJECTED = "rejected"


class ValidationError(Exception):
    """命令不满足领域规则。"""


def today() -> str:
    return _date.today().isoformat()


# ---------------------------------------------------------------------------
# 只追加账本
# ---------------------------------------------------------------------------


class Ledger:
    """JSONL 只追加账本：每行一个事件，按写入顺序编号。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._events: list[dict] = []
        self._lock = threading.Lock()
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._events.append(json.loads(line))

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def append(self, event_type: str, payload: dict, on_date: Optional[str] = None) -> dict:
        event = {
            "seq": len(self._events) + 1,
            "type": event_type,
            "date": on_date or payload.get("date") or today(),
            "recorded_at": today(),
            "payload": payload,
        }
        with self._lock:
            self._events.append(event)
            if self.path:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


# ---------------------------------------------------------------------------
# 重放得到的状态
# ---------------------------------------------------------------------------


@dataclass
class ClaimState:
    id: str
    work_id: str
    initial_text: str
    status: str = CLAIM_DRAFT
    effective_text: str = ""
    decisions: list[str] = field(default_factory=list)  # round_id 时间序
    errata_chain: list[str] = field(default_factory=list)  # errata_id 时间序
    dispute: Optional[dict] = None


@dataclass
class RoundState:
    id: str
    claim_id: str
    opened: str
    closed: Optional[str] = None
    revised_text: Optional[str] = None
    votes: dict[str, dict] = field(default_factory=dict)  # expert_id -> vote
    decision: Optional[str] = None
    decision_date: Optional[str] = None


@dataclass
class State:
    works: dict[str, dict] = field(default_factory=dict)
    claims: dict[str, ClaimState] = field(default_factory=dict)
    evidence: dict[str, dict] = field(default_factory=dict)
    experts: dict[str, dict] = field(default_factory=dict)
    conflicts: set[tuple[str, str]] = field(default_factory=set)  # (expert_id, work_id)
    rounds: dict[str, RoundState] = field(default_factory=dict)
    errata: dict[str, dict] = field(default_factory=dict)
    assets: dict[str, dict] = field(default_factory=dict)
    grants: dict[str, dict] = field(default_factory=dict)
    versions: list[dict] = field(default_factory=list)
    publications: list[dict] = field(default_factory=list)  # 开启/关闭窗口事件视图
    pub_windows: dict[str, dict] = field(default_factory=dict)  # channel|work -> window


def replay(events: list[dict]) -> State:
    st = State()
    for ev in events:
        p = ev["payload"]
        d = ev["date"]
        t = ev["type"]

        if t == "work_registered":
            st.works[p["work_id"]] = {"work_id": p["work_id"], "title": p["title"], "date": d}

        elif t == "claim_recorded":
            st.claims[p["claim_id"]] = ClaimState(
                id=p["claim_id"], work_id=p["work_id"], initial_text=p["text"],
                effective_text=p["text"],
            )

        elif t == "claim_disputed":
            claim = st.claims[p["claim_id"]]
            claim.status = CLAIM_DISPUTED
            claim.dispute = {"date": d, "reason": p.get("reason", "")}

        elif t in ("artist_statement_recorded", "fieldwork_recorded", "archive_source_recorded"):
            st.evidence[p["evidence_id"]] = {
                "evidence_id": p["evidence_id"],
                "kind": t.replace("_recorded", ""),
                "work_id": p.get("work_id"),
                "claim_id": p.get("claim_id"),
                "detail": p.get("detail", ""),
                "source": p.get("source", ""),
                "date": d,
            }

        elif t == "expert_registered":
            st.experts[p["expert_id"]] = {"expert_id": p["expert_id"], "name": p.get("name", "")}

        elif t == "conflict_declared":
            st.conflicts.add((p["expert_id"], p["work_id"]))

        elif t == "review_round_opened":
            st.rounds[p["round_id"]] = RoundState(
                id=p["round_id"], claim_id=p["claim_id"], opened=d,
                revised_text=p.get("revised_text"),
            )

        elif t == "vote_cast":
            rnd = st.rounds[p["round_id"]]
            rnd.votes[p["expert_id"]] = {
                "expert_id": p["expert_id"], "vote": p["vote"],
                "note": p.get("note", ""), "date": d,
            }

        elif t == "review_round_closed":
            rnd = st.rounds[p["round_id"]]
            rnd.closed = d
            rnd.decision = p["decision"]
            rnd.decision_date = d
            claim = st.claims[rnd.claim_id]
            claim.decisions.append(rnd.id)
            if p["decision"] == "approved":
                claim.status = CLAIM_APPROVED
                claim.dispute = None
                # 注意：修订文本在此仅获审定授权，公开文本要等勘误发布后才替换，
                # 以保证公众始终能看到完整勘误链。
            else:
                claim.status = CLAIM_REJECTED

        elif t == "revision_response_recorded":
            # 修改回应作为针对 claim 的证据性材料留痕
            st.evidence[p["evidence_id"]] = {
                "evidence_id": p["evidence_id"], "kind": "revision_response",
                "work_id": st.claims[p["claim_id"]].work_id, "claim_id": p["claim_id"],
                "detail": p.get("detail", ""), "source": p.get("source", ""),
                "round_id": p.get("round_id"), "date": d,
            }

        elif t == "errata_issued":
            st.errata[p["errata_id"]] = {
                "errata_id": p["errata_id"], "claim_id": p["claim_id"],
                "round_id": p["round_id"], "old_text": p["old_text"],
                "new_text": p["new_text"], "date": d,
            }
            claim = st.claims[p["claim_id"]]
            claim.errata_chain.append(p["errata_id"])
            claim.effective_text = p["new_text"]

        elif t == "asset_registered":
            st.assets[p["asset_id"]] = {
                "asset_id": p["asset_id"], "work_id": p["work_id"],
                "kind": p.get("kind", "image"), "title": p.get("title", ""), "date": d,
            }

        elif t == "rights_granted":
            st.grants[p["grant_id"]] = {
                "grant_id": p["grant_id"], "subject_type": p["subject_type"],
                "subject_id": p["subject_id"], "channel": p["channel"],
                "valid_from": p["valid_from"], "valid_until": p.get("valid_until"),
                "revoked_at": None, "date": d,
            }

        elif t == "rights_revoked":
            st.grants[p["grant_id"]]["revoked_at"] = d

        elif t == "gallery_version_created":
            st.versions.append({
                "version_id": p["version_id"], "label": p.get("label", ""),
                "install_date": p["install_date"], "superseded_date": None,
                "exhibits": p.get("exhibits", []),
            })
            st.versions.sort(key=lambda v: v["install_date"])

        elif t == "gallery_version_superseded":
            for ver in st.versions:
                if ver["version_id"] == p["version_id"]:
                    ver["superseded_date"] = d

        elif t == "publication_opened":
            key = f"{p['channel']}|{p['work_id']}"
            st.pub_windows[key] = {"channel": p["channel"], "work_id": p["work_id"],
                                   "opened": d, "closed": None}
            st.publications.append({"channel": p["channel"], "work_id": p["work_id"],
                                    "opened": d, "closed": None})

        elif t == "publication_closed":
            key = f"{p['channel']}|{p['work_id']}"
            if key in st.pub_windows:
                st.pub_windows[key]["closed"] = d
            for pub in reversed(st.publications):
                if pub["channel"] == p["channel"] and pub["work_id"] == p["work_id"] \
                        and pub["closed"] is None:
                    pub["closed"] = d
                    break

    return st


# ---------------------------------------------------------------------------
# 审定发布服务
# ---------------------------------------------------------------------------


class ReviewService:
    """对外的全部命令与查询。命令幂等性由调用方自行用编号约束。"""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    # -- 内部工具 ---------------------------------------------------------

    def _state(self) -> State:
        return replay(self.ledger.events)

    @staticmethod
    def _require(cond: bool, msg: str):
        if not cond:
            raise ValidationError(msg)

    def _get_claim(self, st: State, claim_id: str) -> ClaimState:
        self._require(claim_id in st.claims, f"未知叙述 {claim_id}")
        return st.claims[claim_id]

    def _get_round(self, st: State, round_id: str) -> RoundState:
        self._require(round_id in st.rounds, f"未知会签轮次 {round_id}")
        return st.rounds[round_id]

    # -- 命令 -------------------------------------------------------------

    def command(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"cmd_{name}", None)
        if handler is None:
            raise ValidationError(f"未知命令 {name}")
        return handler(**args)

    def cmd_register_work(self, work_id: str, title: str, date: Optional[str] = None) -> dict:
        st = self._state()
        self._require(work_id not in st.works, f"作品 {work_id} 已登记")
        return self.ledger.append("work_registered", {"work_id": work_id, "title": title}, date)

    def cmd_record_claim(self, work_id: str, claim_id: str, text: str,
                         date: Optional[str] = None) -> dict:
        st = self._state()
        self._require(work_id in st.works, f"未知作品 {work_id}")
        self._require(claim_id not in st.claims, f"叙述 {claim_id} 已存在")
        return self.ledger.append("claim_recorded",
                                  {"work_id": work_id, "claim_id": claim_id, "text": text}, date)

    def cmd_record_evidence(self, kind: str, evidence_id: str, detail: str,
                            source: str = "", work_id: Optional[str] = None,
                            claim_id: Optional[str] = None,
                            round_id: Optional[str] = None,
                            date: Optional[str] = None) -> dict:
        """登记创作者陈述 / 田野走访 / 档案出处 / 修改回应。"""
        st = self._state()
        self._require(evidence_id not in st.evidence, f"证据 {evidence_id} 已存在")
        if claim_id:
            claim = self._get_claim(st, claim_id)
            work_id = claim.work_id
            if round_id:
                rnd = self._get_round(st, round_id)
                self._require(rnd.claim_id == claim_id, "回应所关联轮次与叙述不匹配")
        self._require(work_id in st.works if work_id else True, f"未知作品 {work_id}")
        type_map = {
            "artist_statement": "artist_statement_recorded",
            "fieldwork": "fieldwork_recorded",
            "archive_source": "archive_source_recorded",
            "revision_response": "revision_response_recorded",
        }
        self._require(kind in type_map, f"未知证据类型 {kind}")
        if kind == "revision_response":
            self._require(claim_id is not None, "修改回应必须关联到具体叙述")
        payload = {"evidence_id": evidence_id, "detail": detail, "source": source,
                   "work_id": work_id, "claim_id": claim_id}
        if kind == "revision_response":
            payload["round_id"] = round_id
        return self.ledger.append(type_map[kind], payload, date)

    def cmd_register_expert(self, expert_id: str, name: str,
                            date: Optional[str] = None) -> dict:
        st = self._state()
        self._require(expert_id not in st.experts, f"专家 {expert_id} 已登记")
        return self.ledger.append("expert_registered",
                                  {"expert_id": expert_id, "name": name}, date)

    def cmd_declare_conflict(self, expert_id: str, work_id: str,
                             date: Optional[str] = None) -> dict:
        """声明利益冲突；该专家不得参与该作品相关叙述的表决，其票关闭时排除。"""
        st = self._state()
        self._require(expert_id in st.experts, f"未知专家 {expert_id}")
        self._require(work_id in st.works, f"未知作品 {work_id}")
        if (expert_id, work_id) in st.conflicts:
            return self.ledger.append("conflict_declared",
                                      {"expert_id": expert_id, "work_id": work_id}, date)
        return self.ledger.append("conflict_declared",
                                  {"expert_id": expert_id, "work_id": work_id}, date)

    def cmd_open_review_round(self, claim_id: str, round_id: str,
                              revised_text: Optional[str] = None,
                              date: Optional[str] = None) -> dict:
        st = self._state()
        self._get_claim(st, claim_id)
        self._require(round_id not in st.rounds, f"轮次 {round_id} 已存在")
        return self.ledger.append("review_round_opened", {
            "claim_id": claim_id, "round_id": round_id, "revised_text": revised_text}, date)

    def cmd_cast_vote(self, round_id: str, expert_id: str, vote: str,
                      note: str = "", date: Optional[str] = None) -> dict:
        """投票。有利益冲突的专家直接被拒绝；即便冲突在投票后才声明，闭轮时仍排除。"""
        st = self._state()
        rnd = self._get_round(st, round_id)
        self._require(rnd.closed is None, f"轮次 {round_id} 已关闭")
        self._require(expert_id in st.experts, f"未知专家 {expert_id}")
        self._require(vote in ("approve", "reject"), "vote 只能是 approve/reject")
        claim = st.claims[rnd.claim_id]
        self._require((expert_id, claim.work_id) not in st.conflicts,
                      f"专家 {expert_id} 与作品 {claim.work_id} 存在利益冲突，不得参加表决")
        self._require(expert_id not in rnd.votes, f"专家 {expert_id} 已在本轮投票")
        return self.ledger.append("vote_cast", {
            "round_id": round_id, "expert_id": expert_id, "vote": vote, "note": note}, date)

    def cmd_close_review_round(self, round_id: str, date: Optional[str] = None) -> dict:
        """闭轮并按有效票（排除利益冲突方）形成审定决定。

        规则：至少 2 名无冲突专家投票，赞成多于反对方可通过；否则驳回。
        """
        st = self._state()
        rnd = self._get_round(st, round_id)
        self._require(rnd.closed is None, f"轮次 {round_id} 已关闭")
        claim = st.claims[rnd.claim_id]
        eligible_votes, excluded = [], []
        for expert_id, v in rnd.votes.items():
            if (expert_id, claim.work_id) in st.conflicts:
                excluded.append(expert_id)
            else:
                eligible_votes.append(v)
        approvals = sum(1 for v in eligible_votes if v["vote"] == "approve")
        rejections = len(eligible_votes) - approvals
        self._require(len(eligible_votes) >= 2,
                      f"轮次 {round_id} 有效表决不足 2 票，不能形成审定决定")
        decision = "approved" if approvals > rejections else "rejected"
        return self.ledger.append("review_round_closed", {
            "round_id": round_id, "decision": decision,
            "eligible_votes": len(eligible_votes), "approvals": approvals,
            "rejections": rejections, "excluded_conflicted": excluded}, date)

    def cmd_dispute_claim(self, claim_id: str, reason: str = "",
                          date: Optional[str] = None) -> dict:
        """将事实标记为争议：立即撤出公开视图，只能进入受限预展。"""
        st = self._state()
        self._get_claim(st, claim_id)
        return self.ledger.append("claim_disputed",
                                  {"claim_id": claim_id, "reason": reason}, date)

    def cmd_issue_errata(self, claim_id: str, errata_id: str, round_id: str,
                         new_text: Optional[str] = None,
                         date: Optional[str] = None) -> dict:
        """发布公众可见勘误：修订文本必须已有一轮审定通过，勘误逐号成链。"""
        st = self._state()
        claim = self._get_claim(st, claim_id)
        rnd = self._get_round(st, round_id)
        self._require(rnd.claim_id == claim_id, "勘误依据轮次与叙述不匹配")
        self._require(rnd.closed is not None and rnd.decision == "approved",
                      "勘误所依据的轮次尚未审定通过")
        self._require(errata_id not in st.errata, f"勘误 {errata_id} 已存在")
        final_text = new_text or rnd.revised_text
        self._require(bool(final_text), "勘误必须包含修订后的文本")
        old_text = claim.effective_text
        return self.ledger.append("errata_issued", {
            "errata_id": errata_id, "claim_id": claim_id, "round_id": round_id,
            "old_text": old_text, "new_text": final_text}, date)

    def cmd_register_asset(self, asset_id: str, work_id: str, kind: str = "image",
                           title: str = "", date: Optional[str] = None) -> dict:
        st = self._state()
        self._require(work_id in st.works, f"未知作品 {work_id}")
        self._require(asset_id not in st.assets, f"资料 {asset_id} 已登记")
        return self.ledger.append("asset_registered", {
            "asset_id": asset_id, "work_id": work_id, "kind": kind, "title": title}, date)

    def cmd_grant_rights(self, grant_id: str, subject_type: str, subject_id: str,
                         channel: str, valid_from: str,
                         valid_until: Optional[str] = None,
                         date: Optional[str] = None) -> dict:
        """对图像(asset)或文字(claim)授予某渠道、带期限的使用权。各渠道独立。"""
        st = self._state()
        self._require(channel in CHANNELS, f"未知渠道 {channel}")
        self._require(subject_type in ("asset", "claim"), "subject_type 只能是 asset/claim")
        if subject_type == "asset":
            self._require(subject_id in st.assets, f"未知资料 {subject_id}")
        else:
            self._require(subject_id in st.claims, f"未知叙述 {subject_id}")
        self._require(grant_id not in st.grants, f"授权 {grant_id} 已存在")
        if valid_until:
            self._require(valid_until >= valid_from, "授权截止日早于生效日")
        return self.ledger.append("rights_granted", {
            "grant_id": grant_id, "subject_type": subject_type, "subject_id": subject_id,
            "channel": channel, "valid_from": valid_from,
            "valid_until": valid_until}, date)

    def cmd_revoke_rights(self, grant_id: str, date: Optional[str] = None) -> dict:
        """撤回单项授权：不影响同一内容在其它渠道的授权，也不改动展厅版本快照。"""
        st = self._state()
        self._require(grant_id in st.grants, f"未知授权 {grant_id}")
        self._require(st.grants[grant_id]["revoked_at"] is None, f"授权 {grant_id} 已撤回")
        return self.ledger.append("rights_revoked", {"grant_id": grant_id}, date)

    def cmd_create_gallery_version(self, version_id: str, install_date: str,
                                   exhibits: list[dict], label: str = "",
                                   date: Optional[str] = None) -> dict:
        """创建不可变展厅版本（快照）。exhibits: [{work_id, claim_ids, asset_ids}]。"""
        st = self._state()
        self._require(not any(v["version_id"] == version_id for v in st.versions),
                      f"展厅版本 {version_id} 已存在")
        for ex in exhibits:
            self._require(ex["work_id"] in st.works, f"展品含未知作品 {ex['work_id']}")
            for cid in ex.get("claim_ids", []):
                self._require(cid in st.claims, f"展品含未知叙述 {cid}")
            for aid in ex.get("asset_ids", []):
                self._require(aid in st.assets, f"展品含未知资料 {aid}")
        return self.ledger.append("gallery_version_created", {
            "version_id": version_id, "label": label, "install_date": install_date,
            "exhibits": exhibits}, date)

    def cmd_supersede_gallery_version(self, version_id: str,
                                      date: Optional[str] = None) -> dict:
        st = self._state()
        self._require(any(v["version_id"] == version_id for v in st.versions),
                      f"未知展厅版本 {version_id}")
        return self.ledger.append("gallery_version_superseded",
                                  {"version_id": version_id}, date)

    def cmd_open_publication(self, channel: str, work_id: str,
                             date: Optional[str] = None) -> dict:
        """开启作品在某渠道的公开窗口（撤展通过 close 关闭，关闭日后旧页面不再可见）。"""
        st = self._state()
        self._require(channel in CHANNELS, f"未知渠道 {channel}")
        self._require(work_id in st.works, f"未知作品 {work_id}")
        key = f"{channel}|{work_id}"
        self._require(key not in st.pub_windows or st.pub_windows[key]["closed"] is not None,
                      f"作品 {work_id} 在 {channel} 已处于公开窗口")
        return self.ledger.append("publication_opened",
                                  {"channel": channel, "work_id": work_id}, date)

    def cmd_close_publication(self, channel: str, work_id: str,
                              date: Optional[str] = None) -> dict:
        st = self._state()
        key = f"{channel}|{work_id}"
        self._require(key in st.pub_windows and st.pub_windows[key]["closed"] is None,
                      f"作品 {work_id} 在 {channel} 没有开启中的公开窗口")
        return self.ledger.append("publication_closed",
                                  {"channel": channel, "work_id": work_id}, date)

    # -- 查询 -------------------------------------------------------------

    def _state_as_of(self, on_date: str) -> State:
        """重放截至 on_date（含）已记录的全部事件，得到当时的状态。

        事后补记的争议、驳回、勘误、撤回不会污染历史日期的观众视图。
        """
        return replay([e for e in self.ledger.events if e["date"] <= on_date])

    @staticmethod
    def _right_valid(grant: dict, on_date: str) -> bool:
        if grant["revoked_at"] is not None and grant["revoked_at"] <= on_date:
            return False
        if grant["valid_from"] > on_date:
            return False
        if grant["valid_until"] and grant["valid_until"] < on_date:
            return False
        return True

    def _right_ok(self, st: State, subject_type: str, subject_id: str,
                  channel: str, on_date: str) -> bool:
        for g in st.grants.values():
            if g["subject_type"] == subject_type and g["subject_id"] == subject_id \
                    and g["channel"] == channel and self._right_valid(g, on_date):
                return True
        return False

    def _active_version(self, st: State, on_date: str) -> Optional[dict]:
        """当日实际在展的展厅版本：安装日 <= 当日，且当日尚未被换版撤下。"""
        candidate = None
        for ver in st.versions:
            if ver["install_date"] <= on_date and (
                    ver["superseded_date"] is None or ver["superseded_date"] > on_date):
                if candidate is None or ver["install_date"] > candidate["install_date"]:
                    candidate = ver
        return candidate

    def _published(self, st: State, channel: str, work_id: str, on_date: str) -> bool:
        win = st.pub_windows.get(f"{channel}|{work_id}")
        if not win or win["opened"] > on_date:
            return False
        return win["closed"] is None or win["closed"] > on_date

    def audience_view(self, on_date: str, channel: str = "web",
                      viewer: str = VIEWER_PUBLIC) -> dict:
        """重建 on_date 当天该渠道观众实际看到的内容。

        - offline 渠道以在展的展厅版本快照为准；web/education 还要求公开窗口开启。
        - 图像与文字分别检查该渠道、当日有效的授权；被撤回或过期的内容不出现。
        - 争议叙述仅在受限预展(viewer=restricted)出现并带标记。
        """
        if channel not in CHANNELS:
            raise ValidationError(f"未知渠道 {channel}")
        st = self._state_as_of(on_date)
        version = self._active_version(st, on_date)
        if version is None:
            return {"date": on_date, "channel": channel, "viewer": viewer,
                    "version": None, "exhibits": []}

        exhibits_out = []
        for ex in version["exhibits"]:
            work_id = ex["work_id"]
            if channel != "offline" and not self._published(st, channel, work_id, on_date) \
                    and viewer != VIEWER_RESTRICTED:
                continue  # 该渠道未开启公开窗口（或已撤展），旧页面不再传播
                          # 受限预展可在发布前/撤展后继续审阅

            claim_entries = []
            for cid in ex.get("claim_ids", []):
                claim = st.claims[cid]
                visible_status = claim.status
                if claim.status == CLAIM_DISPUTED:
                    if viewer != VIEWER_RESTRICTED:
                        continue  # 争议事实只能进入受限预展
                    visible_status = "disputed_restricted"
                elif claim.status != CLAIM_APPROVED and viewer != VIEWER_RESTRICTED:
                    continue  # 草稿/被驳回叙述未获审定，不得公开
                # 文字在该渠道当日须有有效授权
                if not self._right_ok(st, "claim", cid, channel, on_date):
                    if viewer != VIEWER_RESTRICTED:
                        continue
                    claim_entries.append({"claim_id": cid, "text": claim.effective_text,
                                          "status": visible_status,
                                          "rights": "missing_for_channel"})
                    continue
                claim_entries.append({"claim_id": cid, "text": claim.effective_text,
                                      "status": visible_status,
                                      "has_errata": bool(claim.errata_chain)})

            asset_entries = []
            for aid in ex.get("asset_ids", []):
                asset = st.assets[aid]
                if self._right_ok(st, "asset", aid, channel, on_date):
                    asset_entries.append({"asset_id": aid, "kind": asset["kind"],
                                          "title": asset["title"], "rights": "valid"})
                elif viewer == VIEWER_RESTRICTED:
                    asset_entries.append({"asset_id": aid, "kind": asset["kind"],
                                          "title": asset["title"],
                                          "rights": "missing_for_channel"})

            exhibits_out.append({"work_id": work_id,
                                 "title": st.works[work_id]["title"],
                                 "claims": claim_entries, "assets": asset_entries})

        return {"date": on_date, "channel": channel, "viewer": viewer,
                "version": {"version_id": version["version_id"],
                            "label": version["label"],
                            "install_date": version["install_date"]},
                "exhibits": exhibits_out}

    def restricted_preview(self, on_date: str) -> dict:
        """受限预展：含争议叙述与授权不全内容，仅供审定环节，绝不公开。"""
        return self.audience_view(on_date, channel="web", viewer=VIEWER_RESTRICTED)

    def claim_provenance(self, claim_id: str, on_date: Optional[str] = None) -> dict:
        """追溯一条叙述的完整批准依据。"""
        st = self._state()
        claim = self._get_claim(st, claim_id)

        evidence = []
        for ev in st.evidence.values():
            if ev["claim_id"] == claim_id or (
                    ev["claim_id"] is None and ev["work_id"] == claim.work_id):
                evidence.append({"evidence_id": ev["evidence_id"], "kind": ev["kind"],
                                 "detail": ev["detail"], "source": ev["source"],
                                 "date": ev["date"]})
        evidence.sort(key=lambda e: e["date"])

        rounds_out = []
        for rid in claim.decisions:
            rnd = st.rounds[rid]
            votes_out = []
            for expert_id, v in rnd.votes.items():
                excluded = (expert_id, claim.work_id) in st.conflicts
                votes_out.append({
                    "expert_id": expert_id, "name": st.experts.get(expert_id, {}).get("name", ""),
                    "vote": v["vote"], "note": v["note"], "date": v["date"],
                    "counted": not excluded,
                    "exclusion_reason": "利益冲突，按规则排除" if excluded else None,
                })
            rounds_out.append({
                "round_id": rid, "opened": rnd.opened, "closed": rnd.closed,
                "revised_text": rnd.revised_text, "decision": rnd.decision,
                "decision_date": rnd.decision_date, "votes": votes_out,
            })

        errata_chain = [st.errata[eid] for eid in claim.errata_chain]
        effective_at = None
        if on_date:
            # 截至当日重放：晚于当日发布的勘误不影响当日观众所见文本
            as_of = self._state_as_of(on_date)
            if claim_id in as_of.claims:
                effective_at = as_of.claims[claim_id].effective_text

        return {
            "claim_id": claim_id, "work_id": claim.work_id,
            "status": claim.status,
            "initial_text": claim.initial_text,
            "effective_text": claim.effective_text,
            "text_effective_at_date": effective_at,
            "dispute": claim.dispute,
            "evidence": evidence,
            "review_rounds": rounds_out,
            "errata_chain": errata_chain,
        }

    def public_errata(self) -> dict:
        """公众可见的勘误链。"""
        st = self._state()
        chain = sorted(st.errata.values(), key=lambda e: e["date"])
        return {"count": len(chain), "errata": chain}

    def snapshot(self) -> dict:
        """供调试/导出的完整状态。"""
        st = self._state()
        return {
            "works": list(st.works.values()),
            "claims": [{"claim_id": c.id, "work_id": c.work_id, "status": c.status,
                        "effective_text": c.effective_text} for c in st.claims.values()],
            "experts": list(st.experts.values()),
            "conflicts": sorted(list(st.conflicts)),
            "versions": st.versions,
            "publications": st.publications,
            "event_count": len(self.ledger.events),
        }
