"""A synthetic policy change can affect both approval guards through the rule pack."""

import yaml

from agent.config import REPO_ROOT
from agent.handoffs import ProposalDraft
from agent.nodes.client_liaison import check_message, load_message_rules
from agent.nodes.compliance import check_proposal, load_proposal_rules


def test_rule_pack_change_applies_to_proposal_and_message(tmp_path):
    original = REPO_ROOT / "data/policy_pack/rules.yaml"
    rule_pack = yaml.safe_load(original.read_text(encoding="utf-8"))
    rule_pack["rules"].append(
        {
            "id": "local_policy_drill",
            "severity": "block",
            "applies_to": ["proposal", "message"],
            "pattern": r"\bexternal scheduling portal\b",
            "reason": "Use the marketplace scheduling tools.",
        }
    )
    edited = tmp_path / "rules.yaml"
    edited.write_text(yaml.safe_dump(rule_pack), encoding="utf-8")
    body = (
        "I can outline the data validation steps and agree on a small discovery phase. "
        "I would use your external scheduling portal. We can discuss how often the new "
        "reports need to refresh and confirm the required formats in this workspace."
    )
    draft = ProposalDraft(job_id="policy-drill", cover_letter=body)

    assert check_proposal(draft, [], [], load_proposal_rules(original)).allowed
    assert check_message(body, [], load_message_rules(original)).allowed
    for verdict in (
        check_proposal(draft, [], [], load_proposal_rules(edited)),
        check_message(body, [], load_message_rules(edited)),
    ):
        assert not verdict.allowed
        assert verdict.reasons == ["Use the marketplace scheduling tools."]
