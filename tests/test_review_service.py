"""审定发布服务的端到端领域规则测试。

用一个贯穿的长征雕塑展场景覆盖：
多轮会签、利益冲突排除、争议受限预展、勘误链、三渠道独立期限、
撤回隔离、展厅换版、撤展与任意日期重建及批准依据溯源。
"""

import unittest

from review.core import Ledger, ReviewService, ValidationError, VIEWER_RESTRICTED


def build_service(path=None):
    return ReviewService(Ledger(path))


def seed(svc):
    """铺好一个作品、一条叙述、三件资料、三名专家。"""
    svc.command("register_work", {"work_id": "SC-090", "title": "湘江群雕",
                                  "date": "2026-09-01"})
    svc.command("record_claim", {"work_id": "SC-090", "claim_id": "CL-7",
                                 "text": "人物原型与湘江战役相关", "date": "2026-09-02"})
    svc.command("register_asset", {"asset_id": "IMG-1", "work_id": "SC-090",
                                   "title": "雕塑正面照", "date": "2026-09-02"})
    for eid, name in [("E-A", "党史专家甲"), ("E-B", "军史专家乙"), ("E-C", "艺术史专家丙")]:
        svc.command("register_expert", {"expert_id": eid, "name": name,
                                        "date": "2026-09-02"})


class ConflictOfInterestTests(unittest.TestCase):
    def test_conflicted_expert_cannot_vote(self):
        svc = build_service()
        seed(svc)
        svc.command("declare_conflict", {"expert_id": "E-A", "work_id": "SC-090",
                                         "date": "2026-09-03"})
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-04"})
        with self.assertRaises(ValidationError):
            svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A",
                                      "vote": "approve", "date": "2026-09-04"})

    def test_late_declared_conflict_excludes_already_cast_vote(self):
        # 冲突在投票之后才声明：闭轮时该票仍须被排除，不能靠它凑够 2 票
        svc = build_service()
        seed(svc)
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-04"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "reject",
                                  "date": "2026-09-05"})
        svc.command("declare_conflict", {"expert_id": "E-A", "work_id": "SC-090",
                                         "date": "2026-09-06"})
        with self.assertRaises(ValidationError):
            svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-07"})

    def test_quorum_and_approval(self):
        svc = build_service()
        seed(svc)
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-04"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-C", "vote": "reject",
                                  "date": "2026-09-05"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-06"})
        prov = svc.claim_provenance("CL-7")
        self.assertEqual(prov["review_rounds"][0]["decision"], "approved")


class DisputeAndPreviewTests(unittest.TestCase):
    def _approved_claim(self):
        svc = build_service()
        seed(svc)
        svc.command("grant_rights", {"grant_id": "GR-CL7-WEB", "subject_type": "claim",
                                     "subject_id": "CL-7", "channel": "web",
                                     "valid_from": "2026-09-01", "date": "2026-09-03"})
        svc.command("grant_rights", {"grant_id": "GR-IMG-WEB", "subject_type": "asset",
                                     "subject_id": "IMG-1", "channel": "web",
                                     "valid_from": "2026-09-01", "date": "2026-09-03"})
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-04"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-06"})
        svc.command("create_gallery_version", {
            "version_id": "V1", "install_date": "2026-09-10",
            "exhibits": [{"work_id": "SC-090", "claim_ids": ["CL-7"],
                          "asset_ids": ["IMG-1"]}], "date": "2026-09-08"})
        svc.command("open_publication", {"channel": "web", "work_id": "SC-090",
                                         "date": "2026-09-10"})
        return svc

    def test_approved_claim_publicly_visible(self):
        svc = self._approved_claim()
        view = svc.audience_view("2026-09-11", channel="web")
        claims = view["exhibits"][0]["claims"]
        self.assertEqual([c["claim_id"] for c in claims], ["CL-7"])

    def test_disputed_claim_only_in_restricted_preview(self):
        svc = self._approved_claim()
        svc.command("dispute_claim", {"claim_id": "CL-7", "reason": "原型身份存疑",
                                      "date": "2026-09-15"})
        public = svc.audience_view("2026-09-16", channel="web")
        self.assertEqual(public["exhibits"][0]["claims"], [])
        preview = svc.restricted_preview("2026-09-16")
        claims = preview["exhibits"][0]["claims"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["status"], "disputed_restricted")

    def test_historical_view_not_polluted_by_later_dispute(self):
        # 9/15 才被标记争议，9/11 的观众视图仍应看到已审定通过的原叙述
        svc = self._approved_claim()
        svc.command("dispute_claim", {"claim_id": "CL-7", "reason": "新发现",
                                      "date": "2026-09-15"})
        view = svc.audience_view("2026-09-11", channel="web")
        self.assertEqual(view["exhibits"][0]["claims"][0]["claim_id"], "CL-7")


class ErrataChainTests(unittest.TestCase):
    def test_errata_requires_approved_round_and_forms_chain(self):
        svc = build_service()
        seed(svc)
        # 第一轮修订稿未获通过，不能据此发勘误
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "revised_text": "人物原型为红一军团战士",
                                          "date": "2026-09-04"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "reject",
                                  "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "reject",
                                  "date": "2026-09-05"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-06"})
        with self.assertRaises(ValidationError):
            svc.command("issue_errata", {"claim_id": "CL-7", "errata_id": "ER-1",
                                         "round_id": "R1", "date": "2026-09-07"})

        # 修改回应后重开第二轮，通过并发勘误
        svc.command("record_evidence", {"kind": "revision_response",
                                        "evidence_id": "EV-RESP-1", "claim_id": "CL-7",
                                        "round_id": "R1", "detail": "创作团队补充军史档案",
                                        "date": "2026-09-08"})
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R2",
                                          "revised_text": "人物原型为红一方面军战士",
                                          "date": "2026-09-09"})
        svc.command("cast_vote", {"round_id": "R2", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-10"})
        svc.command("cast_vote", {"round_id": "R2", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-10"})
        svc.command("close_review_round", {"round_id": "R2", "date": "2026-09-11"})
        svc.command("issue_errata", {"claim_id": "CL-7", "errata_id": "ER-1",
                                     "round_id": "R2", "date": "2026-09-12"})

        chain = svc.public_errata()["errata"]
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["old_text"], "人物原型与湘江战役相关")
        self.assertEqual(chain[0]["new_text"], "人物原型为红一方面军战士")

        # 勘误发布前一天的文本仍是旧文，发布当天起是新文
        self.assertEqual(svc.claim_provenance("CL-7", "2026-09-11")["text_effective_at_date"],
                         "人物原型与湘江战役相关")
        self.assertEqual(svc.claim_provenance("CL-7", "2026-09-12")["text_effective_at_date"],
                         "人物原型为红一方面军战士")

    def test_second_errata_chains_previous_new_text(self):
        svc = build_service()
        seed(svc)

        def approve_round(rid, text, d):
            svc.command("open_review_round", {"claim_id": "CL-7", "round_id": rid,
                                              "revised_text": text, "date": d})
            svc.command("cast_vote", {"round_id": rid, "expert_id": "E-A", "vote": "approve",
                                      "date": d})
            svc.command("cast_vote", {"round_id": rid, "expert_id": "E-B", "vote": "approve",
                                      "date": d})
            svc.command("close_review_round", {"round_id": rid, "date": d})

        approve_round("R1", "第一次修订", "2026-09-04")
        svc.command("issue_errata", {"claim_id": "CL-7", "errata_id": "ER-1",
                                     "round_id": "R1", "date": "2026-09-05"})
        approve_round("R2", "第二次修订", "2026-09-07")
        svc.command("issue_errata", {"claim_id": "CL-7", "errata_id": "ER-2",
                                     "round_id": "R2", "date": "2026-09-08"})
        prov = svc.claim_provenance("CL-7")
        self.assertEqual([e["errata_id"] for e in prov["errata_chain"]], ["ER-1", "ER-2"])
        self.assertEqual(prov["errata_chain"][1]["old_text"], "第一次修订")


class RightsChannelsTests(unittest.TestCase):
    def _setup(self):
        svc = build_service()
        seed(svc)
        # 叙述三渠道授权：web 限期一年；education 长期；offline 仅展期
        svc.command("grant_rights", {"grant_id": "GR-CL7-WEB", "subject_type": "claim",
                                     "subject_id": "CL-7", "channel": "web",
                                     "valid_from": "2026-09-01",
                                     "valid_until": "2027-08-31", "date": "2026-09-01"})
        svc.command("grant_rights", {"grant_id": "GR-CL7-EDU", "subject_type": "claim",
                                     "subject_id": "CL-7", "channel": "education",
                                     "valid_from": "2026-09-01", "date": "2026-09-01"})
        svc.command("grant_rights", {"grant_id": "GR-CL7-OFF", "subject_type": "claim",
                                     "subject_id": "CL-7", "channel": "offline",
                                     "valid_from": "2026-09-10",
                                     "valid_until": "2026-12-31", "date": "2026-09-01"})
        # 图像：仅 web 授权
        svc.command("grant_rights", {"grant_id": "GR-IMG-WEB", "subject_type": "asset",
                                     "subject_id": "IMG-1", "channel": "web",
                                     "valid_from": "2026-09-01", "date": "2026-09-01"})
        # 审定通过
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-02"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-03"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-03"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-04"})
        svc.command("create_gallery_version", {
            "version_id": "V1", "install_date": "2026-09-10",
            "exhibits": [{"work_id": "SC-090", "claim_ids": ["CL-7"],
                          "asset_ids": ["IMG-1"]}], "date": "2026-09-05"})
        svc.command("open_publication", {"channel": "web", "work_id": "SC-090",
                                         "date": "2026-09-10"})
        svc.command("open_publication", {"channel": "education", "work_id": "SC-090",
                                         "date": "2026-09-10"})
        return svc

    def test_channels_judged_independently(self):
        svc = self._setup()
        web = svc.audience_view("2026-09-11", channel="web")
        edu = svc.audience_view("2026-09-11", channel="education")
        offline = svc.audience_view("2026-09-11", channel="offline")
        # web：文字+图像都在
        ex = web["exhibits"][0]
        self.assertEqual(len(ex["claims"]), 1)
        self.assertEqual([a["asset_id"] for a in ex["assets"]], ["IMG-1"])
        # education：文字在，图像无该渠道授权 -> 不出现
        self.assertEqual(len(edu["exhibits"][0]["claims"]), 1)
        self.assertEqual(edu["exhibits"][0]["assets"], [])
        # offline：文字在（展期授权内），图像无授权
        self.assertEqual(len(offline["exhibits"][0]["claims"]), 1)
        self.assertEqual(offline["exhibits"][0]["assets"], [])

    def test_expiry_by_date(self):
        svc = self._setup()
        # 2027-09-01：web 文字授权已于 8-31 到期；education 仍长期有效
        web = svc.audience_view("2027-09-01", channel="web")
        self.assertEqual(web["exhibits"][0]["claims"], [])
        edu = svc.audience_view("2027-09-01", channel="education")
        self.assertEqual(len(edu["exhibits"][0]["claims"]), 1)

    def test_revoke_web_does_not_damage_education_or_gallery_snapshot(self):
        svc = self._setup()
        # 撤回图像 web 授权：web 图像消失；展厅版本 V1 快照与文字各渠道授权不受影响
        svc.command("revoke_rights", {"grant_id": "GR-IMG-WEB", "date": "2026-10-01"})
        web = svc.audience_view("2026-10-02", channel="web")
        self.assertEqual(web["version"]["version_id"], "V1")
        self.assertEqual(web["exhibits"][0]["assets"], [])
        self.assertEqual(len(web["exhibits"][0]["claims"]), 1)
        offline = svc.audience_view("2026-10-02", channel="offline")
        self.assertEqual(offline["version"]["version_id"], "V1")
        self.assertEqual(len(offline["exhibits"][0]["claims"]), 1)

        # 撤回前的历史视图里图像依然有效
        before = svc.audience_view("2026-09-20", channel="web")
        self.assertEqual([a["asset_id"] for a in before["exhibits"][0]["assets"]],
                         ["IMG-1"])


class GalleryVersionAndPublicationTests(unittest.TestCase):
    def _setup(self):
        svc = build_service()
        seed(svc)
        svc.command("grant_rights", {"grant_id": "G1", "subject_type": "claim",
                                     "subject_id": "CL-7", "channel": "web",
                                     "valid_from": "2026-09-01", "date": "2026-09-01"})
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-02"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "date": "2026-09-03"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-03"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-04"})
        return svc

    def test_version_swap_changes_audience_view_by_date(self):
        svc = self._setup()
        svc.command("create_gallery_version", {
            "version_id": "V1", "install_date": "2026-09-10",
            "exhibits": [{"work_id": "SC-090", "claim_ids": ["CL-7"], "asset_ids": []}],
            "date": "2026-09-05"})
        svc.command("open_publication", {"channel": "web", "work_id": "SC-090",
                                         "date": "2026-09-10"})
        # 换版：V2 不再陈列这件作品
        svc.command("create_gallery_version", {
            "version_id": "V2", "install_date": "2026-11-01",
            "exhibits": [], "date": "2026-10-20"})
        svc.command("supersede_gallery_version", {"version_id": "V1",
                                                  "date": "2026-11-01"})
        self.assertEqual(
            svc.audience_view("2026-10-15", channel="web")["version"]["version_id"], "V1")
        nov = svc.audience_view("2026-11-02", channel="web")
        self.assertEqual(nov["version"]["version_id"], "V2")
        self.assertEqual(nov["exhibits"], [])

    def test_takedown_stops_old_pages(self):
        svc = self._setup()
        svc.command("create_gallery_version", {
            "version_id": "V1", "install_date": "2026-09-10",
            "exhibits": [{"work_id": "SC-090", "claim_ids": ["CL-7"], "asset_ids": []}],
            "date": "2026-09-05"})
        svc.command("open_publication", {"channel": "web", "work_id": "SC-090",
                                         "date": "2026-09-10"})
        svc.command("close_publication", {"channel": "web", "work_id": "SC-090",
                                          "date": "2026-12-01"})
        # 撤展后旧页面不再公开传播
        self.assertEqual(svc.audience_view("2026-12-02", channel="web")["exhibits"], [])
        # 撤展前的任意日期仍可完整重建
        old = svc.audience_view("2026-09-11", channel="web")
        self.assertEqual(old["exhibits"][0]["claims"][0]["claim_id"], "CL-7")
        # 受限预展在撤展后仍可看到内容用于内部复核
        preview = svc.restricted_preview("2026-12-02")
        self.assertEqual(preview["exhibits"][0]["claims"][0]["claim_id"], "CL-7")


class ProvenanceTests(unittest.TestCase):
    def test_every_narrative_traces_to_approval_basis(self):
        svc = build_service()
        seed(svc)
        svc.command("record_evidence", {"kind": "archive_source", "evidence_id": "AR-1",
                                        "claim_id": "CL-7",
                                        "detail": "1934 年战役序列档案",
                                        "source": "中央档案馆复制件", "date": "2026-09-02"})
        svc.command("record_evidence", {"kind": "fieldwork", "evidence_id": "FW-1",
                                        "claim_id": "CL-7", "detail": "桂林走访口述记录",
                                        "source": "田野走访 FW-2026-014",
                                        "date": "2026-09-03"})
        svc.command("record_evidence", {"kind": "artist_statement", "evidence_id": "AS-1",
                                        "claim_id": "CL-7", "detail": "创作者阐述构思来源",
                                        "source": "创作者陈述 2026-08",
                                        "date": "2026-09-03"})
        svc.command("declare_conflict", {"expert_id": "E-C", "work_id": "SC-090",
                                         "date": "2026-09-03"})
        svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                          "date": "2026-09-04"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A", "vote": "approve",
                                  "note": "档案与口述互证", "date": "2026-09-05"})
        svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B", "vote": "approve",
                                  "date": "2026-09-05"})
        svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-06"})

        prov = svc.claim_provenance("CL-7")
        kinds = {e["kind"] for e in prov["evidence"]}
        self.assertEqual(kinds, {"archive_source", "fieldwork", "artist_statement"})
        rnd = prov["review_rounds"][0]
        self.assertEqual(rnd["decision"], "approved")
        self.assertEqual({v["expert_id"]: v["counted"] for v in rnd["votes"]},
                         {"E-A": True, "E-B": True})
        # E-C 虽未投票，溯源中也能看到其冲突声明（通过 snapshot 间接验证）
        self.assertIn(("E-C", "SC-090"), set(map(tuple, svc.snapshot()["conflicts"])))


class LedgerPersistenceTests(unittest.TestCase):
    def test_rebuild_from_disk_matches(self):
        import tempfile
        from review.core import Ledger as L, ReviewService as RS

        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/ledger.jsonl"
            svc = RS(L(path))
            seed(svc)
            svc.command("grant_rights", {"grant_id": "G1", "subject_type": "claim",
                                         "subject_id": "CL-7", "channel": "web",
                                         "valid_from": "2026-09-01", "date": "2026-09-03"})
            svc.command("open_review_round", {"claim_id": "CL-7", "round_id": "R1",
                                              "date": "2026-09-04"})
            svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-A",
                                      "vote": "approve", "date": "2026-09-05"})
            svc.command("cast_vote", {"round_id": "R1", "expert_id": "E-B",
                                      "vote": "approve", "date": "2026-09-05"})
            svc.command("close_review_round", {"round_id": "R1", "date": "2026-09-06"})
            svc.command("create_gallery_version", {
                "version_id": "V1", "install_date": "2026-09-10",
                "exhibits": [{"work_id": "SC-090", "claim_ids": ["CL-7"],
                              "asset_ids": ["IMG-1"]}], "date": "2026-09-08"})
            svc.command("open_publication", {"channel": "web", "work_id": "SC-090",
                                             "date": "2026-09-10"})

            # 全新进程：从磁盘重放，重建结果应一致
            svc2 = RS(L(path))
            v1 = svc.audience_view("2026-09-11", channel="web")
            v2 = svc2.audience_view("2026-09-11", channel="web")
            self.assertEqual(v1, v2)
            self.assertEqual(v2["exhibits"][0]["claims"][0]["text"],
                             "人物原型与湘江战役相关")


if __name__ == "__main__":
    unittest.main()
