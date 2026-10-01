"""主题雕塑史实审定的领域核心。

覆盖多轮会签与利益冲突回避、图像与文字的分渠道权利期限、
展厅换版、受限预展、公众可见的勘误链，以及按任意日期重建
观众实际所见内容并追溯每条叙述的批准依据。

所有写操作都显式接收日期，保证历史重建可复现。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

MEDIA_KINDS = ("image", "text")
CHANNELS = ("online", "offline", "education")
PUBLIC_CHANNELS = ("online", "education")  # 线下由展厅版本直接生效
EVIDENCE_KINDS = (
    "creator_statement",  # 创作者陈述
    "field_visit",  # 田野走访
    "archive",  # 档案出处
    "expert_opinion",  # 专家意见
    "revision_response",  # 修改回应
)
DECISIONS = ("approve", "reject")


class DomainError(Exception):
    """领域规则被违反。"""


class NotFound(DomainError):
    """编号不存在。"""


class ConflictOfInterest(DomainError):
    """专家与作品存在利益冲突，不得参加对应表决。"""


class DisputedClaimError(DomainError):
    """争议事实只能进入受限预展。"""


class LicenseError(DomainError):
    """权利范围或期限不满足。"""


def _day(value) -> date:
    """把 ISO 日期字符串或 date 统一为 date。"""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass
class Work:
    id: str
    title: str
    creator: str = ""


@dataclass
class Asset:
    """图像或文字素材，权利按媒介与渠道分别授予。"""

    id: str
    media: str  # image / text


@dataclass
class Evidence:
    id: str
    work_id: str
    kind: str  # EVIDENCE_KINDS 之一
    summary: str
    source_date: str = ""


@dataclass
class ClaimRevision:
    """叙述的一次修订；旧版本不被删除，构成勘误链。"""

    number: int
    text: str
    reason: str  # 修订原因；首版为 initial
    created_at: date
    supersedes: Optional[int]
    approved: bool = False


@dataclass
class Claim:
    id: str
    work_id: str
    evidence_ids: list
    revisions: list
    status: str = "draft"  # draft / in_review / approved / disputed

    @property
    def current(self) -> ClaimRevision:
        return self.revisions[-1]

    def revision(self, number: int) -> ClaimRevision:
        for rev in self.revisions:
            if rev.number == number:
                return rev
        raise NotFound(f"主张 {self.id} 没有版本 {number}")


@dataclass
class Expert:
    id: str
    name: str
    conflicts: set  # 存在利益冲突的作品编号


@dataclass
class Vote:
    expert_id: str
    decision: str  # approve / reject
    cast_at: date


@dataclass
class SignoffRound:
    """一轮会签，钉住主张的一个修订版本。"""

    id: str
    claim_id: str
    revision: int
    quorum: int
    opened_at: date
    closed_at: Optional[date] = None
    result: Optional[str] = None  # approved / disputed
    votes: list = field(default_factory=list)


@dataclass
class License:
    """某项素材在某一渠道的使用权利期限。"""

    id: str
    asset_id: str
    media: str  # image / text
    channel: str  # online / offline / education
    valid_from: date
    valid_to: date
    revoked_at: Optional[date] = None

    def covers(self, day: date) -> bool:
        if not (self.valid_from <= day <= self.valid_to):
            return False
        if self.revoked_at is not None and day >= self.revoked_at:
            return False
        return True


@dataclass
class VersionItem:
    claim_id: str
    revision: int
    asset_ids: list


@dataclass
class GalleryVersion:
    """展厅空间版本；换版时旧版本保留生效区间。"""

    id: str
    items: list
    created_at: date
    effective_from: date
    effective_to: Optional[date] = None


@dataclass
class Release:
    """正式发布：把某个展厅版本投放到线上或教育渠道。"""

    id: str
    channel: str
    version_id: str
    published_at: date


@dataclass
class RestrictedPreview:
    """受限预展：争议事实唯一可以出现的地方。"""

    id: str
    claim_ids: list
    opens_at: date
    closes_at: date


class ReviewService:
    """审定发布服务：会签、权利、版本与重建。"""

    def __init__(self):
        self.works = {}
        self.assets = {}
        self.evidence = {}
        self.claims = {}
        self.experts = {}
        self.rounds = {}
        self.licenses = {}
        self.versions = {}
        self.previews = {}
        self.releases = []
        self._seq = itertools.count(1)

    # -- 登记 -------------------------------------------------------------

    def register_work(self, id, title, creator=""):
        if id in self.works:
            raise DomainError(f"作品 {id} 已存在")
        work = Work(id, title, creator)
        self.works[id] = work
        return work

    def register_asset(self, id, media):
        if media not in MEDIA_KINDS:
            raise DomainError(f"未知媒介类型 {media}")
        if id in self.assets:
            raise DomainError(f"素材 {id} 已存在")
        asset = Asset(id, media)
        self.assets[id] = asset
        return asset

    def register_evidence(self, id, work_id, kind, summary, source_date=""):
        self._work(work_id)
        if kind not in EVIDENCE_KINDS:
            raise DomainError(f"未知证据类型 {kind}")
        if id in self.evidence:
            raise DomainError(f"证据 {id} 已存在")
        ev = Evidence(id, work_id, kind, summary, source_date)
        self.evidence[id] = ev
        return ev

    def register_expert(self, id, name, conflicts=()):
        if id in self.experts:
            raise DomainError(f"专家 {id} 已存在")
        expert = Expert(id, name, set(conflicts))
        self.experts[id] = expert
        return expert

    def register_claim(self, id, work_id, text, evidence_ids, at):
        self._work(work_id)
        if id in self.claims:
            raise DomainError(f"主张 {id} 已存在")
        for ev_id in evidence_ids:
            ev = self._evidence(ev_id)
            if ev.work_id != work_id:
                raise DomainError(f"证据 {ev_id} 不属于作品 {work_id}")
        claim = Claim(
            id=id,
            work_id=work_id,
            evidence_ids=list(evidence_ids),
            revisions=[ClaimRevision(1, text, "initial", _day(at), None)],
        )
        self.claims[id] = claim
        return claim

    # -- 修订与勘误链 ------------------------------------------------------

    def revise_claim(self, claim_id, text, reason, at):
        """修订公开说明；旧版本保留在公众可见的勘误链中。"""
        claim = self._claim(claim_id)
        rev = ClaimRevision(
            number=len(claim.revisions) + 1,
            text=text,
            reason=reason,
            created_at=_day(at),
            supersedes=claim.current.number,
        )
        claim.revisions.append(rev)
        if claim.status == "approved":
            claim.status = "in_review"  # 修订后需重新会签
        return rev

    def errata_chain(self, claim_id):
        """公众可见的勘误链：每一次修订都留痕。"""
        claim = self._claim(claim_id)
        return [
            {
                "revision": rev.number,
                "text": rev.text,
                "reason": rev.reason,
                "created_at": rev.created_at.isoformat(),
                "supersedes": rev.supersedes,
                "approved": rev.approved,
            }
            for rev in claim.revisions
        ]

    # -- 会签 --------------------------------------------------------------

    def open_signoff(self, claim_id, at, quorum=None):
        """开启一轮会签；法定人数默认为无冲突专家的过半数。"""
        claim = self._claim(claim_id)
        if claim.current.approved:
            raise DomainError(f"主张 {claim_id} 当前版本已审定")
        for rnd in self.rounds.values():
            if rnd.claim_id == claim_id and rnd.closed_at is None:
                raise DomainError(f"主张 {claim_id} 已有进行中的会签")
        eligible = self._eligible_experts(claim.work_id)
        if quorum is None:
            quorum = len(eligible) // 2 + 1
        rnd = SignoffRound(
            id=self._next_id("SR"),
            claim_id=claim_id,
            revision=claim.current.number,
            quorum=quorum,
            opened_at=_day(at),
        )
        self.rounds[rnd.id] = rnd
        claim.status = "in_review"
        return rnd

    def cast_vote(self, round_id, expert_id, decision, at):
        rnd = self._round(round_id)
        if rnd.closed_at is not None:
            raise DomainError(f"会签 {round_id} 已结束")
        if decision not in DECISIONS:
            raise DomainError(f"未知表决意见 {decision}")
        expert = self._expert(expert_id)
        claim = self._claim(rnd.claim_id)
        if claim.work_id in expert.conflicts:
            raise ConflictOfInterest(
                f"专家 {expert_id} 与作品 {claim.work_id} 存在利益冲突，不得参加表决"
            )
        if any(v.expert_id == expert_id for v in rnd.votes):
            raise DomainError(f"专家 {expert_id} 已在本轮表决")
        vote = Vote(expert_id, decision, _day(at))
        rnd.votes.append(vote)
        return vote

    def close_signoff(self, round_id, at):
        """结束会签：无反对且赞成达到法定人数则审定通过，否则转入争议。"""
        rnd = self._round(round_id)
        if rnd.closed_at is not None:
            raise DomainError(f"会签 {round_id} 已结束")
        claim = self._claim(rnd.claim_id)
        rev = claim.revision(rnd.revision)
        approves = sum(1 for v in rnd.votes if v.decision == "approve")
        rejects = sum(1 for v in rnd.votes if v.decision == "reject")
        rnd.closed_at = _day(at)
        if rejects == 0 and approves >= rnd.quorum:
            rnd.result = "approved"
            rev.approved = True
            claim.status = "approved"
        else:
            rnd.result = "disputed"
            claim.status = "disputed"
        return rnd

    # -- 权利 --------------------------------------------------------------

    def grant_license(self, asset_id, media, channel, valid_from, valid_to, id=None):
        """按媒介与渠道分别授予使用期限。"""
        asset = self._asset(asset_id)
        if media != asset.media:
            raise LicenseError(f"素材 {asset_id} 的媒介是 {asset.media}，不能授予 {media} 权利")
        if channel not in CHANNELS:
            raise DomainError(f"未知渠道 {channel}")
        start, end = _day(valid_from), _day(valid_to)
        if start > end:
            raise LicenseError("权利期限起止颠倒")
        lic = License(id or self._next_id("LIC"), asset_id, media, channel, start, end)
        self.licenses[lic.id] = lic
        return lic

    def revoke_license(self, license_id, at):
        """撤回某项权利；其他渠道与媒介仍然有效的权利不受影响。"""
        lic = self._license(license_id)
        if lic.revoked_at is not None:
            raise DomainError(f"权利 {license_id} 已撤回")
        lic.revoked_at = _day(at)
        return lic

    # -- 版本、预展与发布 ---------------------------------------------------

    def create_version(self, items, effective_from, at, id=None):
        """展厅换版：只能钉选已审定的叙述版本，并核对线下权利。"""
        effective_from = _day(effective_from)
        parsed = []
        for item in items:
            claim = self._claim(item["claim_id"])
            rev = claim.revision(item["revision"])
            if not rev.approved:
                raise DisputedClaimError(
                    f"主张 {claim.id} 第 {rev.number} 版未获审定，只能进入受限预展"
                )
            asset_ids = list(item.get("asset_ids", []))
            for asset_id in asset_ids:
                self._asset(asset_id)
                if not self._covered(asset_id, "offline", effective_from):
                    raise LicenseError(f"素材 {asset_id} 在 {effective_from} 缺少线下权利")
            parsed.append(VersionItem(claim.id, rev.number, asset_ids))
        current = self._current_version()
        if current is not None:
            if effective_from < current.effective_from:
                raise DomainError("换版生效日期早于现行版本")
            current.effective_to = effective_from
        version = GalleryVersion(
            id=id or self._next_id("GV"),
            items=parsed,
            created_at=_day(at),
            effective_from=effective_from,
        )
        self.versions[version.id] = version
        return version

    def open_preview(self, claim_ids, opens_at, closes_at, id=None):
        """受限预展：争议事实只能在这里出现。"""
        for claim_id in claim_ids:
            self._claim(claim_id)
        preview = RestrictedPreview(
            id=id or self._next_id("PV"),
            claim_ids=list(claim_ids),
            opens_at=_day(opens_at),
            closes_at=_day(closes_at),
        )
        if preview.opens_at > preview.closes_at:
            raise DomainError("预展期限起止颠倒")
        self.previews[preview.id] = preview
        return preview

    def publish_release(self, channel, version_id, at, id=None):
        """正式发布到线上或教育渠道，逐项核对权利期限。"""
        if channel not in PUBLIC_CHANNELS:
            raise DomainError("正式发布渠道须为 online 或 education；线下由展厅版本直接生效")
        version = self._version(version_id)
        day = _day(at)
        for item in version.items:
            for asset_id in item.asset_ids:
                if not self._covered(asset_id, channel, day):
                    raise LicenseError(f"素材 {asset_id} 在 {day} 缺少 {channel} 渠道权利")
        release = Release(id or self._next_id("REL"), channel, version_id, day)
        self.releases.append(release)
        return release

    # -- 重建与追溯 ---------------------------------------------------------

    def reconstruct(self, channel, day):
        """重建指定日期、指定渠道下观众实际看到的内容。"""
        day = _day(day)
        if channel == "offline":
            version = self._version_at(day)
            if version is None:
                return {"channel": channel, "date": day.isoformat(), "source": None, "items": []}
            return self._render_version(version, channel, day, {"version": version.id})
        if channel in PUBLIC_CHANNELS:
            release = self._release_at(channel, day)
            if release is None:
                return {"channel": channel, "date": day.isoformat(), "source": None, "items": []}
            version = self.versions[release.version_id]
            return self._render_version(
                version, channel, day, {"release": release.id, "version": version.id}
            )
        if channel == "restricted_preview":
            active = [p for p in self.previews.values() if p.opens_at <= day <= p.closes_at]
            items = []
            for preview in active:
                for claim_id in preview.claim_ids:
                    items.append(self._render_preview_item(self._claim(claim_id), day))
            return {
                "channel": channel,
                "date": day.isoformat(),
                "source": {"previews": [p.id for p in active]},
                "items": items,
            }
        raise DomainError(f"未知渠道 {channel}")

    def provenance(self, claim_id):
        """每条叙述的批准依据：证据、会签轮次与表决记录。"""
        claim = self._claim(claim_id)
        rounds = [
            {
                "id": rnd.id,
                "revision": rnd.revision,
                "quorum": rnd.quorum,
                "result": rnd.result,
                "opened_at": rnd.opened_at.isoformat(),
                "closed_at": rnd.closed_at.isoformat() if rnd.closed_at else None,
                "votes": [
                    {
                        "expert_id": v.expert_id,
                        "decision": v.decision,
                        "cast_at": v.cast_at.isoformat(),
                    }
                    for v in rnd.votes
                ],
            }
            for rnd in self.rounds.values()
            if rnd.claim_id == claim_id
        ]
        return {
            "claim_id": claim.id,
            "work_id": claim.work_id,
            "status": claim.status,
            "evidence_ids": list(claim.evidence_ids),
            "rounds": rounds,
            "errata": self.errata_chain(claim_id),
        }

    # -- 内部 ----------------------------------------------------------------

    def _render_version(self, version, channel, day, source):
        items = []
        for entry in version.items:
            claim = self._claim(entry.claim_id)
            rev = claim.revision(entry.revision)
            assets = []
            for asset_id in entry.asset_ids:
                lic = self._covering_license(asset_id, channel, day)
                if lic is not None:
                    assets.append(
                        {
                            "asset_id": asset_id,
                            "media": self.assets[asset_id].media,
                            "license_id": lic.id,
                        }
                    )
            items.append(
                {
                    "claim_id": claim.id,
                    "revision": rev.number,
                    "text": rev.text,
                    "status": "approved",
                    "approval": self._approval_basis(claim, rev.number),
                    "assets": assets,
                    "errata": self._errata_after(claim, rev.number, day),
                }
            )
        return {"channel": channel, "date": day.isoformat(), "source": source, "items": items}

    def _render_preview_item(self, claim, day):
        shown = claim.revisions[0]
        for rev in claim.revisions:
            if rev.created_at <= day:
                shown = rev
        return {
            "claim_id": claim.id,
            "revision": shown.number,
            "text": shown.text,
            "status": claim.status,
            "restricted": True,
            "approval": self._approval_basis(claim, shown.number),
            "assets": [],
            "errata": self._errata_after(claim, shown.number, day),
        }

    def _approval_basis(self, claim, revision_number):
        for rnd in self.rounds.values():
            if (
                rnd.claim_id == claim.id
                and rnd.revision == revision_number
                and rnd.result == "approved"
            ):
                return {
                    "round": rnd.id,
                    "quorum": rnd.quorum,
                    "closed_at": rnd.closed_at.isoformat(),
                    "votes": [
                        {
                            "expert_id": v.expert_id,
                            "decision": v.decision,
                            "cast_at": v.cast_at.isoformat(),
                        }
                        for v in rnd.votes
                    ],
                    "evidence_ids": list(claim.evidence_ids),
                }
        return None

    def _errata_after(self, claim, revision_number, day):
        return [
            {
                "revision": rev.number,
                "reason": rev.reason,
                "created_at": rev.created_at.isoformat(),
            }
            for rev in claim.revisions
            if rev.number > revision_number and rev.created_at <= day
        ]

    def _next_id(self, prefix):
        return f"{prefix}-{next(self._seq)}"

    def _work(self, work_id):
        if work_id not in self.works:
            raise NotFound(f"作品 {work_id} 不存在")
        return self.works[work_id]

    def _asset(self, asset_id):
        if asset_id not in self.assets:
            raise NotFound(f"素材 {asset_id} 不存在")
        return self.assets[asset_id]

    def _evidence(self, evidence_id):
        if evidence_id not in self.evidence:
            raise NotFound(f"证据 {evidence_id} 不存在")
        return self.evidence[evidence_id]

    def _claim(self, claim_id):
        if claim_id not in self.claims:
            raise NotFound(f"主张 {claim_id} 不存在")
        return self.claims[claim_id]

    def _expert(self, expert_id):
        if expert_id not in self.experts:
            raise NotFound(f"专家 {expert_id} 不存在")
        return self.experts[expert_id]

    def _round(self, round_id):
        if round_id not in self.rounds:
            raise NotFound(f"会签 {round_id} 不存在")
        return self.rounds[round_id]

    def _license(self, license_id):
        if license_id not in self.licenses:
            raise NotFound(f"权利 {license_id} 不存在")
        return self.licenses[license_id]

    def _version(self, version_id):
        if version_id not in self.versions:
            raise NotFound(f"展厅版本 {version_id} 不存在")
        return self.versions[version_id]

    def _eligible_experts(self, work_id):
        return [e for e in self.experts.values() if work_id not in e.conflicts]

    def _covered(self, asset_id, channel, day):
        return self._covering_license(asset_id, channel, day) is not None

    def _covering_license(self, asset_id, channel, day):
        for lic in self.licenses.values():
            if lic.asset_id == asset_id and lic.channel == channel and lic.covers(day):
                return lic
        return None

    def _current_version(self):
        current = None
        for version in self.versions.values():
            if version.effective_to is None:
                if current is not None:
                    raise DomainError("存在多个现行展厅版本")
                current = version
        return current

    def _version_at(self, day):
        for version in self.versions.values():
            if version.effective_from <= day and (
                version.effective_to is None or day < version.effective_to
            ):
                return version
        return None

    def _release_at(self, channel, day):
        chosen = None
        for release in self.releases:
            if release.channel == channel and release.published_at <= day:
                if chosen is None or release.published_at > chosen.published_at:
                    chosen = release
        return chosen
