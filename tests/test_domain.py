"""领域规则核对：会签、利益冲突、权利期限、换版、勘误与重建。"""

import unittest

from domain import (
    ConflictOfInterest,
    DisputedClaimError,
    LicenseError,
    ReviewService,
)


def build_service():
    svc = ReviewService()
    svc.register_work("SC-CHANGZHENG-090", "湘江记忆", creator="某创作者")
    svc.register_evidence(
        "EV-1", "SC-CHANGZHENG-090", "creator_statement",
        "创作者陈述：人物原型来自湘江战役幸存者口述", "2026-03-01",
    )
    svc.register_evidence(
        "EV-2", "SC-CHANGZHENG-090", "archive",
        "档案出处：湘江战役相关名册节选", "2026-03-15",
    )
    svc.register_evidence(
        "EV-3", "SC-CHANGZHENG-090", "field_visit",
        "田野走访：界首渡口现场记录", "2026-04-02",
    )
    svc.register_claim(
        "CL-7", "SC-CHANGZHENG-090", "人物原型与湘江战役相关",
        ["EV-1", "EV-2"], at="2026-05-01",
    )
    svc.register_claim(
        "CL-8", "SC-CHANGZHENG-090", "某团长姓名待考",
        ["EV-3"], at="2026-05-01",
    )
    svc.register_expert("E-1", "专家一")
    svc.register_expert("E-2", "专家二", conflicts=["SC-CHANGZHENG-090"])
    svc.register_expert("E-3", "专家三")
    svc.register_asset("IMG-1", "image")
    svc.register_asset("TXT-1", "text")
    return svc


def approve(svc, claim_id, at, quorum=2):
    """一轮无反对、达到法定人数的会签。"""
    rnd = svc.open_signoff(claim_id, at=at, quorum=quorum)
    svc.cast_vote(rnd.id, "E-1", "approve", at=at)
    svc.cast_vote(rnd.id, "E-3", "approve", at=at)
    return svc.close_signoff(rnd.id, at=at)


def grant_offline_text(svc):
    svc.grant_license("TXT-1", "text", "offline", "2026-09-01", "2027-08-31", id="LIC-TXT-OFF")


class SignoffTest(unittest.TestCase):
    def test_conflicted_expert_cannot_vote(self):
        svc = build_service()
        rnd = svc.open_signoff("CL-7", at="2026-05-02", quorum=2)
        with self.assertRaises(ConflictOfInterest):
            svc.cast_vote(rnd.id, "E-2", "approve", at="2026-05-02")

    def test_conflicted_experts_excluded_from_default_quorum(self):
        svc = build_service()
        rnd = svc.open_signoff("CL-7", at="2026-05-02")
        # 三位专家中一位有冲突，法定人数按两位无冲突专家计算
        self.assertEqual(rnd.quorum, 2)

    def test_disputed_claim_only_enters_restricted_preview(self):
        svc = build_service()
        rnd = svc.open_signoff("CL-8", at="2026-05-02", quorum=2)
        svc.cast_vote(rnd.id, "E-1", "reject", at="2026-05-02")
        svc.cast_vote(rnd.id, "E-3", "approve", at="2026-05-02")
        svc.close_signoff(rnd.id, at="2026-05-03")
        self.assertEqual(svc.claims["CL-8"].status, "disputed")

        with self.assertRaises(DisputedClaimError):
            svc.create_version(
                [{"claim_id": "CL-8", "revision": 1, "asset_ids": []}],
                effective_from="2026-10-01", at="2026-09-20",
            )

        svc.open_preview(["CL-8"], opens_at="2026-09-25", closes_at="2026-10-05")
        view = svc.reconstruct("restricted_preview", "2026-10-01")
        self.assertEqual(view["items"][0]["text"], "某团长姓名待考")
        self.assertEqual(view["items"][0]["status"], "disputed")
        # 争议事实不出现在公开渠道
        public = svc.reconstruct("offline", "2026-10-01")
        self.assertEqual(public["items"], [])

    def test_multi_round_signoff_after_revision_response(self):
        svc = build_service()
        rnd = svc.open_signoff("CL-8", at="2026-05-02", quorum=2)
        svc.cast_vote(rnd.id, "E-1", "reject", at="2026-05-02")
        svc.close_signoff(rnd.id, at="2026-05-03")
        self.assertEqual(svc.claims["CL-8"].status, "disputed")

        svc.register_evidence(
            "EV-4", "SC-CHANGZHENG-090", "revision_response",
            "修改回应：补充地方志办公室核对函", "2026-05-20",
        )
        svc.revise_claim("CL-8", "某团长姓名已核定", "修改回应：补充核对函", at="2026-06-01")
        approve(svc, "CL-8", at="2026-06-05")
        self.assertEqual(svc.claims["CL-8"].status, "approved")

        grant_offline_text(svc)
        version = svc.create_version(
            [{"claim_id": "CL-8", "revision": 2, "asset_ids": ["TXT-1"]}],
            effective_from="2026-10-01", at="2026-09-20",
        )
        self.assertEqual(version.items[0].revision, 2)
        prov = svc.provenance("CL-8")
        self.assertEqual([r["result"] for r in prov["rounds"]], ["disputed", "approved"])


class ErrataTest(unittest.TestCase):
    def test_errata_chain_public_and_gallery_rehang(self):
        svc = build_service()
        approve(svc, "CL-7", at="2026-05-02")
        grant_offline_text(svc)
        svc.create_version(
            [{"claim_id": "CL-7", "revision": 1, "asset_ids": ["TXT-1"]}],
            effective_from="2026-10-01", at="2026-09-20", id="GV-1",
        )

        svc.revise_claim(
            "CL-7", "人物原型为湘江战役红三十四师战士",
            "档案馆补充名册佐证，表述收紧", at="2026-10-20",
        )
        chain = svc.errata_chain("CL-7")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["text"], "人物原型与湘江战役相关")  # 旧文本公众可见
        self.assertEqual(chain[1]["supersedes"], 1)

        # 修订版未重新会签前不能换版
        with self.assertRaises(DisputedClaimError):
            svc.create_version(
                [{"claim_id": "CL-7", "revision": 2, "asset_ids": ["TXT-1"]}],
                effective_from="2026-11-01", at="2026-10-30",
            )
        approve(svc, "CL-7", at="2026-10-25")
        svc.create_version(
            [{"claim_id": "CL-7", "revision": 2, "asset_ids": ["TXT-1"]}],
            effective_from="2026-11-01", at="2026-10-30", id="GV-2",
        )

        before = svc.reconstruct("offline", "2026-10-25")
        self.assertEqual(before["source"], {"version": "GV-1"})
        self.assertEqual(before["items"][0]["text"], "人物原型与湘江战役相关")
        self.assertEqual(
            before["items"][0]["errata"][0]["reason"], "档案馆补充名册佐证，表述收紧"
        )
        after = svc.reconstruct("offline", "2026-11-05")
        self.assertEqual(after["source"], {"version": "GV-2"})
        self.assertEqual(after["items"][0]["text"], "人物原型为湘江战役红三十四师战士")


class LicenseTest(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        approve(self.svc, "CL-7", at="2026-05-02")
        svc = self.svc
        svc.grant_license("IMG-1", "image", "offline", "2026-09-01", "2027-08-31", id="LIC-IMG-OFF")
        svc.grant_license("IMG-1", "image", "online", "2026-09-01", "2026-12-31", id="LIC-IMG-ON")
        svc.grant_license("TXT-1", "text", "offline", "2026-09-01", "2027-08-31", id="LIC-TXT-OFF")
        svc.grant_license("TXT-1", "text", "online", "2026-09-01", "2027-08-31", id="LIC-TXT-ON")
        svc.create_version(
            [{"claim_id": "CL-7", "revision": 1, "asset_ids": ["IMG-1", "TXT-1"]}],
            effective_from="2026-10-01", at="2026-09-20", id="GV-1",
        )
        svc.publish_release("online", "GV-1", at="2026-10-05")

    def test_media_and_channel_windows_checked_separately(self):
        # IMG-1 没有教育渠道权利，正式发布到教育渠道被拒绝
        with self.assertRaises(LicenseError):
            self.svc.publish_release("education", "GV-1", at="2026-10-06")
        self.svc.grant_license(
            "IMG-1", "image", "education", "2026-09-01", "2027-08-31", id="LIC-IMG-EDU"
        )
        self.svc.grant_license(
            "TXT-1", "text", "education", "2026-09-01", "2027-08-31", id="LIC-TXT-EDU"
        )
        release = self.svc.publish_release("education", "GV-1", at="2026-10-06")
        self.assertEqual(release.channel, "education")

    def test_media_mismatch_rejected(self):
        with self.assertRaises(LicenseError):
            self.svc.grant_license("IMG-1", "text", "offline", "2026-09-01", "2027-08-31")

    def test_revoke_online_keeps_offline_intact(self):
        svc = self.svc
        svc.revoke_license("LIC-IMG-ON", at="2026-11-10")

        online = svc.reconstruct("online", "2026-11-15")
        online_assets = [a["asset_id"] for a in online["items"][0]["assets"]]
        self.assertNotIn("IMG-1", online_assets)
        self.assertIn("TXT-1", online_assets)

        # 撤回线上权利不损坏仍然有效的展厅版本
        offline = svc.reconstruct("offline", "2026-11-15")
        offline_assets = sorted(a["asset_id"] for a in offline["items"][0]["assets"])
        self.assertEqual(offline_assets, ["IMG-1", "TXT-1"])

    def test_expired_window_drops_asset(self):
        # IMG-1 线上权利 2026-12-31 到期
        online = self.svc.reconstruct("online", "2027-01-15")
        online_assets = [a["asset_id"] for a in online["items"][0]["assets"]]
        self.assertNotIn("IMG-1", online_assets)
        self.assertIn("TXT-1", online_assets)

    def test_no_release_no_online_content(self):
        view = self.svc.reconstruct("online", "2026-10-04")  # 发布前一天
        self.assertEqual(view["items"], [])


class ReconstructTest(unittest.TestCase):
    def test_approval_basis_traceable(self):
        svc = build_service()
        rnd = approve(svc, "CL-7", at="2026-05-02")
        grant_offline_text(svc)
        svc.create_version(
            [{"claim_id": "CL-7", "revision": 1, "asset_ids": ["TXT-1"]}],
            effective_from="2026-10-01", at="2026-09-20",
        )
        view = svc.reconstruct("offline", "2026-10-10")
        item = view["items"][0]
        self.assertEqual(item["approval"]["round"], rnd.id)
        self.assertEqual(
            {v["expert_id"] for v in item["approval"]["votes"]}, {"E-1", "E-3"}
        )
        self.assertEqual(item["approval"]["evidence_ids"], ["EV-1", "EV-2"])

        prov = svc.provenance("CL-7")
        self.assertEqual(prov["rounds"][0]["result"], "approved")
        self.assertEqual(prov["work_id"], "SC-CHANGZHENG-090")


if __name__ == "__main__":
    unittest.main()
